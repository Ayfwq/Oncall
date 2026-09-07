# Oncall 实现状态矩阵

生成日期：2026-08-19

状态定义：

- **DONE（Real E2E）**：源码实现 + 本地真实环境实测通过。
- **DONE（Code + Offline）**：源码实现 + 编译/单元/契约测试通过，真实外部依赖未联调。
- **BLOCKED**：源码实现，但真实联调被外部条件阻塞（无凭证/无应用）。

| 模块 | 状态 | 实测口径 |
|---|---|---|
| FastAPI / Session Auth / Request-ID | DONE（Real E2E） | 12 个 HTTP 集成测试 + 401/404 全覆盖 |
| Project 配置 | DONE（Real E2E） | 服务器绑定 + 两个远程 URL + /test dry-run |
| Conversation / Message | DONE（Real E2E） | 创建/搜索/归档/删除 + 重启持久化 |
| Context Summary（防失忆） | DONE（Real E2E） | 45 条消息触发真实 LLM 摘要，二次压缩 no-op |
| Unified OncallAgent（LangGraph） | DONE（Real E2E） | PostgreSQL checkpointer + 真实 LLM + 流式 token |
| 4 Read-only Tools | DONE（Code + Offline） | 当前指标、指标历史、服务健康、知识库 |
| Monitoring Engine | DONE（Real E2E） | 多项目真实采集 + detector 状态机 |
| 远程 Python 28/34 signals | DONE（Code + Offline） | 28 个基础远程指标；配置 GPU 后增加 6 个 GPU 指标；采集路径已做 allow-list |
| Python 快速接入 | DONE（Code + Offline） | 创建页/`POST /api/projects/onboard/python` 绑定服务器、健康检查和 Prometheus，并自动生成基础规则 |
| Prometheus 应用指标 | DONE（Code + Offline） | 多源抓取时间隔离、请求加权错误率、RPS/P95/P99/可用性 |
| 历史基线规则 | DONE（Code + Offline） | threshold/baseline/hybrid，持久化正常窗口，异常值不回写基线 |
| NVIDIA GPU 主机指标 | DONE（Code + Offline） | 配置 DCGM URL 后加入 6 项；无 GPU 时不进入规则 |
| 采集 Integrations | DONE（Code + Offline） | 只保留 server/service/prometheus 三条远程采集路径 |
| Rule State Machine | DONE（Real E2E） | hysteresis + PG 持久化 + fresh-session 重启恢复 |
| Incident Manager | DONE（Real E2E） | FIRING→Resolved→再异常 E2E + severity 升级重调查 |
| Durable Job Queue | DONE（Real E2E） | PG lease/reclaim + 并发删除不崩 worker（Core UPDATE 幂等） |
| Evidence / Diagnosis | DONE（Real E2E） | 真实 LLM 报告 + Incident 被删时的 FK 兜底 |
| ToolRun / RetrievalTrace | DONE（Real E2E） | trace 持久化 + RAG citation refs |
| Docling Ingestion | DONE（Real E2E） | 3 份 .md 真实解析入库 |
| Milvus Dense + BM25 | DONE（Real E2E） | collection 33 entities + BM25 函数 + drop→reindex 恢复 |
| RRF + remote rerank | DONE（Real API） | RRF 单元 + 硅基流动 rerank API 通过 |
| Knowledge API | DONE（Real E2E） | 上传→Job→Index→检索→删除 E2E |
| Feishu WebSocket 入站 | DONE（Code + Offline） | parser/contract + import OK |
| Feishu Outbox 出站 | DONE（Code + Offline） | retry/cooldown/message-link 单测 |
| Incident Follow-up Routing | DONE（Code + Offline） | 路由逻辑 + 契约 |
| Vue Chat UI | DONE（Real E2E） | vue-tsc + vite build + dev 代理透传 |
| 流式输出（token + 结构化事件） | DONE（Real E2E） | 真实 LLM 308 token chunk + tool/rag/diagnosis 事件 |
| Incident Trace UI / Knowledge UI / Settings UI | DONE（Code + Offline） | 构建通过 + API 契约 |
| Docker Compose | DONE（Real E2E） | 4 容器实际启动 |
| Alembic schema | DONE（Code + Offline） | 版本 0004，增加项目环境、基线窗口和自适应规则表 |
| Restart Persistence | DONE（Real E2E） | 全进程重启后会话/检查点/记忆不丢 |
| Real LLM E2E | DONE（Real E2E） | 见 RELEASE_VALIDATION.md |
| Real Feishu E2E | **BLOCKED** | 无飞书 App ID/Secret/receive_id |
| AutoGEO 实机采集 | **BLOCKED** | 本机不存在 `D:\GEO` 应用 |
| 生产 Embedding / Rerank | DONE（Real API） | 硅基流动 BGE-M3 + bge-reranker-v2-m3 API 通过；待服务启动后重建唯一索引 |

## 结论

所有在**本仓库/本机可单方面完成**的实现与验收均已 DONE；剩余 2 项 BLOCKED 依赖外部条件（飞书凭证、AutoGEO 应用），属显式剩余项而非遗漏实现。

当前实现状态：**可运行**。

## 2026-08-19 增量实现

- 项目配置：增加后端 DTO 约束、指标/目标依赖校验、阈值方向与范围校验、前端 JSON/表单统一校验及采集反馈。
- 飞书 Outbox：增加 `sending` 租约、过期租约回收和数据库并发抢占，降低多 Worker 重复发送风险。
- 验收工程：新增 `offline/local/integration/rag` 测试分层、严格 marker、外部检查超时和 `--required` 发布门禁。
- 仍需外部联调：飞书真实消息闭环、AutoGEO 实机采集；Embedding/Rerank 已通过真实 API，服务启动后需重建新索引。
