"""分层整理、待确认问题与可撤销长辈入口。"""
import json
from typing import Literal
from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field


class ItemInput(BaseModel):
    kind: Literal['fact','person','event','story','question']
    title: str = Field(min_length=1,max_length=200)
    body: str = Field(default='',max_length=30000)
    answer: str = Field(default='',max_length=10000)
    status: Literal['draft','confirmed','open','resolved','archived'] = 'draft'
    source_ids: list[str] = Field(default_factory=list,max_length=30)
    time_text: str = Field(default='',max_length=200)
    people: str = Field(default='',max_length=500)
    place: str = Field(default='',max_length=300)
    certainty: Literal['unverified','approximate','confirmed'] = 'unverified'
    version: int = Field(default=0,ge=0)


def install_content(router,store,current_user,profile_access):
    def validate_sources(profile,ids):
        with store.db() as db:
            for ident in ids:
                if not db.execute('SELECT 1 FROM sources WHERE id=? AND profile=? UNION SELECT 1 FROM sessions WHERE id=? AND profile=?',(ident,profile,ident,profile)).fetchone():
                    raise HTTPException(422,'来源必须属于当前人物档案')

    @router.post('/profiles/{ident}/links')
    def create_link(ident:str,user=Depends(current_user)):
        profile_access(ident,user,owner=True)
        return store.create_link(ident)

    @router.get('/profiles/{ident}/links')
    def links(ident:str,user=Depends(current_user)):
        profile_access(ident,user,owner=True)
        with store.db() as db:
            return [dict(r) for r in db.execute('SELECT id,expires,revoked,created_at FROM narrator_links WHERE profile=? ORDER BY id DESC',(ident,))]

    @router.delete('/profiles/{ident}/links/{link_id}')
    def revoke_link(ident:str,link_id:int,user=Depends(current_user)):
        profile_access(ident,user,owner=True)
        with store.db() as db:
            db.execute('UPDATE narrator_links SET revoked=1 WHERE id=? AND profile=?',(link_id,ident))
        return {'ok':True}

    @router.get('/profiles/{ident}/sources')
    def sources(ident:str,user=Depends(current_user)):
        profile_access(ident,user)
        with store.db() as db:
            return [dict(r) for r in db.execute('SELECT * FROM sources WHERE profile=? ORDER BY created_at',(ident,))]

    @router.get('/profiles/{ident}/items')
    def items(ident:str,user=Depends(current_user)):
        access=profile_access(ident,user)
        with store.db() as db:
            ids=[r['id'] for r in db.execute('SELECT id FROM items WHERE profile=? ORDER BY created_at,id',(ident,))]
        result=[store.item(i) for i in ids]
        return [i for i in result if access['role']!='reader' or i['status'] in ('confirmed','open','resolved')]

    @router.post('/profiles/{ident}/items')
    def add_item(ident:str,data:ItemInput,user=Depends(current_user)):
        profile_access(ident,user,edit=True)
        validate_sources(ident,data.source_ids)
        return store.add_item(ident,data.model_dump())

    @router.put('/items/{ident}')
    def edit_item(ident:str,data:ItemInput,user=Depends(current_user)):
        item=store.item(ident)
        if not item:raise HTTPException(404,'条目不存在')
        profile_access(item['profile'],user,edit=True)
        validate_sources(item['profile'],data.source_ids)
        if any((e.get('source_id') or e.get('session_id')) not in data.source_ids for e in item.get('evidence',[])):
            raise HTTPException(422,'不能移除自动草稿引用的原始采访')
        if item['kind']!=data.kind:raise HTTPException(422,'条目类型不可更换，请另建条目保留层级')
        d=data.model_dump()
        with store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            from .materials import item_stale
            if item['status']=='draft' and data.status in ('confirmed','open','resolved') and item_stale(store,item,db):
                raise HTTPException(409,'素材已经校订，请使用新整理的条目；旧草稿可保留或归档')
            updated=db.execute('UPDATE items SET title=?,body=?,answer=?,status=?,source_ids=?,time_text=?,people=?,place=?,certainty=?,version=version+1 WHERE id=? AND version=?',
                               (data.title,data.body,data.answer,data.status,json.dumps(data.source_ids),data.time_text,data.people,data.place,data.certainty,ident,data.version))
            if not updated.rowcount:raise HTTPException(409,'条目已有新版本，请重新打开')
            db.execute('INSERT INTO item_revisions(item,version,snapshot,editor) VALUES(?,?,?,?)',(ident,item['version'],json.dumps(item,ensure_ascii=False),user.id))
        return store.item(ident)

    @router.get('/items/{ident}/revisions')
    def item_revisions(ident:str,user=Depends(current_user)):
        item=store.item(ident)
        if not item:raise HTTPException(404,'条目不存在')
        access=profile_access(item['profile'],user)
        if access['role']=='reader':
            raise HTTPException(404,'条目尚未确认')
        with store.db() as db:
            return [{**dict(r),'snapshot':json.loads(r['snapshot'])} for r in db.execute('SELECT version,snapshot,created_at FROM item_revisions WHERE item=? ORDER BY version DESC',(ident,))]

    @router.get('/profiles/{ident}/processing')
    def processing_status(ident:str,user=Depends(current_user)):
        profile_access(ident,user,edit=True)
        with store.db() as db:
            old=[dict(r) for r in db.execute('SELECT session,status,attempts,progress,total,error,created_at,completed_at FROM processing_jobs WHERE profile=? ORDER BY created_at DESC',(ident,))]
            new=[dict(r) for r in db.execute("SELECT json_extract(snapshot,'$.sources[0].id') session,status,attempts,CASE WHEN status='completed' THEN 1 ELSE 0 END progress,1 total,error,created_at,completed_at FROM writing_jobs WHERE profile=? AND kind='material' ORDER BY created_at DESC",(ident,))]
            return sorted(old+new,key=lambda r:r['created_at'],reverse=True)

    @router.post('/sessions/{ident}/process')
    def start_processing(ident:str,user=Depends(current_user)):
        from .editor_jobs import enqueue_session, retry
        session=store.session(ident)
        if not session:raise HTTPException(404,'采访不存在')
        profile_access(session['profile'],user,edit=True)
        if session['status'] not in ('ended','interrupted'):
            raise HTTPException(409,'请先结束采访')
        job=enqueue_session(store,ident)
        if not job:raise HTTPException(422,'没有可整理的讲述转写')
        if job['status']=='failed':
            return retry(store,job['id'])
        return job
