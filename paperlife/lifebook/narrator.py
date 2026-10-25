"""讲述者的只读历史与人生之书；专属入口的档案范围来自服务端授权。"""
from typing import Literal
from fastapi import Depends,HTTPException,Query
from fastapi.responses import FileResponse


def install_narrator(router,store,current_user,profile_access,guest_access):
    def history(profile,offset,limit):
        with store.db() as db:
            rows=[dict(r) for r in db.execute('SELECT id,title,status,created_at,version FROM sessions WHERE profile=? ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?', (profile,limit+1,offset))]
        return {'sessions':rows[:limit],'has_more':len(rows)>limit}

    def book(profile):
        with store.db() as db:
            ids=[r['id'] for r in db.execute("SELECT id FROM items WHERE profile=? AND status='confirmed' AND kind IN ('fact','person','event','story') ORDER BY created_at,id",(profile,))]
            pending=db.execute("SELECT count(*) FROM items WHERE profile=? AND status='draft'",(profile,)).fetchone()[0]
        from .chapters import published
        with store.db() as db:
            pending += db.execute("SELECT count(*) FROM chapter_versions v JOIN chapters c ON c.id=v.chapter WHERE c.profile=? AND v.status='draft'",(profile,)).fetchone()[0]
        items=[]
        for ident in ids:
            item=store.item(ident)
            item.pop('source_snapshot',None);item.pop('generation_metadata',None)
            items.append(item)
        allowed=('id','chapter','number','title','body','evidence','status','confirmed_at','stale')
        return {'items':items,'pending_count':pending,'chapters':[{k:v[k] for k in allowed} for v in published(store,profile)]}

    def owned_session(ident,profile):
        session=store.session(ident)
        if not session or session['profile']!=profile:
            raise HTTPException(404,'聊天记录不存在或未获授权')
        return session

    @router.get('/guest/history')
    def guest_history(offset:int=Query(default=0,ge=0,le=100000),limit:int=Query(default=20,ge=1,le=50),link=Depends(guest_access)):
        return history(link['profile'],offset,limit)

    @router.get('/guest/history/{ident}')
    def guest_session(ident:str,link=Depends(guest_access)):
        session=owned_session(ident,link['profile'])
        return {k:session[k] for k in ('id','title','status','created_at','edited_text','version')}|{'turns':store.turns(ident)}

    @router.get('/guest/history/{ident}/audio')
    def guest_audio(ident:str,track:Literal['user','assistant']='user',link=Depends(guest_access)):
        owned_session(ident,link['profile']);path=store.audio_path(ident,track)
        if not path.exists():raise HTTPException(404,'这次聊天还没有可回听的音频')
        return FileResponse(path,media_type='audio/wav',headers={'Cache-Control':'private, no-store'})

    @router.get('/guest/book')
    def guest_book(link=Depends(guest_access)):
        return book(link['profile'])

    @router.get('/profiles/{ident}/history')
    def member_history(ident:str,offset:int=Query(default=0,ge=0,le=100000),limit:int=Query(default=20,ge=1,le=50),user=Depends(current_user)):
        profile_access(ident,user)
        return history(ident,offset,limit)

    @router.get('/profiles/{ident}/book')
    def member_book(ident:str,user=Depends(current_user)):
        profile_access(ident,user)
        return book(ident)
