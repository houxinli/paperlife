# 数据模型与状态

更新：2026-10-26。两个私有 SQLite 库都在数据卷里，不进入仓库：

- `auth.sqlite3`：账号、登录会话、第三方身份、OTP、限流记录，由 `paperlife/auth.py` 独占。lifebook 只通过 `account_active(uid)` 与 `account_authorized(uid, digest)` 两个函数询问账号状态，不直接读这个库。
- `life/life.sqlite3`：档案、聊天、素材、整理任务与章节，由 `paperlife/lifebook/store.py` 打开（WAL 模式），表结构由 `paperlife/lifebook/schema.py` 维护。
- 原声在 `life/<session>-user.wav` 与 `life/<session>-assistant.wav`；`provider.json`、`editor-models.json` 等私有配置也在 `life/` 下，权限 0600。

## 迁移规则

`schema.py` 中的 `MIGRATIONS` 按顺序编号，当前版本号写在 `PRAGMA user_version`。启动时只执行比当前版本新的迁移；数据库版本高于程序时拒绝启动，防止旧程序写坏新库。

- 只追加新迁移，不修改已发布的迁移。
- 数据回填也写成迁移，保证只执行一次。
- 迁移 1 是基线，对没有版本号的旧库保持幂等（缺表建表、缺列补列）。
- 迁移 2 退役旧整理队列：`processing_jobs` 中未完成的行标记为 `superseded`，交给新队列重新整理；已完成旧任务生成的草稿保持不变。
- 迁移 3 为常用外键列补索引（`turns.session`、`sessions.profile/user`、`members.user`、`items.profile` 等），并给 `writing_jobs` 增加 `source_session` 列（素材任务的来源会话），回填旧任务。

## 表与关系

```
profiles 1─* members (user→auth 账号)
profiles 1─* narrator_links (只存 token 的 SHA256)
profiles 1─* sessions 1─* turns
                     ├─ quality_flags / voice_metrics
                     ├─ revisions（会话整体校订旧格式）
                     └─ listening_originals 1─* listening_annotations（仅 mode=listening）
profiles 1─* sources（导入的背景资料）
sessions / sources 1─* material_revisions（素材校订版本）
profiles 1─* items 1─* item_revisions
profiles 1─* interview_preferences（来源 session+turn）
profiles 1─* chapters 1─* chapter_versions
profiles 1─* writing_jobs ─→ items 或 chapter_versions
processing_jobs（旧队列，只读历史）
```

| 表 | 用途 | 不可变约束 |
|---|---|---|
| `profiles` | 被讲述者档案；`owner` 是所有者账号 | `context` 首次填写时冻结成 `sources` 一行 |
| `members` | 档案成员：`owner` / `editor` / `reader` | 授权只由所有者修改，模型不能修改 |
| `narrator_links` | 长辈专属链接，180 天有效，可撤销 | 服务端只存哈希；guest 的 uid 记为 `-link.id` |
| `sessions` | 一次聊天或旁听；`mode` 为 `dialogue` / `listening` | — |
| `turns` | 原始逐轮转写 | 不覆盖；校订另存版本 |
| `material_revisions` | 会话/背景资料的人工校订版本 | 追加版本，用乐观锁版本号防覆盖 |
| `listening_originals` | 旁听的原始识别分段 | 定稿后不改写 |
| `listening_annotations` | 旁听的声音组绑定、逐句纠正 | 追加版本；修改会让衍生草稿过期 |
| `items` | 人物 / 事件 / 事实 / 问题 / 故事条目 | AI 产物以 `draft` 写入；保存来源快照与 `input_hash` |
| `item_revisions` | 条目人工修改历史 | 追加 |
| `interview_preferences` | 从明确最终发言学到的称呼等偏好 | 来源到具体 turn；撤销不删除 |
| `writing_jobs` | 素材整理 / 章节写作任务；素材任务的来源会话记在 `source_session` | `request_key` 去重；保存输入快照与阶段 checkpoint |
| `chapters` / `chapter_versions` | 章节与版本；`published_version_id` 指向已确认版本 | 已确认版本只读，新确认只移动指针 |
| `quality_flags` / `voice_metrics` | 启发式疑点与服务端延迟 | 不含原文日志 |

## 状态

**sessions.status**：`connecting` → `active` → `ended`；连接失败、断线或进程重启时变为 `interrupted`（启动时由 `worker.recover_interrupted` 统一收尾）。票据未被使用、或连接在采访开始前被拒绝时，会话也收尾为 `interrupted`；后台 worker 每轮把超过 5 分钟仍为 `connecting` 的会话标记为 `interrupted`。

后台 worker 每 3 秒补齐已结束但还没有整理任务的会话。暂时无法整理的会话（旁听尚未确认讲述者、校订为空）按“会话+版本”记住，版本变化后再试，避免每轮重复加写锁。

**writing_jobs.status**：`pending` → `running` → `completed`；失败时回到 `pending` 并按 30 秒 × 次数退避，第 3 次失败变为 `failed`，可手动重试。`running` 依靠 10 分钟租约，进程崩溃后由下一次 `claim` 接管。`stage` 记录章节流程的位置：`outline` → `write` → `audit` → `polish` → `final_audit`；核对不通过时进入 `needs_revision`，带着反馈重写。

**items.status**：AI 产物为 `draft`，人工确认后为 `confirmed`；问题类条目为 `open` / `resolved`。reader 与 guest 看不到 `draft`。

**chapter_versions.status**：生成后为 `draft`，人工确认后为 `confirmed` 并成为章节当前版本；旧的确认版保留只读。

**listening_originals.recognition_status**：`pending` → `partial` → `complete`；中断时为 `incomplete`（有部分结果）或 `failed`（无结果）。

## 过期（stale）

每个 AI 草稿保存生成时的素材快照哈希（`items.input_hash`、`chapter_versions.source_hash`）。读取时用当前素材重新计算哈希（`materials.stale`）：素材被校订、旁听归属被修改，或来源已不可用时，草稿标记为过期，不允许直接确认，需要重新生成。

## 敏感数据

两个库与音频都含家庭隐私：备份必须加密，恢复演练在隔离目录进行（见 [operations.md](operations.md)）。测试只使用虚构资料。
