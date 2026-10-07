# 项目配置与接入

## 创建服务器

目标服务器默认是 Linux + Docker。页面提供四组可复制命令：

1. Node Exporter：必装，整台服务器安装一次。
2. cAdvisor：必须，整台服务器安装一次。
3. Collector：必装，整台服务器安装一次，用于 Docker 日志、容器诊断和 PostgreSQL 只读诊断。
4. DCGM Exporter：只有 NVIDIA GPU 时安装。

填写对应地址与 Collector Token，通过“测试服务器”后保存。删除配置不会远程卸载采集器；已有项目引用时会拒绝删除。

## 创建项目

项目只填写：

- 项目名称与说明；
- 已绑定服务器；
- 应用 Prometheus `/metrics`；
- PostgreSQL 只读连接串；
- Docker Compose 项目名。

Compose 项目名用于把 cAdvisor 指标、Docker 日志和容器诊断限制在该项目，不会读取同机其他项目。一个服务器可以绑定多个项目，不需要重复安装采集器。

创建前验证服务器、应用指标、日志匹配和数据库连接。创建后默认停用，确认状态后开启；平台自动生成 Prometheus target 与统一规则。

## MCP 查询

平台 Agent 使用 MCP 查询项目指标、日志、容器和数据库信息。Collector 提供采集 API；MCP Server 在平台侧封装这些能力，目标服务器无须增加 MCP 安装项。

外部客户端绑定一个已有会话：项目工具使用该会话绑定的项目，事故上下文还需要绑定事件。当前告警查询覆盖该用户全部项目，知识库属于整个工作区。认证和配置见 [MCP.md](MCP.md)。

## 配置不变量

- Alertmanager Webhook 是唯一事件入口。
- 保存项目不会直接产生告警。
- 测试连接只做 dry-run，不创建 Incident。
- 数据库密码与 Collector Token 加密保存且不回显。
- 删除项目会同时从 Prometheus 目标和规则中移除。
