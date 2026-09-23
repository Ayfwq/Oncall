# Agent 诊断工具链

LLM 在 Alertmanager 已确认告警之后才运行。首告不等待模型，诊断报告随后追加到同一飞书事件线程。

| Tool | 数据来源 | 作用 |
| --- | --- | --- |
| `query_incident_context` | PostgreSQL | 告警组、标签、注释与已有证据 |
| `query_current_metrics` | Prometheus API | 当前主机、应用、进程、GPU 和数据库数字指标 |
| `query_metric_history` | Prometheus API | 指标时间趋势 |
| `search_logs` | 目标服务器 Collector | 搜索 Docker stdout、聚合错误特征 |
| `query_database_health` | 目标服务器 Collector | 查询连接、锁、长事务、慢 SQL、复制状态 |
| `query_runtime_resources` | 目标服务器 Collector | 查询容器状态、重启、OOM、CPU、内存和进程 |
| `search_knowledge` | Milvus/RAG | 检索内部手册与处置步骤 |

共 7 个工具。除 `search_knowledge` 外，监控工具都必须绑定当前 Incident 的 Project；`search_knowledge` 检索的是当前工作区共享知识库，不按项目隔离。

```text
Alertmanager 告警
  → query_incident_context
  → query_current_metrics / query_metric_history
  → 按异常类型调用日志、数据库或容器诊断
  → 检索知识库
  → Diagnosis + Evidence + 飞书回复
```

日志文本和数据库明细不是 Prometheus 时间序列，因此不会拿来直接触发首条告警。数据库可用性、连接率、锁等待数量等纯数字会被转换为 Prometheus 指标；需要解释原因时再查询详细记录。

知识库查询不是固定的一句话。诊断时会从告警名、异常类型、告警摘要和用户补充内容提取关键词，再追加对应的排查、处理和恢复验证词。例如 `OncallContainerCpuHigh` 会检索“Docker 容器 CPU 使用率过高、高负载、热点进程、排查处理方案、恢复验证”。没有命中知识库时，诊断仍以告警和实时工具结果为依据，不应伪造知识库引用。
