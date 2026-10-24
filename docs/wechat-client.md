# 微信原生小程序开发准备

`miniprogram/` 是无外部依赖的微信原生客户端源代码。它直接调用 REST 和 WSS，没有 web-view 套壳、模拟登录、假录音或停录后伪装实时上传。当前交付是源代码与自动化协议验证；未导入微信开发者工具、未使用真实 AppID/供应商、未真机验收，也未上传、提审或发布。

## 配置与导入

1. 在微信开发者工具中导入仓库的 `miniprogram/` 目录。把项目 AppID 改为自己实际持有的小程序 AppID。仓库的 `touristappid` 只便于查看源文件，运行时会明确阻止登录。
2. 按 `config.example.js` 填写 `config.js` 的 `apiBaseUrl`，格式为 `https://实际域名/可选前缀/api`。必须是 HTTPS；WSS 使用同一主机和路径前缀下的 `/api/life/live`。这里不放 AppSecret、供应商密钥、session 或邀请令牌。
3. 服务端配置对应 AppID 和 AppSecret，并开放本轮新增的本人 history/book 与 `transport: "wechat"` 票据协议。微信公众平台配置该主机的 request、socket 和 downloadFile 合法域名及有效 TLS。源码保持 `urlCheck: true`，不把关闭域名校验当作上线方案。
4. 使用支持隐私接口的基础库（最低 2.32.3；项目示例选择 3.7.12，可在工具中升级）。在微信后台填写真实的《小程序用户隐私保护指引》，说明录音、原声/转写保存、用途、获授权家人可见范围、后端语音供应商及其数据处理、保存期限和删除/联系渠道。不能仅依靠页面文案代替后台声明。
5. 真机打开开发版，用虚构内容走完整链路。配置缺失、微信登录未开通或设备不支持时会显示错误，不能以网页登录成功替代小程序登录验收。

配置和真实 AppID 修改应留在开发者本地；不要将私人令牌、录音、真实访谈或运行日志加入 Git。测试目录在小程序打包时排除，不含真实姓名、账号、服务地址或凭据。

## 身份、邀请与阅读

- 点击微信登录后，客户端调用 `wx.login`，仅将临时 `code` 发到 `POST /api/auth/wechat`。服务器兑换身份后返回 `session_id`，后续账号请求用 `Authorization: Bearer …`，不依赖浏览器 Cookie。
- 账号读取获授权档案列表，可创建本人空白档案（`context: ""`）。账号登录不会自行取得其他家庭档案权限。
- 页面入口 `pages/index/index?invite=…` 接收既有专属链接的 capability。该令牌只通过 `X-Life-Link` 发给 `/life/guest…`，与账号会话分别存储，并按 AppID/API 地址隔离。邀请不会变成管理员、永久成员或账号绑定。无效的新邀请会清除旧邀请；会话进行中收到新邀请，会先停止旧会话，再切换档案，避免混用人物资料。
- 邀请是敏感凭证。只应由已获授权的家人经私密渠道发送。页面不提供转发按钮，微信索引被禁用；用户可清除本机邀请。服务端失效/撤销检查仍是最终权限边界。
- guest 使用 `/life/guest/history`、`/life/guest/history/{id}`、同路径 `/audio?track=user|assistant` 和 `/life/guest/book`；账号使用 `/life/profiles/{id}/history`、`/life/profiles/{id}/book`，详情与音频仍走 `/life/sessions/{id}`。history 为分页对象，book 只展示 `confirmed` 内容。
- 历史对话、当前对话折叠和人生之书在同一页面切换，不停止麦克风，也不强制滚动。访谈中可阅读，历史原声回听在访谈结束后启用。下载音频通过鉴权头获取临时文件，关闭回听/离开页面时尝试删除，不写入相册或公开 URL。文本只保留于页面内存；会话和邀请保留于微信应用存储，可退出/清除。

## 录音同意与实际停止

每次开始前显示本次录音与保存用途，用户点击同意后才调用隐私授权及 `wx.authorize({scope: "scope.record"})`。隐私弹层的同意由真实 `agreePrivacyAuthorization` 按钮触发，没有自动 resolve 同意。拒绝后仍可阅读。`app.json` 声明录音用途，不声明后台音频或无关的定位权限；用户可以自行打开微信授权设置。

麦克风只在一次性 ticket 经 WSS 首条 JSON `{ticket}` 验证且服务器发送 `ready` 后开始。客户端不会将 Bearer 或邀请放进 socket URL。票据通过账号或 guest 的 session 创建接口签发，携带 `transport: "wechat"`；服务端必须核验票据绑定和原生 Origin，不能因该标志跳过认证。

收到 `ending`、用户点结束、页面隐藏或系统录音中断时，立即调用真实 `RecorderManager.stop()`，关闭后续输入并停止排队播放。正常结束等待 `ended`，超时关闭连接。单次录音到微信最长十分钟边界时明确保存结束；没有悄悄停录后仍显示正在听，也没有循环停录/上传来假装连续实时。用户可在结束后再开始一段。

## 音频契约和当前限制

实现为 `format: "PCM"`、`sampleRate: 16000`、单声道、2 KB 分帧；注意官方枚举是大写 `PCM`。输入适配器保留跨帧的半个样本，检查完整样本，并拒绝识别出的 MP3/AAC/Ogg/FLAC 容器。如果设备返回标准 PCM WAV，可解析分片 RIFF 头并验证 16k/单声道/16位，去掉头后发送。不会把压缩音频直接当 PCM。五秒内没有真实帧会失败并停麦。

