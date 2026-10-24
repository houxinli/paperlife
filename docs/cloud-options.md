# 火山与腾讯：当前可核实的价格和备案条件

核验：2026-10-24。没有购买任何资源。以下云主机报价不含豆包模型调用费用，不记录实际账户账单。

| 候选 | 地域与规格 | 三个月 | 月均 | 口径 |
|---|---|---:|---:|---|
| 腾讯轻量通用型 | 成都，2核4GB，90GB SSD，6Mbps，800GB/月流量 | 240元 | 80元 | 已登录控制台配置报价，未下单 |
| 火山共享型 `ecs.e-c1m2.large` | 广州，2核4GiB，90GiB极速SSD PL0，BGP公网6Mbps，Linux | 927.36元 | 309.12元 | 官方公开计算器参考，未核实账号专属优惠及库存 |
| 同一火山机型降低带宽 | 其余不变，公网改为2Mbps | 402.36元 | 134.12元 | 作为低带宽参考，不能冒充同等6Mbps方案 |

火山数据来自[官方ECS计算器](https://www.volcengine.com/pricing?product=ECS&tab=2)，页面已明确最终以实际订单为准。计算器显示的折扣不代表已确认当前账号的首购资格或续费同价。CPU型号、虚拟化保障、盘单位与网络计费方式也不是完全相同，因此这是资源规模比较，不是性能基准。

对当前小规模家庭应用，腾讯已核实套餐更便宜、费用更容易预估。火山按固定带宽的EIP和按流量的EIP是不同计费模式；如果使用量很低，按流量值得另行核价，但不能把“带宽上限6Mbps”当成包含腾讯的800GB/月流量包。[火山EIP计费](https://docs.volcengine.com/docs/ElasticIPAddress/Billingoverview-37?lang=zh)说明只按出方向计量及IP费用规则。

特价云主机到期可能按官网价续费，盘/IP等后付费资源也可能独立继续出账，见[火山ECS计费FAQ](https://docs.volcengine.com/docs/ecs/Billing-FAQs?lang=zh)。没有采用第三方宣传的“几十元一年”作为完整可部署报价。

## 备案

| 条件 | 腾讯云 | 火山引擎 |
|---|---|---|
| 基础资源 | 中国大陆、包年包月、公网IP | 中国大陆、包年包月、公网IP |
| 购买时长 | 累计不少于3个月 | 一次性购买不少于3个月，或续费至要求 |
| 提交时剩余有效期 | 至少1个月，按当前资源条件核验 | 大于24小时 |
| 单实例限额 | 按资源类型和当期后台核验 | 最多5个网站/APP |

来源：[腾讯备案资源条件](https://cloud.tencent.com/document/product/243/18908)、[火山准备备案资源](https://docs.volcengine.com/docs/Record/Preparingtofilecloudresources?lang=zh)。不要临近资源到期才安排备案，办理期间仍要保持资源有效。

两家都需要符合要求的域名、实名和备案主体。服务器机房所在省不等于备案所属省；火山一个账号只能备案一个主体，另一主体应使用对应实名账号。详见[火山主体填写FAQ](https://docs.volcengine.com/docs/Record/FillintheFAQforsubjectinformationandserviceinformation?lang=zh)、[备案限制](https://docs.volcengine.com/docs/Record/Limitationdescription?lang=zh)。

已有网站ICP备案不等于小程序已备案；微信服务类目、主体资格和AI相关材料也不会因换云免除。同一家云与语音服务可能方便管理，但没有证据保证端到端延迟必然降低。

## 语音费用如何核实

实际消费应从费用中心账单明细与语音用量页面读取，并限定产品、模型、项目和日期。分别记录已出账金额、实际支付、优惠/资源包抵扣、剩余额度和最新用量日期；账户余额或充值额不能直接当作项目消费。最近调用可能尚未出账。

参考：[语音控制台用量FAQ](https://docs.volcengine.com/docs/DoubaoVoice/ConsoleUsageFAQ?lang=en)、[费用中心](https://console.volcengine.com/finance/account-overview/)。实时聊天、后台文本整理和开发验证可能共用同一个语音服务凭据；如果账单没有按用途拆分，不把总额说成仅家人实际聊天费用。实际账户数据只作私有交付，不提交到公开代码库。
