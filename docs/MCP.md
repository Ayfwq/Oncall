# MCP 工具接入

PulseOps 的 8 个只读工具统一通过官方 Python MCP SDK 提供。目标服务器仍使用现有 Collector；Prometheus 与应用指标采集方式不变。

## 内部调用

```text
OncallAgent → MCPToolClient → MCP Server → DiagnosticTools
                                          ├─ Prometheus
                                          ├─ Collector
                                          ├─ PostgreSQL
                                          └─ RAG
```

平台内部使用 SDK 的进程内 MCP 传输，执行初始化、工具发现和调用，不增加网络请求、端口或独立服务。项目、事件和 AgentRun 由运行时注入，不属于模型可修改的工具参数。工具服务端保留参数校验、15 秒执行超时、只读范围、脱敏以及 ToolRun/RetrievalTrace 审计。

工具定义与执行位于 `mcp/contracts.py`、`mcp/backend.py`；协议、客户端和 HTTP 入口分别位于 `mcp/server.py`、`mcp/client.py`、`mcp/http.py`。

## 外部客户端

外部入口为已有 API 的 `/api/mcp`，采用 Streamable HTTP（无状态、JSON 响应）。生产前端 Nginx 已转发 `/api/`，因此可以使用平台域名下的同一路径。无需改动目标服务器。

在平台 `.env` 中配置：

```dotenv
ONCALL_MCP_ACCESS_TOKEN=<独立的随机长令牌>
ONCALL_MCP_CONVERSATION_ID=<已有会话 UUID>
ONCALL_MCP_ALLOWED_HOSTS=["ops.example.com"]
```

会话 UUID 可以从 `GET /api/conversations` 返回的 `id` 获取。选择绑定了目标项目的会话；事故上下文工具还需要该会话绑定事件。未绑定项目的会话仅能查询当前用户告警及工作区知识库，其余工具返回范围缺失错误。外部调用在执行前重新检查会话、项目归属及事件范围，归档或删除会话后停止访问；每次执行创建独立 `mode=mcp` 的 AgentRun，保留工具审计。

当前告警工具的范围是会话用户的全部项目，知识库范围是当前工作区；令牌持有者可以访问这些数据。当前版本提供一个外部绑定会话，不允许客户端通过工具参数选择其他项目或事件。平台内部 Agent 的范围仍随其当前会话或调查事件变化。

修改允许的 Host 后重启 API。Host 列表填写客户端请求中的域名或 IP，可附带端口；Nginx 使用 `$host` 转发时通常不含端口。原生客户端可省略 Origin；浏览器调用的 Origin 仅允许 `ONCALL_WEB_ORIGIN`。凭证经独立 Bearer Token 校验，未配置令牌或绑定会话时外部入口返回 503，错误令牌返回 401。

本地配置后重启 API；生产环境运行 `sudo bash deploy/update.sh`。直接连接本机 API 时，URL 为 `http://127.0.0.1:9900/api/mcp`，允许 Host 可写 `127.0.0.1:9900`；完整部署见 [deploy/README.md](../deploy/README.md)。

在支持远程 MCP 和自定义请求头的客户端中填写：

```text
Transport: Streamable HTTP
URL: https://ops.example.com/api/mcp
Authorization: Bearer <ONCALL_MCP_ACCESS_TOKEN>
```

该入口使用静态 Bearer 认证，不提供 OAuth 自动登录。平台现有免登录管理 API 继续依赖可信内网或反向代理访问控制；对公网开放时应由现有边界设施隔离管理 API，并为 MCP 使用 HTTPS。

## 工具

| MCP 工具 | 数据来源 |
| --- | --- |
| `query_active_alerts` | 当前会话用户的 PostgreSQL 告警 |
| `query_incident_context` | 绑定事件的 PostgreSQL 上下文 |
| `query_current_metrics` | Prometheus 当前指标 |
| `query_metric_history` | Prometheus 历史指标 |
| `search_logs` | Collector 容器日志 |
| `query_database_health` | Collector PostgreSQL 诊断 |
| `query_runtime_resources` | Collector 容器运行资源 |
| `search_knowledge` | 当前工作区知识库 |

所有工具标注 `readOnlyHint=true`、`destructiveHint=false`。返回同时包含 JSON 文本和结构化 ToolResult；失败返回 `isError=true` 及稳定错误码。不提供 restart、kill 或写 SQL 工具。

验证结果见 [VALIDATION.md](VALIDATION.md)。
