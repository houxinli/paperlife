"""旁听受权入口；复用实时会话一次性票据。"""
import hashlib
from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, Field, StrictBool
from .materials import MaterialError, ConflictError
from . import listening_store


class Participant(BaseModel):
    id: str = Field(min_length=1,max_length=80,pattern=r'^[A-Za-z0-9_-]+$')
    name: str = Field(min_length=1,max_length=80)
    relationship: str = Field(default='',max_length=100)
    is_subject: StrictBool = False


class Start(BaseModel):
    title: str = Field(default='旁听通话',min_length=1,max_length=200)
    participants: list[Participant] = Field(default_factory=list,max_length=30)
    consent: StrictBool


class Edit(BaseModel):
    version: int = Field(ge=0)
    participants: list[Participant] = Field(max_length=30)
    bindings: dict = Field(max_length=200)
    overrides: dict = Field(max_length=20000)


class Configuration(BaseModel):
    enabled: StrictBool


def install_listening(router,store,current_user,profile_access,session_access,guest_access,issue_ticket):
    from .listening_asr import public_config, save_config

    def translate(fn, *args, **kwargs):
        try: return fn(*args,**kwargs)
        except ConflictError as error: raise HTTPException(409,str(error)) from None
        except MaterialError as error: raise HTTPException(422,str(error)) from None

    @router.get('/listening/provider')
    def provider(user=Depends(current_user)):
        return public_config(store,user.role=='admin')

    @router.get('/guest/listening/provider')
    def guest_provider(link=Depends(guest_access)):
        return public_config(store,False)

    @router.put('/listening/provider')
    def configure(data:Configuration,user=Depends(current_user)):
        if user.role!='admin': raise HTTPException(403,'仅管理员可以启用旁听识别')
        save_config(store,data.model_dump())
        return public_config(store,True)

    def create(profile,uid,data,**auth):
        if not data.consent: raise HTTPException(422,'请先确认在场与通话双方均知情同意录音')
        participants=translate(listening_store.validate_participants,[p.model_dump() for p in data.participants])
        return issue_ticket(profile,uid,data.title,mode='listening',participants=participants,**auth)

    @router.post('/profiles/{ident}/listening')
    def start(ident:str,data:Start,request:Request,user=Depends(current_user)):
        profile_access(ident,user,edit=True)
        header=request.headers.get('authorization','')
        token=header[7:] if header.startswith('Bearer ') else request.cookies.get('paperlife_session','')
        return create(ident,user.id,data,auth_digest=hashlib.sha256(token.encode()).hexdigest())

    @router.post('/guest/listening')
    def guest_start(data:Start,link=Depends(guest_access)):
        uid=-link['id']
        with store.db() as db:
            count=db.execute("SELECT count(*) FROM sessions WHERE user=? AND created_at>datetime('now','-1 day')",(uid,)).fetchone()[0]
        if count>=20: raise HTTPException(429,'今天已录过很多次，请明天继续，或联系家人')
        return create(link['profile'],uid,data,link_id=link['id'])

    def check(ident,user,edit=False):
        s=session_access(ident,user,edit=edit)
        if s['mode']!='listening': raise HTTPException(404,'旁听记录不存在')
        return s

    @router.get('/listening/sessions/{ident}')
    def get(ident:str,user=Depends(current_user)):
        s=check(ident,user); p=profile_access(s['profile'],user)
        return translate(listening_store.detail,store,ident,p['role']!='reader')

    @router.get('/listening/sessions/{ident}/versions/{version}')
    def get_version(ident:str,version:int,user=Depends(current_user)):
        check(ident,user)
        return translate(listening_store.detail,store,ident,False,version)

    @router.put('/listening/sessions/{ident}')
    def edit(ident:str,data:Edit,user=Depends(current_user)):
        check(ident,user,True)
        result=translate(listening_store.annotate,store,ident,data.version,[p.model_dump() for p in data.participants],data.bindings,data.overrides,user.id)
        from .editor_jobs import enqueue_session
        enqueue_session(store,ident)
        return result

    @router.get('/guest/listening/{ident}')
    def guest_get(ident:str,link=Depends(guest_access)):
        s=store.session(ident)
        if not s or s['profile']!=link['profile'] or s['mode']!='listening': raise HTTPException(404,'旁听记录不存在或未获授权')
        return translate(listening_store.detail,store,ident,False)
