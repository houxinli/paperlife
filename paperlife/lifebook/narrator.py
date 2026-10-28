"""讲述者的只读历史与人生之书；专属入口的档案范围来自服务端授权。"""

from typing import Literal
from fastapi import Depends, HTTPException, Query
from fastapi.responses import FileResponse


def install_narrator(router, store, current_user, access):
    def book(profile):
        from .chapters import published

        items = []
        for item in store.items(profile, ('confirmed',), ('fact', 'person', 'event', 'story')):
            item.pop('source_snapshot', None)
            item.pop('generation_metadata', None)
            items.append(item)
        allowed = ('id', 'chapter', 'number', 'title', 'body', 'evidence', 'status', 'confirmed_at', 'stale')
        return {
            'items': items,
            'pending_count': store.pending_count(profile),
            'chapters': [{k: v[k] for k in allowed} for v in published(store, profile)],
        }

    def owned_session(ident, profile):
        session = store.session(ident)
        if not session or session['profile'] != profile:
            raise HTTPException(404, '聊天记录不存在或未获授权')
        return session

    @router.get('/guest/history')
    def guest_history(
        offset: int = Query(default=0, ge=0, le=100000),
        limit: int = Query(default=20, ge=1, le=50),
        link=Depends(access.guest),
    ):
        return store.history(link['profile'], offset, limit)

    @router.get('/guest/history/{ident}')
    def guest_session(ident: str, link=Depends(access.guest)):
        session = owned_session(ident, link['profile'])
        return {k: session[k] for k in ('id', 'title', 'status', 'created_at', 'edited_text', 'version', 'mode')} | {
            'turns': store.turns(ident)
        }

    @router.get('/guest/history/{ident}/audio')
    def guest_audio(ident: str, track: Literal['user', 'assistant'] = 'user', link=Depends(access.guest)):
        owned_session(ident, link['profile'])
        path = store.audio_path(ident, track)
        if not path.exists():
            raise HTTPException(404, '这次聊天还没有可回听的音频')
        return FileResponse(path, media_type='audio/wav', headers={'Cache-Control': 'private, no-store'})

    @router.get('/guest/book')
    def guest_book(link=Depends(access.guest)):
        return book(link['profile'])

    @router.get('/profiles/{ident}/history')
    def member_history(
        ident: str,
        offset: int = Query(default=0, ge=0, le=100000),
        limit: int = Query(default=20, ge=1, le=50),
        user=Depends(current_user),
    ):
        access.profile(ident, user)
        return store.history(ident, offset, limit)

    @router.get('/profiles/{ident}/book')
    def member_book(ident: str, user=Depends(current_user)):
        access.profile(ident, user)
        return book(ident)
