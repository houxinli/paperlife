# Paperlife 非虚构写作规则 v1

运行时规则位于 `paperlife/lifebook/writing_rules.py`，版本 `paperlife-biographer-v1`。每个阶段完整带入共同规则与阶段规则；哈希包括版本及全部固定指令，不包括私人素材。模块内置文本，旧站 Docker 只要回补两个 Python 模块即可，不依赖容器外的 policies 路径。

这是针对本产品的原创操作化规则。参考范围为公开文章、规范及出版社介绍/公开节选，并非对整本书的阅读声明或作者认可的标准：

- William Zinsser，[How to Write a Memoir](https://theamericanscholar.org/how-to-write-a-memoir/)：从具体经历取舍，保留个人声音。
- Vivian Gornick，[The Situation and the Story](https://us.macmillan.com/books/9780374528584/thesituationandthestory/) 的出版社介绍与公开节选：区分事件与回望视角；不得据此发明反思。
- Lauren Kessler，[Reporting and writing scenes](https://niemanstoryboard.org/2023/04/20/narrative-journalism-reporting-writing-scenes/)：场景细节来自采访、观察与记录。
- Oral History Association，[Statement on Ethics](https://oralhistory.org/oha-statement-on-ethics/) 与 [Archiving Oral History](https://oralhistory.org/archives-principles-and-best-practices-complete-manual/)：参与核对、保留对应记录、区分审核与分享授权。

## 写作约定

来源、说话者和授权优先于文采。依据 writing_brief/brief 的读者、人称、时间视角、篇幅、方言、素材版本、排除内容和批准旧稿写作。约定缺失时先给短样稿，不能替用户决定正式风格。同一正文视角稳定；不把家人观点或编辑推断写成本人的心声。

## 证据与事实

录音、原始转写、人工校订、家人补充、外部资料与模型草稿分别保存。原始资料不被生成结果覆盖，校订有独立版本；人工校订不能假装逐字原声，没有精确对齐就不编造 turn 或秒数。近似定位应明确说明。

每个具体断言应关联本次授权快照实际存在的 `unit_id` 与其 `text` 中逐字连续的 `quote`，并由应用保存来源、版本及定位。采访者 `context` 只辅助理解，不作为事实依据。引用存在只是机械核对，不能证明引用支持整个段落，更不能证明历史事实已被外部验证。

保留不确定性与冲突；区分亲历、转述、家人后补、外部佐证和尚不确定。姓名音记、同音字、亲属称谓、虚岁/周岁与模糊日期不擅自归一。相对日期不按当前日期补算。不同日期的片段不拼成同一场景；不能确认逐字对白时用转述。外部背景不能推断个人的衣着、天气、心情或行程。

不得编造人物、时间、动作、心理、因果、对白、场景或结局。资料薄就写短概述或返回空稿。权限与使用范围由服务端执行；模型不授予访问权限、不决定分享、不确认入书。保留原文不能替代产品对合法撤回、隐私删除的处理。

## 可读性

围绕一个有材料支持的问题、关系、阶段或经历选材。保留本人惯用词、节奏、幽默、犹疑与有辨识度的方言，轻量注释，不制造年龄与地域刻板口吻。用有依据的行动、物件与选择推进；文学性来自取舍、顺序、长短句和节奏。删除无贡献套话，不堆砌感动；允许矛盾、遗憾、未想通和开放结尾，不强加和解、宽恕、成长或人生意义。

## 五阶段与验收

1. `extract`：整理素材卡与待核实问题，不写故事；冲突各自记录，保留引用与版本关系。
2. `outline`：给范围、核心问题、片段顺序、取舍理由和尚缺材料；主题不是新事实。
3. `write`：按大纲写短而诚实的正文草稿，每段事实有证据覆盖；不足可返回空稿。
4. `audit`：逐条检查人物、日期、因果、场景、动作、心理、对白与不确定性。只支持部分、冲突、无支持都退回；不以引文存在代替事实审核。
5. `polish`：改善结构、指代、重复与节奏，保留事实、证据与本人表达；不加细节和情感结论，之后重新审核。

所有结果先 draft。应用完成精确引用检查、独立事实审核、素材新鲜度检查后，仍须本人或获授权编辑显式确认。生成失败保留原始材料与确认版；素材变化生成新草稿，不能把重试当作依校订重写。供应商未提供模型快照时只记录请求模型名，不宣称精确复现。当前不宣称某模型写作最好。

## 供应商与运行边界

默认 extractor/writer 都明确使用现有豆包实时接口的文本模式，模型 `1.2.1.1`，密钥来自 `store.provider()`。独立 DeepSeek/Ark 的私有配置只保存固定供应商、请求模型与密钥；空密钥仅保留同供应商旧值，切换供应商不继承密钥。管理员权限必须由调用 `save_config` 的 HTTP 路由检查。

固定地址依据 [DeepSeek 官方文档](https://api-docs.deepseek.com/api/create-chat-completion/) 与 [火山方舟官方 API Explorer](https://api.volcengine.com/api-explorer/?action=ChatCompletions&groupName=%E5%AF%B9%E8%AF%9D%28Chat%29+API&serviceCode=ark&tab=2&version=2024-01-01) 核对。不接受自定义 URL，不跟随重定向，不静默更换模型或供应商。密钥写入私有运行目录 `editor-models.json`，文件权限 0600、原子替换与进程间锁保护。

单次输入 JSON 最多 240,000 UTF-8 字节，输出最多 160,000 字节，HTTP 响应体最多 1,000,000 字节；总时限 120 秒，HTTP 最多 8192 输出 token。豆包实时协议沿用已开通调用，未假设其支持 Chat Completions token 参数，以时限和增量输出字节上限约束。任何限制均明确失败，不截断素材继续生成。错误仅返回固定错误码，不记录供应商正文或凭据。
