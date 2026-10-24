# 双端复用、契约与兼容发布

Paperlife 采用一套后端业务、一份共享纯状态机，以及 Web/微信各自的平台适配。共享的目标是同一条协议规则只维护一次；录音、授权弹窗、网络发送和界面仍使用各平台的真实能力。

## 规模与复用口径

2026-10-24 在本轮重构前的 `c941ea8` 上，按 Git 跟踪的生产源码统计；排除测试、文档、示例配置、生成文件和第三方代码。行数为物理行，字节为 UTF-8。源码有较长的压缩行，不能用行数代替功能复杂度。

| 范围 | 文件数 | 行数 | 字节 |
|---|---:|---:|---:|
| `paperlife/**/*.py` 后端 | 18 | 1,734 | 98,813 |
| `web/life/*.js` 运行代码 | 6 | 534 | 45,852 |
| 小程序运行 JS | 7 | 448 | 31,780 |
| Web HTML/CSS | 2 | 45 | 23,947 |
| 小程序 WXML/WXSS | 3 | 88 | 9,829 |

重构前两客户端共同 import/require 的 JS 模块为零，但独立项目的后端业务一直只有一套；现有 GCP 兼容站仍有一份发布副本，见末尾的迁移边界。后端中有些接口只供网页家人校对使用，不能把全部后端字节称为两端功能重用量，更不能据此承诺某个“复用率”。

字面检查方法：对两端运行 JS 逐行 trim，排除空行、注释行和少于 30 字符的行，再取完全相同文本的交集。检查得到 7 种相同行，对应 Web 7 行、小程序 8 行。它只衡量字面重合，不能识别改名或拆行后的同一逻辑。已确认的语义重复是原小程序 `lib/protocol.js` 的 25 行 AudioGate 与 Web `audio.js` 中分散的回答取消、迟到音频及结束规则；本轮将这部分移到单一源。

后续统计应将 `shared/audio-gate.js` 计一次，并排除两个自动生成的 `audio-gate.js`；运行包存在两份产物不等于存在两份手工实现。

## 责任边界

| 层 | 位置 | 职责 |
|---|---|---|
| 账号与身份 | `paperlife/auth.py`、`identity_providers.py` | 密码、短信、微信登录；普通身份不自动取得家人授权 |
| REST 与授权 | `lifebook/api.py`、`narrator.py`、`content.py` | 账号/guest 范围、一次性票据、历史、confirmed 内容与家人校对 |
| 实时对话 | `lifebook/realtime.py`、`prompt.py`、`preferences.py`、`policies/interviewer.md` | 模型代理、开场、结束意愿、明确偏好；不由模型修改授权 |
| 资料与整理 | `lifebook/store.py`、`processing.py`、`generation.py` | 原声/原始转写保留、草稿、确认及来源 |
| 共享纯逻辑 | `shared/audio-gate.js` | 回答音频边界、取消/完成记录、结束后不可重新开门 |
| Web 音频适配 | `web/life/audio.js`、`capture.js` | getUserMedia、AudioWorklet、WebSocket、浏览器播放和恢复声音 |
| 微信音频适配 | `miniprogram/lib/voice.js`、`pcm.js`、`protocol.js` 的 OrderedSender | RecorderManager、PCM/WAV 分片、回调式发送与背压、设备生命周期 |
| 身份入口 | Web `app.js`/`entry.js`；微信 `lib/api.js` | 各端凭证存储、Cookie/Bearer/wx.login、独立邀请凭证；不合并认证系统 |
| 页面 | Web `app.js`/`book.js`/`narrator.js`；微信 `pages/index` | DOM 与 WXML 各自渲染；网页家人校对界面不是小程序的共同组件 |

`paperlife/lifebook/protocol.py` 编解码供应商的二进制帧；客户端 AudioGate 处理的是本服务的 JSON 边界事件。这两个“协议”位于不同层，不能合并。

## AudioGate 接口与平台差异

```js
const gate = new AudioGate({allowLegacyAudio: false});
gate.event(event); // 接受服务器事件，只改变内存状态
gate.accepts();    // 是否允许当前二进制音频进入播放适配器
gate.cancel();     // 取消当前回答，不改变当前 question_id，不结束会话
gate.close();      // 永久关闭；后续事件不能重新打开
```

Web 显式选择 `allowLegacyAudio: true`，保留现有试用站在独立音频边界出现前的旧协议兼容；微信默认严格关闭。文字 `answer_start` 不打开新协议音频门。见到 `audio_start` 后，旧式 `answer_start` 不再开门；`audio_sentence_end` 不关闭整段回答；匹配的 `audio_end` 才关闭。取消和完成的回答不能复活。`ending`、`ended` 和本地 `close()` 是终止状态。

`replyId`、`questionId`、`audioReplyId`、`audioQuestionId`、`finished` 等状态只有核心维护。Web 仍在适配器累计接收、抑制、播放、恢复失败等诊断，不复制第二套状态。共享核心不调用 stop 麦克风或清空播放队列，这些副作用由各适配器处理。

