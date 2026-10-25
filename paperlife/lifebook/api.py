"""人生档案、服务配置、实时采访与校对接口。"""
import asyncio
import json
import secrets
import sqlite3
import time
import uuid
import hashlib
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, Header
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..private_route import PrivateRoute
from .store import Store


class ProfileInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    context: str = Field(default='', max_length=30000)
    voice_dialect: Literal['sichuan','mandarin'] | None = None


class ProviderInput(BaseModel):
    app_id: str = Field(default='', max_length=100, pattern=r'^[a-zA-Z0-9_-]*$')
    access_token: str = Field(default='', max_length=2000)
    api_key: str = Field(default='', max_length=2000)


class TopicInput(BaseModel):
    topic: str = Field(default='随便聊聊今天的小事', min_length=1, max_length=200)
    transport: Literal['web','wechat'] = 'web'


class EditInput(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    edited_text: str = Field(max_length=100000)
    version: int = Field(ge=0)


class MemberInput(BaseModel):
    role: Literal['editor', 'reader']


def build_router(database_path: Path, current_user, account_authorized=None):
    router = APIRouter(prefix='/life', tags=['life'], route_class=PrivateRoute)
    store = Store(Path(database_path).parent / 'life')
    tickets = {}
    active = set()

    def display_names(p):
        values={entry['key']:entry['value'] for entry in store.preferences(p['id'])}
        return {'interviewer_name':values.get('interviewer_name','拾光'),
                'narrator_name':values.get('narrator_name',p['name'])}

    def profile_access(ident, user, edit=False, owner=False):
        p = store.profile(ident, user.id)
        if not p:
            raise HTTPException(404, '档案不存在或未获授权')
        if (owner and p['role'] != 'owner') or (edit and p['role'] == 'reader'):
            raise HTTPException(403, '没有修改权限')
        return p

    def session_access(ident, user, edit=False):
        s = store.session(ident)
        if not s:
            raise HTTPException(404, '聊天记录不存在')
        profile_access(s['profile'], user, edit=edit)
        return s

    def configured():
        p = store.provider()
        return bool(p.get('api_key') or (p.get('app_id') and p.get('access_token')))

    @router.get('/provider')
    def provider(user=Depends(current_user)):
        return {'configured':configured(), 'can_configure':user.role=='admin', 'model':'豆包端到端语音 O2.0'}

    @router.put('/provider')
    def configure(data: ProviderInput, user=Depends(current_user)):
        if user.role != 'admin':
            raise HTTPException(403, '仅管理员可以配置语音服务')
        if not (data.api_key.strip() or (data.app_id and data.access_token.strip())):
            raise HTTPException(422, '请填写 API Key 或 App ID 和 Access Token')
        if any(c in data.access_token + data.api_key for c in '\r\n'):
            raise HTTPException(422, '凭据格式无效')
        store.configure({k:v.strip() for k,v in data.model_dump().items()})
        return {'configured':True}

    @router.get('/profiles')
    def profiles(user=Depends(current_user)):
        return store.profiles(user.id)

    @router.post('/profiles')
    def create_profile(data: ProfileInput, user=Depends(current_user)):
        return store.create_profile(user.id, data.name, data.context,data.voice_dialect or 'sichuan')

    @router.get('/profiles/{ident}')
    def get_profile(ident: str, user=Depends(current_user)):
        p=profile_access(ident,user)
        return {**p,**display_names(p)}

    @router.put('/profiles/{ident}')
    def update_profile(ident: str, data: ProfileInput, user=Depends(current_user)):
        profile_access(ident, user, edit=True)
        with store.db() as db:
            db.execute('UPDATE profiles SET name=?,context=?,voice_dialect=COALESCE(?,voice_dialect) WHERE id=?', (data.name,data.context,data.voice_dialect,ident))
            if data.context and not db.execute('SELECT 1 FROM sources WHERE profile=?',(ident,)).fetchone():
                db.execute('INSERT INTO sources(id,profile,title,text) VALUES(?,?,?,?)',(uuid.uuid4().hex,ident,'首次导入的原始访谈',data.context))
        return profile_access(ident, user)

    @router.get('/profiles/{ident}/members')
    def members(ident: str, user=Depends(current_user)):
        profile_access(ident,user,owner=True)
        with store.db() as db:
            return [dict(r) for r in db.execute('SELECT user,role FROM members WHERE profile=?', (ident,))]

    @router.put('/profiles/{ident}/members/{member}')
    def grant(ident: str, member: int, data: MemberInput, user=Depends(current_user)):
        p = profile_access(ident,user,owner=True)
        if member <= 0 or member == p['owner']:
            raise HTTPException(422, '不能修改档案所有者')
        with sqlite3.connect(database_path) as accounts:
            if not accounts.execute('SELECT 1 FROM users WHERE id=? AND is_active=1', (member,)).fetchone():
                raise HTTPException(404, '该账号不存在或已停用')
        with store.db() as db:
            db.execute('INSERT INTO members VALUES(?,?,?) ON CONFLICT(profile,user) DO UPDATE SET role=excluded.role', (ident,member,data.role))
        return {'ok':True}

    @router.delete('/profiles/{ident}/members/{member}')
    def revoke(ident: str, member: int, user=Depends(current_user)):
        p = profile_access(ident,user,owner=True)
        if member == p['owner']:
            raise HTTPException(422, '不能移除档案所有者')
        with store.db() as db:
            db.execute('DELETE FROM members WHERE profile=? AND user=?', (ident,member))
        return {'ok':True}

    @router.get('/profiles/{ident}/sessions')
    def sessions(ident: str, user=Depends(current_user)):
        profile_access(ident,user)
        with store.db() as db:
            return [dict(r) for r in db.execute('SELECT id,title,status,created_at,version FROM sessions WHERE profile=? ORDER BY created_at DESC', (ident,))]

    @router.get('/profiles/{ident}/preferences')
    def preferences(ident: str, user=Depends(current_user)):
        profile_access(ident,user)
        return store.preferences(ident)

    @router.delete('/profiles/{ident}/preferences/{preference}')
    def revoke_preference(ident: str, preference: str, user=Depends(current_user)):
        profile_access(ident,user,edit=True)
        if not store.revoke_preference(ident,preference):
            raise HTTPException(404,'这条聊天习惯不存在或已撤销')
        return {'ok':True}

    def issue_ticket(ident, uid, topic, link_id=None, auth_digest=None, transport='web'):
        if not configured():
            raise HTTPException(503, '请管理员先配置豆包实时语音服务')
        for token, entry in list(tickets.items()):
            if entry['expires'] < time.monotonic():
                tickets.pop(token, None)
        if uid in active or any(t['user']==uid for t in tickets.values()):
            raise HTTPException(409, '已有聊天正在连接或进行中，请结束或等候一分钟')
        session = store.create_session(ident,uid,topic)
        ticket = secrets.token_urlsafe(32)
        tickets[ticket] = {'user':uid,'session':session['id'],'link_id':link_id,'auth_digest':auth_digest,'transport':transport,'expires':time.monotonic()+60}
        return {**session,'ticket':ticket}

    @router.post('/profiles/{ident}/sessions')
    def start(ident: str, data: TopicInput, request:Request, user=Depends(current_user)):
        profile_access(ident,user,edit=True)
        header=request.headers.get('authorization','')
        token=header[7:] if header.startswith('Bearer ') else request.cookies.get('paperlife_session','')
        return issue_ticket(ident,user.id,data.topic,auth_digest=hashlib.sha256(token.encode()).hexdigest(),transport=data.transport)

    def guest_access(x_life_link: str = Header(default='')):
        link=store.link(token=x_life_link) if len(x_life_link)<=200 else None
        if not link or (account_authorized and not account_authorized(link['profile_data']['owner'])):
            raise HTTPException(401,'专属链接已失效，请联系家人重新发送')
        return link

    @router.get('/guest')
    def guest(link=Depends(guest_access)):
        p=link['profile_data']
        return {'id':p['id'],'name':p['name'],'role':'narrator','configured':configured(),**display_names(p)}

    @router.get('/guest/preferences')
    def guest_preferences(link=Depends(guest_access)):
        return store.preferences(link['profile'])

    @router.delete('/guest/preferences/{preference}')
    def guest_revoke_preference(preference: str, link=Depends(guest_access)):
        if not store.revoke_preference(link['profile'],preference):
            raise HTTPException(404,'这条聊天习惯不存在或已撤销')
        return {'ok':True}

    @router.post('/guest/sessions')
    def guest_start(data:TopicInput,link=Depends(guest_access)):
        uid=-link['id']
        with store.db() as db:
            count=db.execute("SELECT count(*) FROM sessions WHERE user=? AND created_at>datetime('now','-1 day')",(uid,)).fetchone()[0]
        if count>=20:raise HTTPException(429,'今天已聊了很多次，请明天继续，或联系家人')
        return issue_ticket(link['profile'],uid,data.topic,link['id'],transport=data.transport)

    @router.get('/sessions/{ident}')
    def session(ident: str, user=Depends(current_user)):
        return {**session_access(ident,user),'turns':store.turns(ident)}

    @router.get('/sessions/{ident}/quality')
    def quality(ident: str, user=Depends(current_user)):
        session_access(ident,user)
        with store.db() as db:
            return {'flags':[dict(r) for r in db.execute('SELECT turn,code,reason,ask_now,seconds FROM quality_flags WHERE session=?',(ident,))], 'metrics':[dict(r) for r in db.execute('SELECT kind,value_ms FROM voice_metrics WHERE session=?',(ident,))]}

    @router.get('/sessions/{ident}/revisions')
    def revisions(ident: str, user=Depends(current_user)):
        session_access(ident,user)
        with store.db() as db:
            return [dict(r) for r in db.execute('SELECT version,text,created_at FROM revisions WHERE session=? ORDER BY version DESC',(ident,))]

    @router.put('/sessions/{ident}')
    def edit_session(ident: str, data: EditInput, user=Depends(current_user)):
        s = session_access(ident,user,edit=True)
        from .materials import revise, ConflictError, MaterialError
        from .editor_jobs import enqueue_session
        try:
            revise(store,s['profile'],'session',ident,data.edited_text,data.version,user.id,title=data.title)
        except ConflictError as error:
            raise HTTPException(409,str(error)) from None
        except MaterialError as error:
            raise HTTPException(422,str(error)) from None
        enqueue_session(store,ident)
        return session(ident,user)

    @router.get('/sessions/{ident}/audio')
    def audio(ident: str, track: Literal['user','assistant']='user', user=Depends(current_user)):
        session_access(ident,user)
        path = store.audio_path(ident,track)
        if not path.exists():
            raise HTTPException(404, '该聊天还没有可回听的音频')
        return FileResponse(path, media_type='audio/wav', headers={'Cache-Control':'private, no-store'})

    @router.websocket('/live')
    async def live(ws: WebSocket):
        origin = ws.headers.get('origin')
        same_origin = bool(origin and urlparse(origin).netloc == ws.headers.get('host'))
        native_origin = not origin or origin == 'https://servicewechat.com'
        if not same_origin and not native_origin:
            await ws.close(code=1008)
            return
        await ws.accept()
        uid = None
        claimed = False
        try:
            first = await asyncio.wait_for(ws.receive_text(), 10)
            if len(first)>2048:
                raise ValueError('Invalid ticket')
            ticket = tickets.pop(json.loads(first).get('ticket',''),None)
            if not ticket or ticket['expires']<time.monotonic():
                raise ValueError('Expired ticket')
            # 原生客户端没有浏览器同源环境；授权仍由服务器签发的一次性票据决定。
            # transport只来自已鉴权的REST签发步骤，不相信WS首包的自报标记。
            if not same_origin and ticket.get('transport') != 'wechat':
                raise ValueError('Origin denied')
            uid = ticket['user']
            s = store.session(ticket['session'])
            link_id=ticket.get('link_id')
            link=store.link(link_id=link_id) if link_id else None
            p=link['profile_data'] if link else (store.profile(s['profile'],uid) if not link_id else None)
            if not p or p.get('role')=='reader' or p['id']!=s['profile'] or uid in active:
                raise ValueError('Access denied')
            active.add(uid)
            claimed = True
            from .realtime import interview
            def authorize():
                if link_id:
                    current=store.link(link_id=link_id)
                    return bool(current and (not account_authorized or account_authorized(current['profile_data']['owner'])))
                current=store.profile(s['profile'],uid)
                return bool(current and current['role']!='reader' and (not account_authorized or account_authorized(uid,ticket.get('auth_digest'))))
            if not authorize():raise ValueError('Access revoked')
            await interview(ws,store,s,p,authorize=authorize)
        except Exception:
            try:
                await ws.send_json({'type':'error','message':'连接未完成或已中断。请检查服务配置后重试；已收到的内容会保留。'})
            except Exception:
                pass
        finally:
            if claimed:
                active.discard(uid)
            try:
                await ws.close()
            except Exception:
                pass

    from .content import install_content
    install_content(router,store,current_user,profile_access)
    from .narrator import install_narrator
    install_narrator(router,store,current_user,profile_access,guest_access)
    from .chapter_api import install_chapters
    install_chapters(router,store,current_user,profile_access)
    return router
