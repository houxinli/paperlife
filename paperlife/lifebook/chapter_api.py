"""章节与素材 HTTP 授权边界；模型配置仅管理员写入。"""
from typing import Literal
from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field, ConfigDict

from . import chapters, materials, editor_jobs, editor_models


class ChapterInput(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    brief: str = Field(default='', max_length=4000)


class SourceRef(BaseModel):
    kind: Literal['session', 'source']
    id: str = Field(pattern=r'^[a-f0-9]{32}$')


class GenerateInput(BaseModel):
    sources: list[SourceRef] = Field(min_length=1, max_length=20)
    brief: str | None = Field(default=None, max_length=4000)


class MaterialInput(BaseModel):
    text: str = Field(max_length=100000)
    version: int = Field(ge=0)


class VersionInput(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=30000)
    edit_version: int = Field(ge=0)


class ConfirmInput(BaseModel):
    edit_version: int = Field(ge=0)


class ModelChoice(BaseModel):
    model_config = ConfigDict(extra='forbid')
    provider: Literal['doubao_realtime', 'deepseek', 'ark']
    model: str = Field(min_length=1, max_length=200)
    api_key: str = Field(default='', max_length=2000)


class ModelInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    extractor: ModelChoice | None = None
    writer: ModelChoice | None = None


def install_chapters(router, store, current_user, profile_access):
    def access(ident, user, edit=False):
        c = chapters.chapter(store, ident)
        if not c:
            raise HTTPException(404, '章节不存在')
        p = profile_access(c['profile'], user, edit=edit)
        return c, p

    def guarded(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except materials.ConflictError as error:
            raise HTTPException(409, str(error)) from None
        except (materials.MaterialError, ValueError) as error:
            raise HTTPException(422, str(error)) from None

    @router.get('/editor-models')
    def get_models(user=Depends(current_user)):
        return editor_models.public_config(store, user.role == 'admin')

    @router.put('/editor-models')
    def set_models(data: ModelInput, user=Depends(current_user)):
        if user.role != 'admin':
            raise HTTPException(403, '仅管理员可以配置整理与写作模型')
        return guarded(editor_models.save_config, store, data.model_dump(exclude_none=True))

    @router.get('/profiles/{profile}/materials')
    def list_materials(profile: str, user=Depends(current_user)):
        profile_access(profile, user, edit=True)
        return {'materials': materials.list_materials(store, profile)}

    @router.get('/profiles/{profile}/materials/{kind}/{ident}')
    def read_material(profile: str, kind: str, ident: str, user=Depends(current_user)):
        profile_access(profile, user, edit=True)
        result = guarded(materials.material, store, profile, kind, ident)
        result.pop('turns', None)
        return result

    @router.put('/profiles/{profile}/materials/{kind}/{ident}')
    def revise_material(profile: str, kind: str, ident: str, data: MaterialInput, user=Depends(current_user)):
        profile_access(profile, user, edit=True)
        result = guarded(materials.revise, store, profile, kind, ident, data.text, data.version, user.id)
        try:
            editor_jobs.enqueue_material(store, profile, [{'kind': kind, 'id': ident}], user.id)
        except materials.MaterialError:
            pass  # 空稿作为排除保留，不重新使用原始文字。
        result.pop('turns', None)
        return result

    @router.get('/profiles/{profile}/chapters')
    def list_chapters(profile: str, user=Depends(current_user)):
        p = profile_access(profile, user)
        drafts = p['role'] != 'reader'
        return {'chapters': chapters.listing(store, profile, drafts), 'jobs': editor_jobs.jobs(store, profile) if drafts else []}

    @router.post('/profiles/{profile}/chapters')
    def create_chapter(profile: str, data: ChapterInput, user=Depends(current_user)):
        profile_access(profile, user, edit=True)
        if not data.title.strip():
            raise HTTPException(422, '章节标题不能为空')
        return chapters.create(store, profile, data.title.strip(), data.brief)

    @router.get('/chapters/{ident}')
    def get_chapter(ident: str, user=Depends(current_user)):
        c, p = access(ident, user)
        result = chapters.detail(store, ident, p['role'] != 'reader')
        if not result:
            raise HTTPException(404, '章节尚未确认')
        return result

    @router.post('/chapters/{ident}/generate')
    def generate_chapter(ident: str, data: GenerateInput, user=Depends(current_user)):
        c, _ = access(ident, user, edit=True)
        return guarded(editor_jobs.enqueue_chapter, store, c['profile'], ident,
                       [s.model_dump() for s in data.sources], user.id, data.brief)

    @router.put('/chapters/{ident}/versions/{vid}')
    def edit_version(ident: str, vid: str, data: VersionInput, user=Depends(current_user)):
        access(ident, user, edit=True)
        if not data.title.strip() or not data.body.strip():
            raise HTTPException(422, '标题和正文不能为空')
        return guarded(chapters.edit, store, ident, vid, data.title, data.body, data.edit_version)

    @router.post('/chapters/{ident}/versions/{vid}/confirm')
    def confirm_version(ident: str, vid: str, data: ConfirmInput, user=Depends(current_user)):
        access(ident, user, edit=True)
        return guarded(chapters.confirm, store, ident, vid, data.edit_version, user.id)

    @router.get('/profiles/{profile}/writing-jobs')
    def writing_jobs(profile: str, user=Depends(current_user)):
        profile_access(profile, user, edit=True)
        return {'jobs': editor_jobs.jobs(store, profile)}

    @router.post('/writing-jobs/{ident}/retry')
    def retry_job(ident: str, user=Depends(current_user)):
        with store.db() as db:
            job = db.execute('SELECT profile FROM writing_jobs WHERE id=?', (ident,)).fetchone()
        if not job:
            raise HTTPException(404, '任务不存在')
        profile_access(job['profile'], user, edit=True)
        return editor_jobs.retry(store, ident)
