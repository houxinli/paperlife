# 跨会话章节写作实施计划

> **For agentic workers:** Use superpowers:executing-plans; independent provider/UI modules may be delegated with fixed contracts. User explicitly approved implementation and existing-site deployment; no additional plan approval is needed.

**Goal:** 接入非虚构写作规则，拆分素材整理/章节写作模型，保存可追溯的素材版本与章节版本，人工确认后入书。

**Architecture:** 现有实时语音不改；新编辑任务队列与版本表位于同一私有 SQLite。模型适配、素材快照、章节版本、HTTP 授权和页面各自独立。旧条目与旧队列保留兼容；已确认章节只通过显式确认新版本更新当前指针，历史版本不修改。

**Tech Stack:** Python/FastAPI/SQLite/httpx/websockets，原生 Web JS；使用现有部署流程。

**Spec:** 用户本轮已批准的方案及 `paperlife-deliverables/paperlife-writing-rules-v1.txt`（公开写作资料的原创操作化规则，无家人材料）。

## Global Constraints

- 原声和原始转写不可覆盖；校订独立版本。所有生成先 draft；不自动覆盖确认内容。
- guest 只读绑定档案的已确认书籍；reader 不读章节草稿；所有授权由服务端执行。
- 公开仓库不含真实资料、姓名、链接或密钥。真实 API 验证仅用虚构资料。
- 本次部署到既有 GCP 站；不购买国内服务器，不迁移主体或创建外部服务。
- 独立 extractor/writer 配置，默认明确使用已有豆包文本调用；DeepSeek/火山方舟可由管理员私有配置。密钥不回显、不写任务快照。

## Review Focus

- 校订发生在生成中或确认前：快照保留、草稿显示过期，拒绝误确认旧素材稿。
- 跨人物素材引用、reader/guest 读取草稿或配置：拒绝，历史版本不成为旁路。
- 重试/进程中断/并发确认：租约校验、幂等提交、发布指针乐观锁。
- 校订稿不能假装逐字原声定位，引用需区分原始 turn 与整篇人工校订。
- 模型引用存在不等于内容为真：独立事实核对阶段、最终人工确认；生成失败保留原始材料和已确认版。

### Task 1: 模型适配与写作规则

Files: `editor_models.py`, `writing_rules.py`, `policies/biographer.md`, `tests/test_editor_models.py`.
Interface: `public_config(store, can_configure)`, `save_config(store, data)`, `generate_editor(store, stage, payload)` -> `{output: dict, metadata: {provider,model,prompt_version,prompt_hash}}`. Stages: extract / outline / write / audit / polish. extract uses extractor, others writer. Payload is authorized snapshot only. User-visible config GET/PUT handled by root router.
- [ ] Test configuration independent roles, secret redaction/preservation, rejected endpoint/provider, valid JSON and output limits with fake transport.
- [ ] Implement separate private configuration with speech-text fallback, fixed HTTPS DeepSeek/Ark endpoints and explicit errors; no silent provider fallback.
- [ ] Embed versioned rules in all writing stages; fact audit returns `{ok: boolean, issues: [...]}`; outline returns `{outline: [...]}`; write/polish return `{title,paragraphs:[{text,citations:[{unit_id,quote}]}]}`; extract returns `{items:[{kind,title,body,time_text?,people?,place?,citations:[{unit_id,quote}]}]}`.

### Task 2: 素材、任务和章节版本核心

Files: new `materials.py`, `chapters.py`, `editor_jobs.py`, `chapter_api.py`; integrate `store.py`, `api.py`, `processing.py`, `content.py`, `narrator.py`.
- [ ] Write failing tests for two-session provenance, source correction snapshots, no automatic publishing, stale confirmation, immutable accepted revision, cross-profile and reader/guest access, restart/retry idempotency.
- [ ] Add additive schema only. Material selection refs `{kind:'session'|'source',id}`. Unit fields `unit_id,source_kind,source_id,revision,title,text,turn_id,seconds,context`; edited sessions have approximate/no turn alignment and explicitly identified revision.
- [ ] After ended/interrupted sessions and session/source corrections enqueue extraction only; old automatic story tasks are compatibility data, not the new default. Persist job snapshots and versioned provenance. Chunk extraction without dropping tails; reject overly large chapter selections explicitly instead of truncating.
- [ ] Chapter generation checkpoints outline -> write -> audit -> polish -> audit; citations checked each time; failures retry boundedly with generic errors. Commit new immutable draft version; never modify published versions.
- [ ] Draft manual edit uses version counter, confirmation atomically checks material freshness and prior published pointer; mark reviewed version confirmed and move pointer, preserve all historical versions.

### Task 3: 网页编辑入口

Files: `web/life/chapters.js`, `index.html`, `style.css`, `app.js`, `book.js`, `narrator.js`, focused JS tests.
HTTP contracts (under `/life`):
- GET `/profiles/{p}/materials` -> `{materials:[{kind,id,title,revision,chars,has_audio}]}`; GET `/profiles/{p}/materials/{kind}/{id}` -> `{kind,id,title,revision,text,original_text}`; PUT same -> `{text,version}` revision conflict 409.
- GET `/profiles/{p}/chapters` -> `{chapters:[{id,title,published_version_id,published_version,draft_count,latest_version,stale}],jobs:[...]}`. Reader gets confirmed only.
- POST `/profiles/{p}/chapters` `{title,brief}` -> chapter.
- POST `/chapters/{id}/generate` `{sources:[{kind,id}],brief?}` -> job (pending/running/completed/failed).
- GET `/chapters/{id}` -> `{chapter,versions:[{id,number,status,edit_version,title,body,paragraphs,evidence,sources,stale,metadata,base_published_id,...}]}`; reader receives only published/previously confirmed versions.
- PUT `/chapters/{id}/versions/{v}` `{title,body,edit_version}` -> version; draft only.
- POST `/chapters/{id}/versions/{v}/confirm` `{edit_version}` -> confirmed version; explicit human confirmation only, stale/concurrent conflict 409.
- GET `/profiles/{p}/writing-jobs`; POST `/writing-jobs/{id}/retry` (failed only).
- GET/PUT `/editor-models`, admin-only PUT; shape `{extractor:{provider,model,configured},writer:{...},can_configure}`; PUT role configs `{provider,model,api_key?}` blank preserves existing same-provider key. Allowed providers `doubao_realtime`, `deepseek`, `ark`.
- [ ] Provide source selection, multiple chats/imports, draft/version comparison, source quote and recording links, stale notices, save draft then explicit confirm, background progress/manual refresh, private model settings.
- [ ] Confirmed chapters appear in existing book endpoints as additional `chapters` array and narrator display, without exposing drafts. UI profile switches invalidate stale responses.

### Task 4: 验证与发布

- [ ] Run `PYTHON_BIN=<existing venv> ./scripts/check.sh`, focused actual route and job integration tests; synthetic browser preview and one synthetic provider smoke if configured.
- [ ] Fresh independent whole-branch review; repair important findings and run appropriate checks.
- [ ] Public source-only PR/CI/merge. Backport scoped files to existing private host, preserving host auth; backup database via SQLite backup plus retain private audio volume; deploy with `promote_to_main.sh`.
- [ ] Verify public assets/health, additive schema, unchanged profile/link/audio inventory and confirmed items. Do not trigger real family content generation as a smoke test.
