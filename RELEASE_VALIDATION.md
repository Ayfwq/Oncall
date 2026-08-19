# Release Validation — Oncall AI SRE V1.0

生成时间：2026-08-19
环境：Windows 本地开发机（Docker Desktop / WSL2 Linux 容器）

> 本文件只记录**在本机真实执行过**的命令与结果。未执行的项（真实飞书、AutoGEO 实机、生产 Embedding/Rerank）在文末单列，不做任何“视同通过”的推断。

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
# → 33 张业务表 + alembic_version=0001
uv run oncall-init-admin
# → 管理员账号就绪
```

## 2. 真实 LLM（OpenAI-compatible）

模型：`deepseek-v4-flash`（用户提供的 OpenAI-compatible 端点；API Key 不写入本文件/仓库）。

实测项：

| 用例 | 结果 |
|---|---|
| `POST /v1/chat/completions` 自述 | 200，正常返回 |
| 工具调用（`检查宿主机 CPU 和内存`） | Agent 正确返回 `query_host_metrics` 工具调用并基于真实返回作答 |
| 无项目绑定时的诚实降级 | 工具返回“需要绑定 Project”，Agent 明确说明“未获取到数据”，**未编造指标** |

## 3. 自动化测试（pytest，真实 PostgreSQL + Milvus + 本机集成）

```powershell
.venv\Scripts\python.exe -m pytest backend/tests -q
```

结果：**76 passed in 55.31s**（0 失败）。

覆盖范围（均为真实执行）：

- **API 集成（12）**：Auth、Projects CRUD、Conversations、Incidents（trigger/investigate/resolve/trace）、Knowledge（upload/job/reindex/delete）、Settings、401/404、畸形 UUID 全量 404。
- **监控（36）**：6 类 Integration 真实采集（Host CPU/内存/磁盘、Process、Docker 容器状态、PostgreSQL 连接/慢查询/锁、HTTP 服务健康、日志文件统计与正则扫描）；32 baseline signals；Detector 状态机（NORMAL→PENDING→FIRING→RECOVERING→NORMAL + hysteresis + RECOVERING 再异常回 FIRING）；Incident 生命周期 + fingerprint + 重启恢复 + severity 升级重新调查。
- **RAG（12）**：Milvus collection 实体、RRF、citation 结构、ToolRegistry→RetrievalTrace 持久化、embedding 契约、7 个参数化检索回归。
- **单元（16）**：信号契约、tool 契约、hysteresis、fingerprint、RRF、脱敏、Mock 安全决策、飞书线程解析、通知重试/cooldown 策略、路由契约。

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

说明：Dense 向量当前为**确定性 hash fallback**（该 LLM 端点只提供 chat/speech/image，没有 embedding/rerank 模型）；BM25 全文召回 + RRF + 词法 rerank 是真实机制，实测 7/7 章节命中。

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
| 生产 Embedding / Rerank | **不可用** | 当前 LLM 端点无 embedding/rerank 模型；RAG 走 hash fallback + BM25 + 词法 rerank |
| Settings readiness 的 embedding `configured` 判定 | 与 hash fallback 事实不一致 | 仅按 `embedding_api_key` 判定；记录为已知小缺口 |

## 10. 结论

- 真实 LLM / RAG / Monitoring / PostgreSQL / Milvus / 前端 / 流式 / 记忆压缩 / 重启持久化 **均已在本地真实环境跑通**。
- 真实飞书与 AutoGEO 实机采集需要外部凭证/应用，属显式阻塞项，已在 `IMPLEMENTATION_STATUS.md` 标注。
- 版本定级：**Oncall V1.0 Release**（真实飞书/AutoGEO 为后续增量联调，非本仓库内可单方面完成的验收）。
