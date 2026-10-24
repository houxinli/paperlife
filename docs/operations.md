# 部署与迁移

## 新环境

复制`.env.example`到服务器私有`.env`，填入域名和必要配置。创建`private/bootstrap-password`（至少12字符，0600），目录及文件须让容器uid10001可读；不要使用示例密码。DNS指向主机，防火墙只开放必要的80/443及受限SSH。确认compose网段不与已有网络冲突。

```sh
docker compose config --quiet
docker compose up -d --build
curl --fail https://你的域名/health
```

`.env`、private目录和data卷不进入Git。服务端8000不直接对公网开放；反向代理固定地址用于可信转发IP。正式依赖版本/镜像建议在目标云私有镜像仓库锁定并扫描；本轮compose不配置不明来源镜像加速器。

## 从旧应用迁移

迁移工具默认仅统计，不写数据。用旧应用数据卷的私有快照在可信主机执行：

```sh
python scripts/migrate.py --source-life /private/source/life --source-users /private/source/users.sqlite3 --destination /private/paperlife-new
```

确认停旧站写入，重新取快照，再加`--apply --source-stopped`。目标目录必须不存在；只保留life资料和被引用的用户基本身份/密码摘要，不复制交易表、已有登录会话或供应商密钥。音频逐个核对SHA256；链接hash和180天期限保留。迁移后家人重新登录，长辈使用完整的专属链接。

验收：人物和成员数量、权限拒绝、录音可播放、转写/版本/草稿/问题数量、链接重复使用及撤销、自动整理作业。旧域名转向新域名必须实机确认fragment保留，不能把无token的页面地址当原分享链接。切换前保留旧快照；出现问题先停新站写入，再决定回滚，不能让双端写入分叉。

## 备份与容量

使用`backup.py --writes-stopped`创建一致私有快照，包含SQLite和原始文件。备份包含敏感内容及运行时秘密，须加密、限制访问，并在隔离目录恢复演练。不能把“备份命令成功”当恢复成功。原声PCM体积较大，定期检查剩余磁盘；大规模生产应实现对象存储后端和生命周期策略，不能把本地volume称作COS/OSS/TOS。

## 延迟与质量

`GET /api/life/sessions/{id}/quality`受档案权限保护。connect_ms、response_first_audio_ms分别是连接耗时和ASR结束事件到首个音频的服务器间隔；不含手机判停/播放缓冲。疑点标记是启发式，不是模型置信度，不会自动改原文。即时追问目前由采访提示词驱动，尚未实现强制插入每个疑点的流程。

## 已知边界

单进程worker/ticket状态；扩容前需共享会话状态。手机号/微信登录会创建普通身份，不自动合并现有家庭账号；新用户需被授权，身份绑定/迁移确认界面待后续。语音模型和后台文本生成暂时共用豆包语音服务；专门文本模型适配是下一阶段。小程序原生录音端与发布审核不在本轮完成范围。
