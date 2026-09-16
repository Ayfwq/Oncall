# Release Validation — PulseOps · 巡脉智能运维平台

初始验证：2026-08-19；最新复验：2026-09-16
环境：Windows 本地开发机（Docker Desktop / WSL2 Linux 容器）

> 本文件只记录**在本机真实执行过**的命令与结果。未执行的项（真实飞书、AutoGEO 实机、生产 Embedding/Rerank）在文末单列，不做任何“视同通过”的推断。

## 最新复验（2026-09-16）

- 后端全量测试：**106 passed in 68.72s**（0 失败）。
- 前端：`vue-tsc -b && vite build` 通过。
- 在线 API：数据库、LangGraph Checkpointer 和 `/api/settings/tool-contracts` 正常，工具契约为 8 个。
- Collector：`/health`、数据库诊断和 `/v1/runtime/diagnose` 正常；实际读取 Docker 容器 CPU、内存、重启和 OOM 字段。
- API、Monitoring Worker、Agent Worker、Notification Worker、RAG Worker 和 Collector 已按当前代码重启。
- 并发删除保护：Agent 审计写入与 RAG 入库在父对象被删除时安全降级，不再阻断 Worker。
- 首次 Incident 的事件、会话、飞书 outbox 和调查任务同事务提交；空知识库不会调用远程 rerank。

## 1. 环境与依赖

| 项 | 实测 |
|---|---|
| Python | 3.13.13（uv 托管） |
| uv | 0.11.8 |
| Node / npm | v24.16.0 / 11.17.0 |
| Docker / Compose | 29.4.1 / v5.1.3 |
| PostgreSQL | 18（Docker 容器，healthy） |
| Milvus | v2.6.3（Docker 容器） |
| etcd / MinIO | v3.5.18 / 最新（Milvus 依赖，up） |

```powershell
uv sync --all-extras
# → resolved 185 packages, installed 165（含 langgraph 1.2.x / docling / lark-oapi / asyncpg / pymilvus）
docker compose up -d
# → postgres(healthy) / milvus / etcd / minio 全部 up
uv run alembic -c backend/alembic.ini upgrade head
# → 33 张业务表 + 当前 Alembic head（0004）
# → 数据库结构就绪；PulseOps 自动准备本地工作区
```

## 2. 真实 LLM（OpenAI-compatible）

模型：`deepseek-v4-flash`（用户提供的 OpenAI-compatible 端点；API Key 不写入本文件/仓库）。

实测项：

| 用例 | 结果 |
|---|---|
| `POST /v1/chat/completions` 自述 | 200，正常返回 |
| 工具调用（`检查宿主机 CPU 和内存`） | 当前 Agent 使用 `query_current_metrics` 读取真实指标并作答（旧验证记录中的 `query_host_metrics` 已移除） |
| 无项目绑定时的诚实降级 | 工具返回“需要绑定 Project”，Agent 明确说明“未获取到数据”，**未编造指标** |

## 3. 自动化测试（pytest，真实 PostgreSQL + Milvus + 本机集成）

```powershell
.venv\Scripts\python.exe -m pytest backend/tests -q
```

结果：**106 passed in 68.72s**（0 失败）。

覆盖范围（均为真实执行）：

- 覆盖 API 集成：Projects CRUD、Conversations、Incidents（trigger/investigate/resolve/trace）、Knowledge（upload/job/reindex/delete）、Settings、404 和畸形 UUID。
- 覆盖监控采集与规则：Host/Process/Docker/PostgreSQL/HTTP/日志、基线与 hysteresis 状态机、Incident 生命周期、重启恢复、severity 升级重新调查和首次告警事务一致性。
- 覆盖 RAG：Milvus collection、RRF、citation、ToolRegistry→RetrievalTrace、embedding/rerank 契约和参数化检索回归；空知识库跳过远程 rerank。
- 覆盖 Agent/渠道契约：8 个工具、参数校验、项目隔离、飞书线程/通知重试、路由、脱敏和 Mock 安全降级。

## 4. RAG 端到端（Docling → Milvus → RRF → Rerank → Citation）

```powershell
uv run --no-sync python scripts/rag_e2e/run_e2e.py
# → 3 份 SOP 上传，rag_ingest job 全部 done，文档 ready
# → 7 个查询 top-1 全部命中正确文档与正确章节
uv run --no-sync pytest backend/tests/rag -q
# → 12 passed
uv run --no-sync python scripts/rag_e2e/reindex_recovery.py
# → drop collection → reindex ×3 → 33 entities 恢复，检索正常
```

历史说明：本节记录的是旧端点时期的本地回退验证；当前代码已删除 hash embedding 和词法 rerank，改为硅基流动 BGE-M3 + bge-reranker-v2-m3，详见当前配置与外部检查结果。

## 5. Agent 流式输出（Token + 结构化事件）

真实 LLM 下 `POST /api/conversations/{cid}/messages:stream` 实测事件序列：

```text
status → tool_started → tool_finished → … → token ×N → final
```

- `token`：真实 LLM 逐 token 流式正文（实测 308 个 chunk）。
- `tool_started/tool_finished/rag_retrieved/diagnosis_ready`：工具/检索/诊断结构化状态事件。
- 前端 `ChatView.vue` 已消费 token 做渐进渲染，并显示工具状态行。

## 6. 上下文自动压缩（防“失忆”）

实测：45 条消息触发 `compact_if_needed` → 真实 LLM 生成摘要（387 字符）→ 后续 context 携带「摘要 + 最近 30 条原文」→ 二次压缩为 no-op（增量不重复）。

## 7. 服务重启持久化 E2E

实测：种入“我叫王小明”→ 停止并重启全部 4 进程 → 会话消息完整保留（2 条）→ 追问“我叫什么名字”正确答出“王小明”。

## 8. 前端

```powershell
cd frontend
npm install --no-audit --no-fund   # 140 packages
npm run build                       # vue-tsc -b && vite build → exit 0
npm run test                        # exit 0（--passWithNoTests）
```

Vite dev server（`http://localhost:5173`）代理 `/api` → `http://127.0.0.1:9900`，`/api/health` 透传验证通过。

## 9. 未执行 / 明确阻塞项

| 项 | 状态 | 原因 |
|---|---|---|
| 真实飞书 E2E（WebSocket 入站 + 出站卡片） | 代码已实现、import 通过，**未真实联调** | 未提供飞书 App ID / Secret / receive_id |
| AutoGEO 实机采集 | 代码路径就绪，**未真实联调** | 本机不存在 `D:\GEO`（AutoGEO 应用未安装） |
| 生产 Embedding / Rerank | 已切换 | 硅基流动 BGE-M3 + bge-reranker-v2-m3 真实 API 已通过；唯一索引需在服务启动后重建 |

## 10. 结论

- 真实 LLM / RAG / Monitoring / PostgreSQL / Milvus / 前端 / 流式 / 记忆压缩 / 重启持久化 **均已在本地真实环境跑通**。
- 真实飞书与 AutoGEO 实机采集需要外部凭证/应用，属显式阻塞项，已在 `IMPLEMENTATION_STATUS.md` 标注。
- 当前实现可运行；真实飞书/AutoGEO 为后续外部联调，非本仓库内可单方面完成的验收。
