# Oncall V1.0 Release 实现状态矩阵

生成日期：2026-08-19

状态定义：

- **DONE（Real E2E）**：源码实现 + 本地真实环境实测通过。
- **DONE（Code + Offline）**：源码实现 + 编译/单元/契约测试通过，真实外部依赖未联调。
- **BLOCKED**：源码实现，但真实联调被外部条件阻塞（无凭证/无应用）。

| 模块 | 状态 | 实测口径 |
|---|---|---|
| FastAPI / Session Auth / Request-ID | DONE（Real E2E） | 12 个 HTTP 集成测试 + 401/404 全覆盖 |
| Project 配置（含 Secret 加密） | DONE（Real E2E） | CRUD + /test dry-run + DB 密码加密保留 |
| Conversation / Message | DONE（Real E2E） | 创建/搜索/归档/删除 + 重启持久化 |
| Context Summary（防失忆） | DONE（Real E2E） | 45 条消息触发真实 LLM 摘要，二次压缩 no-op |
| Unified OncallAgent（LangGraph） | DONE（Real E2E） | PostgreSQL checkpointer + 真实 LLM + 流式 token |
| 8 Read-only Tools | DONE（Real E2E） | 真实 Host/DB/Docker/Service/Log/Process 数据 |
| Monitoring Engine | DONE（Real E2E） | 多项目真实采集 + detector 状态机 |
| 32 baseline signals | DONE（Real E2E） | 数量/契约 + 真实 collect 全 32 信号 |
| 6 Integrations | DONE（Real E2E） | Host/Process/Log/Docker/Postgres/HTTP 实机数据 |
| Rule State Machine | DONE（Real E2E） | hysteresis + PG 持久化 + fresh-session 重启恢复 |
| Incident Manager | DONE（Real E2E） | FIRING→Resolved→再异常 E2E + severity 升级重调查 |
| Durable Job Queue | DONE（Real E2E） | PG lease/reclaim + 并发删除不崩 worker（Core UPDATE 幂等） |
| Evidence / Diagnosis | DONE（Real E2E） | 真实 LLM 报告 + Incident 被删时的 FK 兜底 |
| ToolRun / RetrievalTrace | DONE（Real E2E） | trace 持久化 + RAG citation refs |
| Docling Ingestion | DONE（Real E2E） | 3 份 .md 真实解析入库 |
| Milvus Dense + BM25 | DONE（Real E2E） | collection 33 entities + BM25 函数 + drop→reindex 恢复 |
| RRF / fallback rerank | DONE（Real E2E） | RRF 单元 + 词法 rerank + 7/7 章节命中 |
| Knowledge API | DONE（Real E2E） | 上传→Job→Index→检索→删除 E2E |
| Feishu WebSocket 入站 | DONE（Code + Offline） | parser/contract + import OK |
| Feishu Outbox 出站 | DONE（Code + Offline） | retry/cooldown/message-link 单测 |
| Incident Follow-up Routing | DONE（Code + Offline） | 路由逻辑 + 契约 |
| Vue Chat UI | DONE（Real E2E） | vue-tsc + vite build + dev 代理透传 |
| 流式输出（token + 结构化事件） | DONE（Real E2E） | 真实 LLM 308 token chunk + tool/rag/diagnosis 事件 |
| Incident Trace UI / Knowledge UI / Settings UI | DONE（Code + Offline） | 构建通过 + API 契约 |
| Docker Compose | DONE（Real E2E） | 4 容器实际启动 |
| Alembic initial schema | DONE（Real E2E） | 33 表 + 版本 0001 |
| Restart Persistence | DONE（Real E2E） | 全进程重启后会话/检查点/记忆不丢 |
| Real LLM E2E | DONE（Real E2E） | 见 RELEASE_VALIDATION.md |
| Real Feishu E2E | **BLOCKED** | 无飞书 App ID/Secret/receive_id |
| AutoGEO 实机采集 | **BLOCKED** | 本机不存在 `D:\GEO` 应用 |
| 生产 Embedding / Rerank | **BLOCKED** | 当前 LLM 端点无 embedding/rerank 模型；RAG 走 hash fallback + BM25 + 词法 rerank |

## 结论

所有在**本仓库/本机可单方面完成**的实现与验收均已 DONE；剩余 3 项 BLOCKED 全部依赖外部条件（飞书凭证、AutoGEO 应用、带 embedding/rerank 能力的模型端点），属显式剩余项而非遗漏实现。

版本定级：**Oncall V1.0 Release**。

## 2026-08-19 增量实现

- 项目配置：增加后端 DTO 约束、指标/目标依赖校验、阈值方向与范围校验、前端 JSON/表单统一校验及采集反馈。
- 飞书 Outbox：增加 `sending` 租约、过期租约回收和数据库并发抢占，降低多 Worker 重复发送风险。
- 验收工程：新增 `offline/local/integration/rag` 测试分层、严格 marker、外部检查超时和 `--required` 发布门禁。
- 仍需外部联调：飞书真实消息闭环、AutoGEO 实机采集、生产 Embedding/Rerank；这些不能仅靠本地源码单方面完成。
