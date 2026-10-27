# 长辈采访体验实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox syntax for tracking.

**Goal:** 修复连续回合播放，简化长辈入口，改善开场并记住可撤销的本人采访偏好。
**Architecture:** 保持 FastAPI / O2.0 / 原生浏览器音频；音频边界与文字分离；偏好为服务端有来源的结构化历史。
**Tech Stack:** Python、SQLite、JavaScript、AudioContext、WebSocket。
**Spec:** ../specs/2026-10-24-interview-experience-design.md

## Global Constraints

- 真实家人数据和密钥不进入公开仓库；测试仅用虚构资料。
- 原始录音与原始转写不可覆盖。档案间隔离、阅读者不能修改。
- 现有线上数据仍由原站写入；修复需同步到原站后部署。

## Review Focus

- 音频早于文字、同轮多句、旧回复迟到：不能无声或复活已取消的回复。
- 手机音频上下文中断：可恢复且不积压播放陈旧声音。
- 引述别人的话、否定建议、含糊抱怨：不能误存为本人长期偏好。
- 撤销旧偏好与跨档案访问：恢复默认且不能改到别人档案。
- 长辈链接过期、连接失败、结束保存：单屏仍能看懂并可重试。

### Task 1: 连续回合音频

- [x] 在 web/life/audio.test.mjs 与 tests/test_realtime_events.py 添加上述事件乱序、打断、后续回合回归用例并确认失败。
- [x] 修改 realtime.py 的音频边界转发与 audio.js 的播放状态机；保持原始音轨写入。
- [x] 运行回归用例，再用虚构语音做真实多轮协议探测。

### Task 2: 采访策略与有来源偏好

- [x] 添加 preferences.py：extract_preferences(text) 返回有限 key/value；render_preferences(entries) 只拼接受约束的偏好。
- [x] Store 增加 preference history 表，record_preferences(session,turn,text)、preferences(profile)、revoke_preference(profile,id)。来源保留 session/turn；新同 key 版本替换有效状态。
- [x] 增加隔离、误报边界、撤销、原文不变测试，失败后实现。
- [x] prompt.py 与 interviewer.md 使用具体开场和偏好；最终 user turn 保存后记录偏好，下次连接读取。

### Task 3: 单屏长辈界面与整合

- [x] app.js/index.html/style.css 增加简洁模式，当前一句话和大号按钮；保存/错误提示清晰，管理区保留在“读与整理”。
- [x] api.py 为成员和当前 guest 绑定档案提供偏好读取与撤销；reader 不可撤销，guest 不可指定其他档案。
- [x] 本地手机尺寸视觉检查、全套测试、独立审查、公开源扫描。
- [ ] 同步必要修复到旧站，通过原部署脚本发布，检查真实网页与存档兼容性。
