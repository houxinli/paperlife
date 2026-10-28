"""分层整理、待确认问题与可撤销长辈入口。"""

from typing import Literal
from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

from .materials import ConflictError


class ItemInput(BaseModel):
    kind: Literal['fact', 'person', 'event', 'story', 'question']
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(default='', max_length=30000)
    answer: str = Field(default='', max_length=10000)
    status: Literal['draft', 'confirmed', 'open', 'resolved', 'archived'] = 'draft'
    source_ids: list[str] = Field(default_factory=list, max_length=30)
    time_text: str = Field(default='', max_length=200)
    people: str = Field(default='', max_length=500)
    place: str = Field(default='', max_length=300)
    certainty: Literal['unverified', 'approximate', 'confirmed'] = 'unverified'
    version: int = Field(default=0, ge=0)


def install_content(router, store, current_user, access):
    def validate_sources(profile, ids):
        if not store.owns_sources(profile, ids):
            raise HTTPException(422, '来源必须属于当前人物档案')

    @router.post('/profiles/{ident}/links')
    def create_link(ident: str, user=Depends(current_user)):
        access.profile(ident, user, owner=True)
        return store.create_link(ident)

    @router.get('/profiles/{ident}/links')
    def links(ident: str, user=Depends(current_user)):
        access.profile(ident, user, owner=True)
        return store.links(ident)

    @router.delete('/profiles/{ident}/links/{link_id}')
    def revoke_link(ident: str, link_id: int, user=Depends(current_user)):
        access.profile(ident, user, owner=True)
        store.revoke_link(ident, link_id)
        return {'ok': True}

    @router.get('/profiles/{ident}/sources')
    def sources(ident: str, user=Depends(current_user)):
        access.profile(ident, user)
        return store.sources(ident)

    @router.get('/profiles/{ident}/items')
    def items(ident: str, user=Depends(current_user)):
        member = access.profile(ident, user)
        # reader 只看已确认内容与问题，看不到 AI 草稿。
        return store.items(ident, None if member['role'] != 'reader' else ('confirmed', 'open', 'resolved'))

    @router.post('/profiles/{ident}/items')
    def add_item(ident: str, data: ItemInput, user=Depends(current_user)):
        access.profile(ident, user, edit=True)
        validate_sources(ident, data.source_ids)
        return store.add_item(ident, data.model_dump())

    @router.put('/items/{ident}')
    def edit_item(ident: str, data: ItemInput, user=Depends(current_user)):
        item = store.item(ident)
        if not item:
            raise HTTPException(404, '条目不存在')
        access.profile(item['profile'], user, edit=True)
        validate_sources(item['profile'], data.source_ids)
        if any((e.get('source_id') or e.get('session_id')) not in data.source_ids for e in item.get('evidence', [])):
            raise HTTPException(422, '不能移除自动草稿引用的原始采访')
        if item['kind'] != data.kind:
            raise HTTPException(422, '条目类型不可更换，请另建条目保留层级')
        try:
            return store.edit_item(ident, data.model_dump(), user.id)
        except ConflictError as error:
            raise HTTPException(409, str(error)) from None

    @router.get('/items/{ident}/revisions')
    def item_revisions(ident: str, user=Depends(current_user)):
        item = store.item(ident)
        if not item:
            raise HTTPException(404, '条目不存在')
        member = access.profile(item['profile'], user)
        if member['role'] == 'reader':
            raise HTTPException(404, '条目尚未确认')
        return store.item_revisions(ident)

    @router.get('/profiles/{ident}/processing')
    def processing_status(ident: str, user=Depends(current_user)):
        access.profile(ident, user, edit=True)
        return store.processing(ident)

    @router.post('/sessions/{ident}/process')
    def start_processing(ident: str, user=Depends(current_user)):
        from .editor_jobs import enqueue_session, retry

        session = store.session(ident)
        if not session:
            raise HTTPException(404, '采访不存在')
        access.profile(session['profile'], user, edit=True)
        if session['status'] not in ('ended', 'interrupted'):
            raise HTTPException(409, '请先结束采访')
        job = enqueue_session(store, ident)
        if not job:
            raise HTTPException(422, '没有可整理的讲述转写')
        if job['status'] == 'failed':
            return retry(store, job['id'])
        return job
