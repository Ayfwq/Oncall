# 系统架构

```text
目标服务器
  Node Exporter / cAdvisor / 应用 metrics / 可选 DCGM
                      ↓
                  Prometheus → Alertmanager
                                   ↓ Webhook
Web / 飞书 → FastAPI → Incident + Evidence + Durable Job
                          ↓
                    Agent Worker
                          ↓
                    MCP Client → MCP Server
                                     ├─ Prometheus：指标与历史
                                     ├─ Collector：日志、容器、数据库
                                     ├─ PostgreSQL：告警与上下文
                                     └─ RAG：共享知识库
                          ↓
                 Diagnosis → Outbox → 飞书

外部 MCP Client → FastAPI /api/mcp → 同一套 MCP 工具
RAG Worker → Docling → 分块 → Embedding → Milvus Dense/BM25
```

## 职责

- Prometheus 根据规则和持续时间判警；Alertmanager 聚合、去重和通知恢复。
- Agent 读取实时证据与 SOP，生成诊断；不周期性判警。
- Collector 按需查询容器 stdout/stderr、运行状态和 PostgreSQL 诊断。
- PostgreSQL 保存会话、事件、证据、诊断、任务、审计和 LangGraph checkpoint。
- Milvus 保存可重建索引；知识库由工作区共享。检索流程为 Dense + BM25 → RRF → Rerank → 引用。
- 项目变更自动更新 Prometheus targets/rules，并请求 reload。

## 运行进程

`oncall-api`、`oncall-agent-worker`、`oncall-notification-worker`、`oncall-rag-worker`，加 PostgreSQL、Milvus 及其依赖、Prometheus、Alertmanager、前端。MCP 集成在现有进程中。

内部 MCP 使用进程内传输；外部使用 `/api/mcp` 的 Streamable HTTP，认证和范围见 [MCP.md](MCP.md)。

## 指标来源

| 来源 | 信息 |
| --- | --- |
| Node Exporter | 整机 CPU、内存、磁盘、网络 |
| cAdvisor | 项目容器 CPU、内存 |
| 应用 `/metrics` | 可用性、请求量、5xx、P95、进程指标 |
| Collector → API 数字转发 | PostgreSQL 可用性、连接率、锁等待、长事务、复制延迟 |
| DCGM Exporter（可选） | NVIDIA GPU 指标 |

日志文本、SQL 明细、进程和端口由诊断工具按需读取。阈值以 `integrations/prometheus_api.py` 的规则定义为准。

## 边界

8 个工具只读。项目与事件范围由运行时提供，模型不能任意指定目标。数据库凭证加密存储，模型输入和工具结果按现有规则脱敏。平台管理 API 为单工作区免登录模式，访问控制由部署边界负责。
