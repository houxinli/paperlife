# 老人家庭访谈：提问依据与阅读书目

2026-10-24。用于一般家庭口述史，不把年长讲述者等同于失智患者；不以停顿、短答或方言识别困难推断认知状态。本页将资料原则与产品实现分开，未声称已完成真人效果实验。

## 提问如何落地

1. **从容易定位的信息起步。** 先问合适称呼、成长地点等基本背景，再进入具体场景。“最难忘”“一生最重要”需要先筛选和比较大量经历，放在已有故事之后更合适。前半条来自 [NPS 访谈指南](https://www.nps.gov/articles/000/oral-history-resources-interviewing.htm) 与 [Interview an Elder](https://www.nps.gov/articles/interview-an-elder.htm)；后半条是产品设计推导。
2. **一次问一件事，留时间。** 不把姓名、生日、住址、父母工作连成同一句。参考 [UNC 实用口述史指南第11页](https://sohp.org/wp-content/uploads/2023/11/Revised_2023_A-Practical-Guide-to-Oral-History_November2023.docx.pdf)。
3. **跟随实际回答。** 提到赶场，就先追问一次赶场的具体情景；不要为了完成提纲忽略已经开始的故事。[OHA 最佳实践](https://oralhistory.org/best-practices/)强调主动倾听与讲述者定义主题的权利。
4. **不预设家庭关系。** 先问同住的人，再依实际回答问父母、祖辈或其他照顾者。避免“爸爸一定很疼您吧”等引导。这是将 [Leicester 提问指导](https://le.ac.uk/emoha/what-is-oral-history/how-to-do-oral-history/interviewing/questions)应用到家庭场景的设计。
5. **沉默不等于没听懂。** Leicester 提醒部分年长讲述者需要较慢的节奏和思考停顿。沉默也可能表示不想继续，不能仅按几秒钟判定其含义。
6. **尊重原有语言与拒答。** 四川话中的旧称和词汇先保留；不确定时中性澄清，不能自动改写事实。[OHA 官方中文译本](https://www.oralhistory.org/wp-content/uploads/2022/01/New-Chinese-OHA-Principles-and-Best-Practices.pdf)保护表达方式及拒绝某些话题的权利。
7. **反馈优先于提纲。** “这个你问过了”就换新细节；“我还没说完”就继续听；“不想说”停止该话题。明确停止意愿应结束访谈。以上是 OHA 合作性访谈原则的产品化推导；当前按钮能确定结束，语音停止能力需独立验证，不能把提示词承诺等同于应用已停止录音。

## 开场示例

每次只发出当前一问，不朗读整张清单；讲述者已经展开故事时，跟随故事。

1. “我该怎么称呼您？”
2. “您小时候主要在哪个地方长大？”
3. “您那时候住的房子是什么样的？”
4. “当时跟您一起生活的有哪些人？”
5. 只有对方提到母亲时：“您刚才说到妈妈，她那时候平常忙些什么？”
6. 从回答里选一个具体细节继续问，不能凭空加入织布、种地等生活事实。

回访结合已存记录给一个小入口，避免每次从身份核对重新开始。敏感关系、童年创伤、精确年月不是必须完成的字段。

## 采访者如何调整

当轮：端到端模型收到讲述者反馈后改变问法和方向。跨轮次采访：仅把明确命令式表达提取为有限、档案内的结构化偏好，附原始转写来源，允许撤销。没有每回合额外模型调用。

这不是模型权重训练，也不是自动重写所有人的系统规则。引述别人说的话、假设句和含糊抱怨不自动持久保存。“换个话题”未指定新方向时，模型当轮换题，应用不猜一个长期偏好。已结束的原文和人生事实不会被偏好覆盖。

## 书目

| 书 | 本项目最值得参考的部分 |
|---|---|
| Elizabeth Keating, *The Essential Questions: Interview Your Family to Uncover Stories and Bridge Generations*（2022） | 最贴近家庭访谈，把父母祖辈视为特定社会和年代中的儿童、青年与独立个体。已核验[出版社介绍](https://www.penguinrandomhouse.com/books/690817/the-essential-questions-by-elizabeth-keating-phd/)，未取得完整目录。 |
| Barbara W. Sommer、Mary Kay Quinlan, *The Oral History Manual*，第4版（2024） | 访谈计划、执行、保存与使用的全流程，以及示例表格。已核验[出版社介绍](https://www.bloomsbury.com/us/oral-history-manual-9781538181683/)，不编造章节号。 |
| Valerie Raleigh Yow, *Recording Oral History: A Guide for the Humanities and Social Sciences*，第3版（2014） | [出版社目录](https://www.bloomsbury.com/us/recording-oral-history-9780759122666/)中的第4章提问技巧、第6章伦理、第7章访谈关系、第10章家庭研究。 |

书目依据公开介绍和可见目录，未声称已阅读受版权保护的完整书籍。
