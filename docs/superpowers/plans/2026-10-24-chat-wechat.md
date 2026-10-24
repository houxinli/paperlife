# 对话与微信接入实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans or scoped parallel agents. Steps use checkbox syntax.

**Goal:** 更新讲述者体验与真实语音结束，准备腾讯云和原生小程序接入。
**Architecture:** FastAPI/SQLite保持；浏览器与小程序共享REST和有票据的PCM WSS；长辈只读访问绑定档案。
**Tech Stack:** Python, vanilla JS, 微信原生小程序, Docker。
**Spec:** ../specs/2026-10-24-chat-wechat-design.md

## Global Constraints

- 公开仓库不放真实家人资料、凭据、邀请令牌。
- 原文不覆盖，整理内容确认后入书，guest仅绑定本人档案。
- 云购买、实名认证和发布资质不以代码测试替代。

## Review Focus

- “不要结束”、转述过去的停止话语和临时转写不能误停。
- 命名不能成为任意prompt注入，撤销后恢复默认。
- 本人历史接口不能遍历别人的会话、草稿或音频。
- 微信缺失Origin不等于免授权，票据一次性且绑定权限。
- 阅读历史/切换书籍不停止当前录音；音频兼容原有多轮修复。

### 任务1：语音控制和命名

- [ ] 写停止/命名失败回归；实现受约束的检测和来源持久化。
- [ ] realtime.py接入退出事件、明确语句结束和ending/ended生命周期；客户端立即停麦。
- [ ] 验证尾句原文、录音、整理入队、否定与引述拒绝。

### 任务2：本人只读API与聊天UI

- [ ] 写guest跨档案/草稿/失效链接测试，失败后实现history/book/audio接口。
- [ ] 添加聊天历史展开/折叠、过去会话、本人书籍入口；保持家人编辑界面。
- [ ] 更新AGENTS对guest的新授权范围，并验证手机界面。

### 任务3：微信客户端与部署准备

- [ ] 核验微信录音PCM、分帧和播放API，实施原生客户端与可测试的音频转换/票据协议。
- [ ] 实现wx.login接线、档案/邀请选择、聊天与本人书籍；真实AppID缺失不能假成功。
- [ ] 验证并实现native transport源检查，不降低普通浏览器校验。
- [ ] 查询腾讯云/域名和主体门槛，更新下一轮清单；只在具备授权与资质时执行实际采购/发布。

### 任务4：验证与发布

- [ ] 全套检查、独立审查、修复重要问题；同步现有试用站并部署。
- [ ] 公布实际已完成项、可导入小程序代码与未完成的外部手续，保留登录页面供用户继续。
