# 腾讯云与微信上线核验

核验日期2026-10-24，动态价格和域名状态可能变化。本页只记录观察结果，不能代替控制台审核或合规判断。

## 主体与微信

微信[当前服务类目](https://developers.weixin.qq.com/miniprogram/product/material/)将AI问答、AI创作列在非个人主体的深度合成类目中；[CloudBase说明](https://docs.cloudbase.net/ai/release/algorithm-filing)明确个人主体无法申请。CloudBase[官方答疑](https://tcb.cloud.tencent.com/blog/2026/05/15/hy3-preview-plan)说明持营业执照的个体工商户可申请。具体材料仍以实际主体和模型服务链路审核为准。

[微信认证专门文档](https://developers.weixin.qq.com/doc/oplatform/Third-party_Platforms/2.0/product/weapp_wxverify.html)明确个人可认证，认证提供搜索和分享等能力；旧介绍页部分表述滞后，不能简单说个人永远无法认证。认证不能解除AI类目限制，当前收费需后台订单核实。

[微信基础登录](https://developers.weixin.qq.com/miniprogram/dev/framework/open-ability/login.html)与[获取手机号](https://developers.weixin.qq.com/miniprogram/dev/framework/open-ability/getPhoneNumber.html)是两种能力，后者要求已认证非个人主体。Paperlife不把手机号作为基础登录前提。

小程序备案按[微信备案指引](https://developers.weixin.qq.com/miniprogram/product/record/record_guidelines.html)、[材料示例](https://developers.weixin.qq.com/miniprogram/product/record/record_material.html)办理；不要与“通过腾讯云备案小程序办理后端网站备案”混为一谈。

## 域名

| 域名 | 核验结果 |
|---|---|
| paperlife.cn | CNNIC WHOIS无匹配；腾讯云中国站查询实际显示可加购、首年33元，尚不等于注册成功。 |
| paperlife.ai | [Identity Digital RDAP](https://rdap.identitydigital.services/rdap/domain/paperlife.ai)返回404；腾讯云中国站显示不支持注册。 |
| paperlife.com | [Verisign RDAP](https://rdap.verisign.com/com/v1/domain/paperlife.com)显示已注册。 |
| getpaperlife.com | [Verisign RDAP](https://rdap.verisign.com/com/v1/domain/getpaperlife.com)返回404，仅为查询时未注册观察；未取得腾讯最终报价。 |

[工信部域名体系](https://domain.miit.gov.cn/chinayu.jsp)公开名单中有`.cn`、`.com`，没有`.ai`、`.app`；[腾讯云备案要求](https://cloud.tencent.com/document/product/243/18905)要求合资格后缀、注册商和实名信息。此次国内主域名优先`.cn`。

## 云资源与上线边界

腾讯云[备案资源条件](https://cloud.tencent.com/document/product/243/18908)和购买页要求大陆公网实例、累计购买不少于3个月，备案时还需满足剩余时长条件。已配置的候选实例为成都2核4GB/90GB SSD/6Mbps/800GB月流量，3个月240元；自动续费关闭，套餐外用量和模型调用不包含在此价格中。用户已选择暂不购买，仅保留配置；没有创建实例或导入公钥。

正式国内域名开放以前先做私有预发布。家人的真实档案在计划好的停写窗口迁移，备份和音频校验必须先完成，不能通过GCP长期中转来假装已完成国内数据迁移。

[网信办2026年9月公告](https://www.cac.gov.cn/2026-09/14/c_1791136833136332.htm)说明调用已备案模型的应用还涉及属地登记；实际适用范围需结合产品和属地核实。生成内容展示/导出还需核对[生成合成内容标识办法](https://www.cac.gov.cn/2025-03/14/c_1743654684782215.htm)。这与单纯购买国内服务器是不同工作。
