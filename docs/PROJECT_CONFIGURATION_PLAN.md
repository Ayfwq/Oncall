# 项目配置与接入

## 创建服务器

目标服务器默认是 Linux + Docker。页面提供四组可复制命令：

1. Node Exporter：必装，整台服务器安装一次。
2. cAdvisor：必须，整台服务器安装一次。
3. Collector：必装，整台服务器安装一次，用于 Docker 日志、容器诊断和 PostgreSQL 只读诊断。
4. DCGM Exporter：只有 NVIDIA GPU 时安装。

填写对应地址并通过“测试服务器”后保存。删除服务器配置不会远程卸载采集器；已有项目引用时会拒绝删除。

## 创建项目

项目只填写：

- 项目名称与说明；
- 已绑定服务器；
- 应用 Prometheus `/metrics`；
- PostgreSQL 只读连接串；
- Docker Compose 项目名。

Compose 项目名用于把 cAdvisor 指标、Docker 日志和容器诊断限制在该项目，不会读取同机其他项目。一个服务器可以绑定多个项目，不需要重复安装采集器。

创建前会一次验证服务器、应用指标、日志匹配和数据库连接。创建后默认停用，确认状态后再开启。启用后 Oncall 自动生成 Prometheus target 与统一规则，不存在项目内手写规则页面。

## 配置不变量

- Alertmanager Webhook 是唯一事件入口。
- 保存项目不会直接产生告警。
- 测试连接只做 dry-run，不创建 Incident。
- 数据库密码与 Collector Token 加密保存且不回显。
- 删除项目会同时从 Prometheus 目标和规则中移除。
