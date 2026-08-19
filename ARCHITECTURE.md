# Architecture Baseline

## 不可违反的边界

1. Detector 是确定性代码；LLM 只负责问答、调查、解释与诊断。
2. PostgreSQL 是 Conversation/Message/Incident/Evidence/Diagnosis 的唯一业务事实源。
3. LangGraph Checkpoint 仅用于 Agent Runtime 恢复。
4. Milvus 是可重建索引；raw + canonical document + PostgreSQL metadata 才是知识事实源。
5. Model 不得提供/切换 `project_id`；ToolExecutor 从 runtime context 注入 scope。
6. V1 只注册 READ tools。
7. 同一 Incident 持续 FIRING 不应每轮重新调用 Agent；首次、升级、新证据、人工 DEEP、stale recheck 才重新调查。
8. 所有外部凭证通过 SecretBox/环境配置管理，不写入代码和日志。

## Runtime

- `oncall-api`: REST/SSE/Web auth；交互 Agent。
- `oncall-monitor-worker`: 项目巡检、Detector、Incident。
- `oncall-agent-worker`: `incident_investigate` durable jobs。
- `oncall-rag-worker`: Docling ingestion / indexing。

跨进程通过 PostgreSQL `background_jobs` / `notifications` 协调，V1 不引入 Redis/Celery/Kafka。
