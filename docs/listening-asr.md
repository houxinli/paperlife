# 旁听 ASR 适配

`paperlife/lifebook/listening_asr.py` 仅识别输入音频，不调用 TTS、不创建永久声纹、不推断实名。默认关闭；管理员通过既有服务端授权后，调用 `save_config(store, {"enabled": true})`。私有运行时目录的 `listening-asr.json` 只含开关，以原子替换方式写入，权限为 `0600`。缺失或损坏配置视为关闭。此文件及语音凭据不进入仓库。

`configured(store)` 表示既有 `store.provider()` 中存在格式有效的 API Key，或完整的 App ID / Access Token；它独立于 `enabled`，也不代表资源已开通、余额充足或真实供应商可用。`public_config(store, can_configure=False)` 只返回 `enabled`、`configured`、`provider`、`can_configure`。配置 API 不接受新密钥或自定义地址。

## 已核对的官方接口

2026-10-24 核对：固定连接 `wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async`，资源为 ASR 2.0 小时版 `volc.seedasr.sauc.duration`。优先使用 `X-Api-Key`；旧凭据使用 `X-Api-App-Key`（值为 App ID）及 `X-Api-Access-Key`，这与既有实时对话的鉴权字段不同。请求设置 `enable_nonstream=true`、`enable_speaker_info=true`、`show_utterances=true`、`ssd_version="200"`。不指定 language；关闭顺滑和 ITN，保留标点。

依据：[实时语音识别接口](https://docs.volcengine.com/docs/DoubaoVoice/bidirectional-streaming-automatic-speech-recognition-websocket?lang=zh)、[说话人分离最佳实践](https://docs.volcengine.com/docs/DoubaoVoice/speaker-separation?lang=zh)、[二进制协议及旧鉴权说明](https://docs.volcengine.com/docs/DoubaoVoice/LargemodelstreamingautomaticspeechrecognitionAPI?lang=zh)。说话人编号只是当前录音的声音分组；外放、噪声、短插话和重叠说话需要 iPad 真人验收，参数及协议测试不证明声学效果。

## 调用与快照语义

```python
async with connect_asr(store) as upstream:
    # 发送和接收应在两个并行协程进行。
    await upstream.send_audio(pcm_bytes)
    await upstream.send_audio(b"", final=True)
    async for event in upstream.events():
        # event = {"segments": [...], "final": bool}
        # 用 segments 替换当前快照，不能把它追加到旧快照。
        ...
```

以上片段展示接口；实时代理须在收音期间持续运行 `events()`，避免上游回压。音频为 16kHz、单声道、16 位有符号小端 PCM。停止收音时只发送一次 `final=True`，然后继续接收直到 `event["final"] is True`。调用方负责整段录音和最终排空的超时、授权复查，以及中断后的原声/部分结果保存。上下文退出会有界关闭连接，不会自动提交音频或伪造最终成功。

请求明确设置 `result_type="full"`；每次带 `utterances` 的响应都替换完整累计结果。明确的空 `utterances` 是空快照；仅 ACK（省略 `result` / `utterances`）保留上一快照。适配层不猜测违约的增量返回。服务端若只返回非空整段文字却缺少所请求的分句，适配层报告协议错误，避免丢失说话人归属。

每段格式为 `{id, speaker_id, text, start_ms, end_ms}`。`id` 为当前快照内的 `segment-1` 等序号；临时结果的文字和分句边界允许修订，因此最终前不得把编号视作永久归属依据。最终快照的编号稳定，后续人工修订基于这份快照。支持分句直接 `speaker_id`，以及 `additions.speaker_id` / 旧版 `additions.speaker`（字典或 JSON 字符串）。缺失说话人统一为 `unknown`；不会根据参与者名单映射姓名。编号与核心层统一为 `[A-Za-z0-9_-]{1,80}`，合法编号原样保留，不做可能碰撞的替换或重命名。

主代理的真实供应商合成语音探针确认，一遍 `source="stream"` 临时分句可能省略 `start_time`，但 `words` 含有效时间戳。适配层仅在分句边界字段缺失时，用词级最早 `start_time` / 最晚 `end_time` 补齐对应边界；已给出的分句边界保持不变。参与推导的词级时间必须是合法整数、起止有序且在20分钟内。不使用 `0` 或 `audio_info.duration` 猜测分句时间；非空文字若最终仍无有效时间则报告协议错误，调用方保留已有部分结果，不可标记识别完成。

**`final` 是整段传输结束。** 仅服务端二进制终包标志（flags `0x2` / `0x3`）或负序号使其为真；分句 `definite=true`、`source="two_pass"` 均不能结束整段排空。连接提前关闭抛出 `listening_asr_incomplete`，既有快照仍由调用方保存。

## 二进制与边界

请求使用协议 v1、4 字节头、大端负载长度、gzip 压缩。初始化为 JSON full-client 帧（`11 10 11 00`），音频为 raw audio-only 帧（`11 20 01 00`），最后一包为 `11 22 01 00`；后两者正文仍 gzip。停止帧可含零字节音频。JSON 响应支持有/无序号、gzip/无压缩；响应长度必须与声明一致，未知协议或压缩方式拒绝。

单次 `send_audio` 最多 64,000 字节，须偶数字节，内部将网页每次 640 字节（20ms）等小包累计到 6,400 字节（200ms）才发送；停止时将剩余音频作为终包发送，没有余量则发送空终包。接受但尚未发出的字节同样计入总量，关闭连接不自动提交缓冲区。调用方按实时采集节奏发送。整段上限 38,400,000 字节（20 分钟）；终包发送或接收后拒绝再发音频。二进制响应最大 2 MB，解压最大 4 MB，最多 10,000 分句、300,000 文本字符、单分句 16,000 字符。gzip 有界解压，拒绝截断、尾随垃圾和解压炸弹。WebSocket 队列上限为 4；禁用 WebSocket 二次压缩。`websockets` 15+ 显式禁用环境代理，14 使用其默认直连行为。

供应商错误正文、握手错误和原始转写不会进入对外异常。`ASRError` 只使用固定错误码：`listening_asr_disabled`、`listening_asr_not_configured`、`listening_asr_unavailable`、`listening_asr_provider_error`、`listening_asr_protocol`、`listening_asr_incomplete`。取消保持 asyncio 取消语义；关闭失败不会覆盖原有调用方异常。

## 离线验证

```sh
/Users/houxinli/Documents/dev/github/houxinli/paperbridge/services/api/.venv/bin/python -m unittest discover -s tests -p test_listening_asr.py
```

夹具使用虚构文字、假凭据和内存 WebSocket；无真实 API 请求。21 个测试覆盖二进制帧、最终排空、分句修订、说话人兼容、20ms累计分包、配置权限、长度/时间限制、真实响应的词级时间补齐、错误脱敏和关闭。真实供应商合成双人语音验证由主代理单独执行，不把家庭资料用于调试。
