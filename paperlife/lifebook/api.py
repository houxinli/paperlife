"""人生档案、服务配置、实时采访与校对接口。"""

import asyncio
import json
import logging
import secrets
import time
import hashlib
from typing import Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..private_route import PrivateRoute
from .access import Access

log = logging.getLogger(__name__)


class ProfileInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    context: str = Field(default='', max_length=30000)
    voice_dialect: Literal['sichuan', 'mandarin'] | None = None


class ProviderInput(BaseModel):
    app_id: str = Field(default='', max_length=100, pattern=r'^[a-zA-Z0-9_-]*$')
    access_token: str = Field(default='', max_length=2000)
    api_key: str = Field(default='', max_length=2000)


class TopicInput(BaseModel):
    topic: str = Field(default='随便聊聊今天的小事', min_length=1, max_length=200)
    transport: Literal['web', 'wechat'] = 'web'


class EditInput(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    edited_text: str = Field(max_length=100000)
    version: int = Field(ge=0)


class MemberInput(BaseModel):
    role: Literal['editor', 'reader']


def build_router(store, current_user, account_active, account_authorized=None):
    """store 由应用创建并共享；account_active(uid) 判断账号存在且启用，lifebook 不直接读账号库。"""
    router = APIRouter(prefix='/life', tags=['life'], route_class=PrivateRoute)
    access = Access(store, account_authorized)
    # 票据与活跃会话只在本进程内存中，部署必须保持单 worker（见 docs/architecture.md）。
    tickets = {}
    active = set()

    def display_names(p):
        values = {entry['key']: entry['value'] for entry in store.preferences(p['id'])}
        return {
            'interviewer_name': values.get('interviewer_name', '拾光'),
            'narrator_name': values.get('narrator_name', p['name']),
        }

    def configured():
        p = store.provider()
        return bool(p.get('api_key') or (p.get('app_id') and p.get('access_token')))

    @router.get('/provider')
    def provider(user=Depends(current_user)):
        return {'configured': configured(), 'can_configure': user.role == 'admin', 'model': '豆包端到端语音 O2.0'}

    @router.put('/provider')
    def configure(data: ProviderInput, user=Depends(current_user)):
        if user.role != 'admin':
            raise HTTPException(403, '仅管理员可以配置语音服务')
        if not (data.api_key.strip() or (data.app_id and data.access_token.strip())):
            raise HTTPException(422, '请填写 API Key 或 App ID 和 Access Token')
        if any(c in data.access_token + data.api_key for c in '\r\n'):
            raise HTTPException(422, '凭据格式无效')
        store.configure({k: v.strip() for k, v in data.model_dump().items()})
        return {'configured': True}

    @router.get('/profiles')
    def profiles(user=Depends(current_user)):
        return store.profiles(user.id)

    @router.post('/profiles')
    def create_profile(data: ProfileInput, user=Depends(current_user)):
        return store.create_profile(user.id, data.name, data.context, data.voice_dialect or 'sichuan')

    @router.get('/profiles/{ident}')
    def get_profile(ident: str, user=Depends(current_user)):
        p = access.profile(ident, user)
        return {**p, **display_names(p)}

    @router.put('/profiles/{ident}')
    def update_profile(ident: str, data: ProfileInput, user=Depends(current_user)):
        access.profile(ident, user, edit=True)
        store.update_profile(ident, data.name, data.context, data.voice_dialect)
        return access.profile(ident, user)

    @router.get('/profiles/{ident}/members')
    def members(ident: str, user=Depends(current_user)):
        access.profile(ident, user, owner=True)
        return store.members(ident)

    @router.put('/profiles/{ident}/members/{member}')
    def grant(ident: str, member: int, data: MemberInput, user=Depends(current_user)):
        p = access.profile(ident, user, owner=True)
        if member <= 0 or member == p['owner']:
            raise HTTPException(422, '不能修改档案所有者')
        if not account_active(member):
            raise HTTPException(404, '该账号不存在或已停用')
        store.set_member(ident, member, data.role)
        return {'ok': True}

    @router.delete('/profiles/{ident}/members/{member}')
    def revoke(ident: str, member: int, user=Depends(current_user)):
        p = access.profile(ident, user, owner=True)
        if member == p['owner']:
            raise HTTPException(422, '不能移除档案所有者')
        store.remove_member(ident, member)
        return {'ok': True}

    @router.get('/profiles/{ident}/sessions')
    def sessions(ident: str, user=Depends(current_user)):
        access.profile(ident, user)
        return store.sessions(ident)

    @router.get('/profiles/{ident}/preferences')
    def preferences(ident: str, user=Depends(current_user)):
        access.profile(ident, user)
        return store.preferences(ident)

    @router.delete('/profiles/{ident}/preferences/{preference}')
    def revoke_preference(ident: str, preference: str, user=Depends(current_user)):
        access.profile(ident, user, edit=True)
        if not store.revoke_preference(ident, preference):
            raise HTTPException(404, '这条聊天习惯不存在或已撤销')
        return {'ok': True}

    def issue_ticket(
        ident, uid, topic, link_id=None, auth_digest=None, transport='web', mode='dialogue', participants=None
    ):
        if mode == 'listening':
            from .listening_asr import public_config

            configuration = public_config(store)
            if not configuration['enabled'] or not configuration['configured']:
                raise HTTPException(503, '请管理员先配置并启用旁听识别服务')
        elif not configured():
            raise HTTPException(503, '请管理员先配置豆包实时语音服务')
        for token, entry in list(tickets.items()):
            if entry['expires'] < time.monotonic():
                tickets.pop(token, None)
        if uid in active or any(t['user'] == uid for t in tickets.values()):
            raise HTTPException(409, '已有聊天正在连接或进行中，请结束或等候一分钟')
        session = store.create_session(ident, uid, topic)
        if mode == 'listening':
            from .listening_store import initialize

            initialize(store, session['id'], participants or [])
            session = store.session(session['id'])
        ticket = secrets.token_urlsafe(32)
        tickets[ticket] = {
            'user': uid,
            'session': session['id'],
            'link_id': link_id,
            'auth_digest': auth_digest,
            'transport': transport,
            'expires': time.monotonic() + 60,
        }
        return {**session, 'ticket': ticket}

    @router.post('/profiles/{ident}/sessions')
    def start(ident: str, data: TopicInput, request: Request, user=Depends(current_user)):
        access.profile(ident, user, edit=True)
        header = request.headers.get('authorization', '')
        token = header[7:] if header.startswith('Bearer ') else request.cookies.get('paperlife_session', '')
        return issue_ticket(
            ident, user.id, data.topic, auth_digest=hashlib.sha256(token.encode()).hexdigest(), transport=data.transport
        )

    @router.get('/guest')
    def guest(link=Depends(access.guest)):
        p = link['profile_data']
        return {'id': p['id'], 'name': p['name'], 'role': 'narrator', 'configured': configured(), **display_names(p)}

    @router.get('/guest/preferences')
    def guest_preferences(link=Depends(access.guest)):
        return store.preferences(link['profile'])

    @router.delete('/guest/preferences/{preference}')
    def guest_revoke_preference(preference: str, link=Depends(access.guest)):
        if not store.revoke_preference(link['profile'], preference):
            raise HTTPException(404, '这条聊天习惯不存在或已撤销')
        return {'ok': True}

    @router.post('/guest/sessions')
    def guest_start(data: TopicInput, link=Depends(access.guest)):
        uid = -link['id']
        if store.sessions_last_day(uid) >= 20:
            raise HTTPException(429, '今天已聊了很多次，请明天继续，或联系家人')
        return issue_ticket(link['profile'], uid, data.topic, link['id'], transport=data.transport)

    @router.get('/sessions/{ident}')
    def session(ident: str, user=Depends(current_user)):
        return {**access.session(ident, user), 'turns': store.turns(ident)}

    @router.get('/sessions/{ident}/quality')
    def quality(ident: str, user=Depends(current_user)):
        access.session(ident, user)
        return store.quality(ident)

    @router.get('/sessions/{ident}/revisions')
    def revisions(ident: str, user=Depends(current_user)):
        access.session(ident, user)
        return store.revisions(ident)

    @router.put('/sessions/{ident}')
    def edit_session(ident: str, data: EditInput, user=Depends(current_user)):
        s = access.session(ident, user, edit=True)
        from .materials import revise, ConflictError, MaterialError
        from .editor_jobs import enqueue_session

        try:
            revise(store, s['profile'], 'session', ident, data.edited_text, data.version, user.id, title=data.title)
        except ConflictError as error:
            raise HTTPException(409, str(error)) from None
        except MaterialError as error:
            raise HTTPException(422, str(error)) from None
        enqueue_session(store, ident)
        return session(ident, user)

    @router.get('/sessions/{ident}/audio')
    def audio(ident: str, track: Literal['user', 'assistant'] = 'user', user=Depends(current_user)):
        access.session(ident, user)
        path = store.audio_path(ident, track)
        if not path.exists():
            raise HTTPException(404, '该聊天还没有可回听的音频')
        return FileResponse(path, media_type='audio/wav', headers={'Cache-Control': 'private, no-store'})

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
        ticket = None
        claimed = False
        try:
            first = await asyncio.wait_for(ws.receive_text(), 10)
            if len(first) > 2048:
                raise ValueError('Invalid ticket')
            ticket = tickets.pop(json.loads(first).get('ticket', ''), None)
            if not ticket or ticket['expires'] < time.monotonic():
                raise ValueError('Expired ticket')
            # 原生客户端没有浏览器同源环境；授权仍由服务器签发的一次性票据决定。
            # transport只来自已鉴权的REST签发步骤，不相信WS首包的自报标记。
            if not same_origin and ticket.get('transport') != 'wechat':
                raise ValueError('Origin denied')
            uid = ticket['user']
            s = store.session(ticket['session'])
            link_id = ticket.get('link_id')
            link = store.link(link_id=link_id) if link_id else None
            p = link['profile_data'] if link else (store.profile(s['profile'], uid) if not link_id else None)
            if not p or p.get('role') == 'reader' or p['id'] != s['profile'] or uid in active:
                raise ValueError('Access denied')
            active.add(uid)
            claimed = True
            if s.get('mode') == 'listening':
                from .listening_stream import listen as interview
            else:
                from .realtime import interview

            def authorize():
                if link_id:
                    current = store.link(link_id=link_id)
                    return bool(current and access.owner_active(current))
                current = store.profile(s['profile'], uid)
                return bool(
                    current
                    and current['role'] != 'reader'
                    and (not account_authorized or account_authorized(uid, ticket.get('auth_digest')))
                )

            if not authorize():
                raise ValueError('Access revoked')
            await interview(ws, store, s, p, authorize=authorize)
        except Exception as error:
            # 只记录异常类型与会话编号，不记录原文、票据或凭据。
            log.warning(
                'live connection ended with %s (session=%s)',
                type(error).__name__,
                (ticket or {}).get('session'),
            )
            try:
                await ws.send_json(
                    {'type': 'error', 'message': '连接未完成或已中断。请检查服务配置后重试；已收到的内容会保留。'}
                )
            except Exception:
                pass
        finally:
            if claimed:
                active.discard(uid)
            try:
                await ws.close()
            except Exception:
                pass

    from .listening_api import install_listening

    install_listening(router, store, current_user, access, issue_ticket)
    from .content import install_content

    install_content(router, store, current_user, access)
    from .narrator import install_narrator

    install_narrator(router, store, current_user, access)
    from .chapter_api import install_chapters

    install_chapters(router, store, current_user, access)
    return router