小程序 `updateTranscript(rows, event)` 另负责内存中的展示行。收到 `interrupt` 时冻结已有的未完成助手行，下一回答另起一行；`final: true, interrupted: true` 在此只表示展示行不再被替换，不把临时文本升级成服务器确认的原始转写，也不写入数据库。网页保留其现有同等行为。

## 生成、测试与打包

唯一手写源为 `shared/audio-gate.js`。运行：

```sh
node scripts/sync_shared.mjs
node scripts/sync_shared.mjs --check
```

脚本附加模块导出包装，生成以下文件；没有转译器、npm 运行依赖或全局变量桥接：

| 产物 | 使用方式 | 交付方式 |
|---|---|---|
| `web/life/audio-gate.js` | `audio.js` 相对 ESM import | 已有 Docker `COPY web`，由 `/life/audio-gate.js` 提供 |
| `miniprogram/lib/audio-gate.js` | `protocol.js` 相对 CommonJS require | 位于现有微信包根内，随 `miniprogram/` 源码导入/打包 |

产物头部记录来源和 SHA256，产物随源码提交，部署无需 Node。`scripts/check.sh` 首先以 `--check` 核对完整预期内容；缺失或手工修改产物会失败，检查不会悄悄修复文件。现有 GitHub Actions 调用同一检查脚本，因此不需要另建 CI 管线。禁止让运行端跨包引用仓库顶层 `shared/`。

`web/life/shared.test.mjs` 对两种产物回放同一组音频状态场景，并在临时目录验证产物缺失/修改会让检查失败。原有 Web 和微信适配测试继续核对播放、实际停麦、发送顺序和恢复声音；这些合成测试不代替微信真机验收。

## v1 契约及变更规则

`contracts/conversation-v1.json` 是测试使用的可执行契约清单，包含真实服务端事件名、字段最低形状、角色/状态枚举、分页上限、音频格式与 transport。它不是运行时 schema 框架，也未给现有报文添加版本字段或把路由改成 `/v1`。

`tests/test_conversation_contract.py` 使用实际路由响应检查账号/guest 历史、详情、confirmed 书籍、票据及输入边界；用实际实时代理处理虚构供应商帧，检查服务器事件和保存的 WAV 参数，再通过 `tests/replay_gate.mjs` 将同一实际输出回放到两种客户端核心。测试数据均虚构。客户端本地 `audio_state`、`disconnected` 等 UI 事件不属于服务器 wire 契约。

- 新增可选字段及可忽略的提示事件可以保持 v1，但必须加入契约场景和实际生产者测试，确认旧客户端能忽略。
- 不能在 v1 中改变既有字段含义、音频采样率、角色枚举或终止事件语义，也不能删除必需字段。此类修改需要新增协议版本并设计客户端能力协商或独立入口；当前尚未实现版本协商。
- `transport` 仅选择服务器允许的握手策略，不是客户端自行授予权限。ticket 仍由已授权 REST 请求签发、绑定主体/档案、限时单次使用，WSS 首条 JSON 传 ticket；凭证不进入 URL。
- Web/微信登录凭据和 guest capability 各自保留；共同契约不能把 guest 升级成成员，不能展示未确认草稿。默认话题由服务器决定，客户端只在明确选择时覆盖。
- 接口和数据库现有标识符保持稳定，产品从“采访”改为“聊天”不会触发重命名迁移。

## paperbridge 旧站回补边界

国内迁移完成前，paperbridge 旧 GCP 应用是独立运行实例，不与新实例共享可写档案库。本轮纯逻辑提取不构成数据迁移或身份迁移。

网页回补必须同时交付 `web/life/audio.js` 与生成的 `web/life/audio-gate.js`，映射到旧库 `apps/web/public/life/`，不能漏掉新增的 import 目标。其他页面、提示词、偏好等改动应按本轮清单另行回补；小程序生成产物只进入小程序开发包。旧库可以回放对应网页测试，若移植跨端契约测试，还需一起移植契约/测试依赖并适配路径，不能只复制测试入口。

旧库的 `lifebook/api.py` 有宿主适配：`PrivateRoute` 来自 `briefing.api`，会话 cookie 为 `paperbridge_session`；新库使用 `private_route` 和 `paperlife_session`。顶层 `main.py`、账号服务与部署路径也不同，不能整套覆盖，不能把 `/auth/wechat` 在新库存在视为旧站已开通微信认证。

当前重构只通过本地合成测试核对代码与协议。真实微信开发工具、iOS/Android 录放音、供应商方言听感和国内网络仍需要独立验收；代码生成检查不证明这些效果，也不证明旧站已经发布。

## 后续演进边界

- 独立产品只在 paperlife 开发，旧 GCP 宿主只回补固定版本的兼容文件。正式迁移后取消这个临时副本，不让两仓独立演化业务。
- 当前票据与活跃会话在单进程内，数据库/音频是单机私有卷；不能直接开启多 worker 或多副本就宣称支持横向扩容。扩容前先调整共享票据、会话租约和持久化存储，并保留同一客户端契约。
- 当前 v1 是覆盖核心流的可执行测试契约，不是完整 SDK 生成器。新业务接口必须补文档、边界测试和兼容策略；不要把测试通过当成所有未来场景都已覆盖。
