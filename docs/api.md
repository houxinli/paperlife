# 接口一览

更新：2026-10-26。所有接口在 `/api` 下，响应都带 `Cache-Control: private, no-store`。字段与校验以代码中的 Pydantic 模型为准；本地运行后访问 `/docs` 可看到自动生成的 OpenAPI。生产环境应在反向代理处屏蔽 `/docs`、`/redoc`、`/openapi.json`，或只对内网开放。

## 身份

| 方式 | 适用 | 说明 |
|---|---|---|
| 账号会话 | 家人、管理员 | 网页用 `paperlife_session` Cookie，小程序用 `Authorization: Bearer` |
| 专属链接 | 被讲述的长辈 | 请求头 `X-Life-Link: <token>`；网页地址为 `/life/?view=guest#invite=<token>`，token 在 fragment 里，不会发给服务器日志 |
| 实时票据 | WebSocket | 由已授权的 REST 请求签发，60 秒、单次使用，在 WS 首条消息中发送 |

权限：`owner` 可以管理成员与专属链接；`editor` 可以聊天、校订、生成与确认；`reader` 只读，看不到草稿。专属链接只能采访绑定档案，并只读该档案的历史、原声和已确认内容。

## 账号 `auth.py`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/auth/providers` | 已启用的登录方式 |
| POST | `/auth/login` | 用户名密码登录 |
| GET | `/auth/me` | 当前账号 |
| POST | `/auth/logout` | 注销当前会话 |
| POST | `/auth/wechat` | 微信 code 换登录（默认关闭） |
| POST | `/auth/sms/send`、`/auth/sms/login` | 短信 OTP（默认关闭） |

## 档案、成员与链接 `api.py`、`content.py`

| 方法 | 路径 | 权限 |
|---|---|---|
| GET/PUT | `/life/provider` | 读：登录；写：admin |
| GET/POST | `/life/profiles` | 登录；新建者成为 owner |
| GET/PUT | `/life/profiles/{id}` | 成员 / editor |
| GET | `/life/profiles/{id}/members` | owner |
| PUT/DELETE | `/life/profiles/{id}/members/{user}` | owner |
| GET/POST | `/life/profiles/{id}/links` | owner；POST 返回明文 token，仅此一次 |
| DELETE | `/life/profiles/{id}/links/{link}` | owner |
| GET/DELETE | `/life/profiles/{id}/preferences[/{pref}]` | 成员 / editor |

## 聊天与历史 `api.py`、`narrator.py`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET/POST | `/life/profiles/{id}/sessions` | 列表；POST 新建聊天并返回票据（editor） |
| GET/PUT | `/life/sessions/{id}` | 详情与逐轮转写；PUT 校订（editor，带版本号） |
| GET | `/life/sessions/{id}/quality`、`/revisions`、`/audio?track=user|assistant` | 成员 |
| GET | `/life/profiles/{id}/history`、`/book` | 成员；book 只含已确认内容 |
| WS | `/life/live` | 首条消息 `{"ticket": ...}`；事件见 `contracts/conversation-v1.json` |

## 长辈专属入口（`X-Life-Link`）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/life/guest` | 档案名与称呼 |
| POST | `/life/guest/sessions` | 开始聊天，每天最多 20 次 |
| GET/DELETE | `/life/guest/preferences[/{pref}]` | 查看或撤销聊天习惯 |
| GET | `/life/guest/history[/{id}[/audio]]` | 本档案历史与原声 |
| GET | `/life/guest/book` | 已确认的人生之书 |
| POST | `/life/guest/listening`；GET `/life/guest/listening/{id}`、`/life/guest/listening/provider` | 旁听 |

## 旁听 `listening_api.py`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET/PUT | `/life/listening/provider` | 识别服务配置（写：admin） |
| POST | `/life/profiles/{id}/listening` | 开始旁听，需要 `consent: true` |
| GET/PUT | `/life/listening/sessions/{id}` | 识别结果与归属；PUT 追加归属版本（editor） |
| GET | `/life/listening/sessions/{id}/versions/{v}` | 历史归属版本 |

## 素材、条目与章节 `content.py`、`chapter_api.py`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/life/profiles/{id}/sources` | 导入的背景资料 |
| GET/POST | `/life/profiles/{id}/items`；PUT `/life/items/{id}`；GET `/life/items/{id}/revisions` | 条目；reader 看不到草稿 |
| GET | `/life/profiles/{id}/processing`；POST `/life/sessions/{id}/process` | 自动整理状态 / 手动触发 |
| GET/PUT | `/life/editor-models` | 整理与写作模型配置（写：admin） |
| GET | `/life/profiles/{p}/materials`；GET/PUT `/life/profiles/{p}/materials/{kind}/{id}` | 素材与校订 |
| GET/POST | `/life/profiles/{p}/chapters`；GET `/life/chapters/{id}` | 章节 |
| POST | `/life/chapters/{id}/generate` | 选择素材，排队写作 |
| PUT | `/life/chapters/{id}/versions/{v}`；POST `.../confirm` | 编辑草稿 / 人工确认入书 |
| GET | `/life/profiles/{p}/writing-jobs`；POST `/life/writing-jobs/{id}/retry` | 写作任务 |

细节：[章节写作](chapter-writing.md)、[旁听模式](listening-mode.md)、[数据模型](data-model.md)。