上行由 `SocketTask` 的 send 回调串行发送，限制待发送字节数并设发送超时；取消会移除待发音频。微信 API 不公开浏览器式 `bufferedAmount`，send 成功回调也不等于供应商已消费数据，需真机弱网验收。下行为服务器 s16le/24k/单声道 PCM，显式用 DataView 读取小端样本、转 Float32 后通过 `WebAudioContext.createBuffer(1, n, 24000)` 与 BufferSource 排程。播放队列限长，暂停时显示恢复入口。

`audio_start` 打开音频边界，`sentence_end` 不关闭整段回答，`audio_end` 仅关闭对应回答；`interrupt` 清空播放并记录被取消的 reply/question。迟到的旧回答、已完成回答或 ending 之后的音频不能重新打开播放。`transcript` 更新临时气泡，final 后保留原文。

手机平台和 API 检查允许开发版尝试，桌面微信和模拟器不允许录音。基础库类型定义没有保证所有硬件上的裸 PCM 位深/字节序和声学处理；首帧检测不是这些属性的证明。当前没有真实 iOS/Android/HarmonyOS PCM、蓝牙/有线耳机、音量静音、回声消除、双向抢话、四川话识别、国内网络延迟验收，不作这些保证。

## 官方依据（2026-10-24 核对）

微信文档站在本次抓取环境无法直接打开，因此同时读取了腾讯官方 `wechat-miniprogram/api-typings` 当前源码，不以第三方转载或支付宝/抖音 API 代替微信。

- [微信官方 API 类型定义](https://github.com/wechat-miniprogram/api-typings/blob/master/types/wx/lib.wx.api.d.ts)：`RecorderManagerStartOption` 与 `RecorderManager` 定义支持 PCM 分帧，PC 不支持采样率选择；`WebAudioContext` 和 BufferSource 定义支持原始样本播放与时序控制；隐私授权定义说明实际同意按钮流程。
- [RecorderManager.start](https://developers.weixin.qq.com/miniprogram/dev/api/media/recorder/RecorderManager.start.html)、[onFrameRecorded](https://developers.weixin.qq.com/miniprogram/dev/api/media/recorder/RecorderManager.onFrameRecorded.html)、[createWebAudioContext](https://developers.weixin.qq.com/miniprogram/dev/api/media/audio/wx.createWebAudioContext.html)、[createBuffer](https://developers.weixin.qq.com/miniprogram/dev/api/media/audio/WebAudioContext.createBuffer.html)：对应官方规范入口，开发者真机验收前应在可访问的环境复核最新说明。
- [隐私协议开发指南](https://developers.weixin.qq.com/miniprogram/dev/framework/user-privacy/PrivacyAuthorize.html)、[requirePrivacyAuthorize](https://developers.weixin.qq.com/miniprogram/dev/api/open-api/privacy/wx.requirePrivacyAuthorize.html)、[wx.authorize](https://developers.weixin.qq.com/miniprogram/dev/api/open-api/authorize/wx.authorize.html)：隐私同意与麦克风授权分别处理。
- [腾讯官方 TDesign 小程序 app.json](https://github.com/Tencent/tdesign-miniprogram/blob/develop/packages/tdesign-miniprogram/example/app.json)：可核对 `permission.scope.record.desc` 声明样例。

个人主体、AI 深度合成类目、备案和算法材料是另一个上线门槛。本次已有设计记录指出个人主体不能申请深度合成类目，本产品应在合适主体与材料核实后再考虑提审。能导入开发源码不等于具备上线资格；客户端代码不绕过这些限制。

## 本地检查与后续真机验收

无 npm 安装步骤。在仓库根目录运行：

```sh
node --test miniprogram/tests/*.test.cjs
```

测试使用虚构 API 响应和合成 PCM，验证配置失败、code 兑换、Bearer/邀请隔离、跨服务存储隔离、PCM/WAV 边界、24k播放参数、多句回答、旧音频抑制、发送顺序/背压、真实 stop 调用、后台/中断入口、录音到时及取消连接。它们不验证微信渲染器、官方开发工具编译、实际权限弹窗、真实认证/后端供应商或硬件录放音。

使用虚构讲述在真机上验收：

1. 配置缺失/错误、拒绝隐私和拒绝麦克风都不能开始录音；成功兑换身份后只看到获授权档案，邀请只能读绑定档案的 confirmed 内容。
2. 录制可辨识的测试音，确认上行确为 16k/s16le/mono、速度和音高正确；对照服务端原声 WAV 的时长。检查 iOS/Android、耳机及扬声器场景，不能只听 AI 输出就判定输入正常。
3. 连续至少三轮、多句回答、说话打断、翻阅/折叠历史、静音后恢复、弱网、退出小程序、系统来电、十分钟边界和“结束”语句。确认真实录音指示关闭，末句存档，旧回答不会复活。
4. 使用不同虚构档案/撤销邀请测试越权拒绝，退出后不继续显示旧内容；重新打开时已保存的会话和已确认人生之书可正常阅读。

## 共享核心维护

长期开发请直接导入完整仓库的 `miniprogram/`，开发包是版本快照。音频回合规则只修改 `shared/audio-gate.js`，再运行 `node scripts/sync_shared.mjs`；不要手改包里的 `lib/audio-gate.js`。CI会核对网页和小程序的生成产物，并按 `contracts/conversation-v1.json` 回放同一协议场景。
