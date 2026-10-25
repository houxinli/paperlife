# Paperlife · 拾光人生之书

每个人的人生记录应用：通过专属链接和 AI 语音聊天，从当下小事慢慢聊到想留下的记忆；本人或获授权亲属校对素材、核实问题，再将跨多次聊天的章节草稿确认收入人生之书。默认配置四川话输出，实际口音识别与播报效果需真机验收。

公开仓库仅包含通用代码、文档和虚构测试。真实录音、转写、家人姓名、手机号、账号库、采访链接和密钥必须留在私有运行环境。公开代码不代表公开家人的资料。

## 已实现

- 独立 FastAPI + 手机网页；不依赖交易系统。
- 档案成员隔离、可撤销的长辈免密码链接、本人只读历史与已确认书籍、原声与原始转写留存、版本化校对。
- 豆包 O2.0 实时语音、四川话/普通话配置、聊天策略、日常开场、识别疑点旁路记录、服务器阶段延迟指标。
- 聊天后自动提取素材；独立整理/写作模型配置、跨会话章节、大纲/正文/核对/润色流程、来源快照与校订版本。人工确认后入书，旧确认版不会被自动覆盖。
- 双方称呼记忆、可展开的聊天记录、语音结束采访；只从明确最终发言记忆偏好。
- 独立账号登录；微信 code 兑换与腾讯短信 OTP 服务端适配（默认关闭，需真实资质与凭据）。原生微信客户端位于 `miniprogram/`，尚未完成真实AppID、开发者工具编译、真机和上架审核验证。
- Docker、CI、私有备份和离线迁移工具。

## 本地运行

需要 Python 3.12、Node 22+。Node只用于测试，没有前端构建依赖。

```sh
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/admin.py --data data
COOKIE_SECURE=false .venv/bin/uvicorn paperlife.main:app --host 127.0.0.1 --port 8000
```

打开 `http://127.0.0.1:8000/life/`。管理员登录后配置语音服务；配置前不会伪造采访成功。

```sh
PYTHON_BIN=.venv/bin/python ./scripts/check.sh
```

## 部署与下一轮

- [详细设计、云选型与采访架构](docs/design.md)
- [跨会话章节写作、版本和接口](docs/chapter-writing.md)
- [非虚构写作规则与资料来源](policies/biographer.md)
- [用户下一轮待办包](docs/next-round.md)
- [部署、迁移与回滚](docs/operations.md)
- [聊天与记录约定](policies/interviewer.md)
- [双端复用、v1契约与维护边界](docs/architecture.md)
- [火山/腾讯费用与备案对比](docs/cloud-options.md)
- [个体工商户远程登记核验](docs/business-registration-notes.md)

目前提供的是可运行应用与部署准备。不能把本地测试当作国内网络、小程序审核、短信投递或四川话可懂度的验收；也不能把文本来源匹配当作事实真实性证明。原生全双工切换、独立ASR二遍、私有对象存储/PostgreSQL后端仍是后续工作。
