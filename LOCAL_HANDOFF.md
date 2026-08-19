# Oncall AI SRE - CodeX 本地联调与 Release 收口手册

版本：v1.0.0-rc1  
日期：2026-08-18

## 1. CodeX 的任务边界

不要重新设计架构。除非遇到当前设计在真实依赖上不可运行，否则应保持：统一 OncallAgent、独立 Monitoring Engine、PostgreSQL 事实源、Milvus 可重建索引、8 个 V1 只读 Tool、飞书 Incident Follow-up、多会话持久化。

CodeX 的主要职责是：在用户真实 Windows 开发机安装依赖、启动基础设施、执行 migration/build/test、根据实际 SDK/API 报错做兼容修正、接入用户真实模型/飞书配置、对 AutoGEO 做实机监测配置，并把所有 Release Gate 跑绿。

## 2. 不要要求用户把 Secret 写进源码

让用户在根目录 `.env` 填写真实 Secret。不要提交 `.env`。

必填安全项：

```env
ONCALL_SECRET_MASTER_KEY=<random-secret>
ONCALL_ADMIN_PASSWORD=<strong-password>
```

真实 LLM（OpenAI-compatible 示例）：

```env
ONCALL_MODEL_PROVIDER=openai-compatible
ONCALL_MODEL_BASE_URL=https://.../v1
ONCALL_MODEL_API_KEY=...
ONCALL_MODEL_NAME=...
```

Embedding/Rerank 若与 LLM 不同，再填写对应 `ONCALL_EMBEDDING_*` / `ONCALL_RERANK_*`。飞书填写 `ONCALL_FEISHU_*`。

## 3. 第一次本地安装

```powershell
Copy-Item .env.example .env
# 编辑 .env

docker compose up -d
uv sync --all-extras
uv run alembic -c backend/alembic.ini upgrade head
uv run oncall-init-admin

cd frontend
npm install
npm run build
cd ..
```

如果真实安装得到的 SDK API 与源码不兼容，优先按 `pyproject.toml` 的兼容范围修正源码；不要随意换掉架构组件。

## 4. 静态/单元验证

```powershell
.\scripts\validate-local.ps1
```

必须至少满足：

```text
compileall PASS
pytest PASS
ruff PASS
frontend production build PASS
PostgreSQL SELECT 1 PASS
Milvus connectivity PASS
```

如果依赖已稳定安装，生成并提交真实锁文件：

```powershell
uv lock
npm install   # 生成 package-lock.json
```

RC1 ZIP 没有伪造这些 lockfile，因为构建沙箱无法访问 registry。

## 5. Mock 主链 E2E

保持：

```env
ONCALL_ENV=development
ONCALL_MODEL_PROVIDER=mock
```

启动：

```powershell
.\scripts\start-all.ps1
```

另开终端：

```powershell
.\scripts\e2e-local.ps1
```

必须验证：Login → Project → Conversation → Mock Agent/Tool → Synthetic Incident → Agent Worker → Diagnosis → Incident Trace → FOLLOW_UP → Recovery → 数据仍可查询。

## 6. 重启持久化测试（Release Gate）

这是用户的硬需求，必须真实执行：

1. 新建至少两个 Conversation，并在其中一个产生 5+ 条消息。
2. 创建一个 synthetic Incident，得到 Diagnosis，并在 Incident Conversation 追问一次。
3. 记录 Conversation/Incident ID。
4. 停止 API、monitor-worker、agent-worker、rag-worker；保留 PostgreSQL/Milvus 数据卷。
5. 重新启动四个进程。
6. 确认旧 Conversation 列表、Messages、Incident、Diagnosis、Evidence、Trace 全部存在。
7. 在旧 Conversation 再发消息，确认 LangGraph thread/checkpoint 与业务历史可以继续工作。

## 7. RAG 真实 E2E（Release Gate）

准备至少三份小型运维手册，例如 CPU 高、PostgreSQL connection refused、Playwright/Chromium 异常。

验证：

```text
上传 → durable RAG job → Docling → canonical JSON/Markdown → HybridChunker
→ Embedding → Milvus dense + BM25 → RRF → Rerank → top-k
→ search_knowledge → Citation(page_range/version/chunk) → RetrievalTrace
```

至少做 10 个固定 query 的回归集合；检查专有词、错误码和自然语言均能合理命中。Embedding 模型或维度变化时，不要复用旧索引；提升 `ONCALL_KNOWLEDGE_INDEX_VERSION` 后重建。

## 8. 真实 LLM E2E（Release Gate）

配置真实模型后：

```powershell
.\scripts\validate-local.ps1 -External
```

然后测试：

- 常规运维问题能够选择 RAG；
- “当前 CPU 怎样”能够选择实时 Tool；
- Incident 调查能够产生 Evidence-driven Diagnosis，而不是凭空编根因；
- Tool 参数符合 8 个 Tool JSON schema；
- 超预算/重复调用能停止；
- FOLLOW_UP 能加载原 Incident/Evidence，并按需再查实时数据；
- 无证据时输出 unknowns/低 confidence，而不是伪造事实。

## 9. 飞书真实 E2E（Release Gate）

配置自建应用机器人，启用 WebSocket 事件接收。测试：

1. 用户直接私聊/群聊机器人普通运维问题。
2. Synthetic/真实 Incident 触发后，outbox 主动发送完整 interactive Incident Card。
3. 回复该告警消息，确认根据 `message_id/root_id` 找回原 `conversation_id/incident_id`。
4. Agent 进入 FOLLOW_UP，不创建无关新 Incident。
5. 临时断网/模拟 5xx，确认 Notification 指数退避，达到 max attempts 后 dead。
6. cooldown 内同类 diagnosis 不重复推；resolved 消息不应被 cooldown 吞掉。
7. 入站 Agent 失败时，ProcessedChannelEvent 为 failed，可由平台重试，不提前标 processed。

## 10. AutoGEO 实机接入

不要只按通用指标猜测。根据 AutoGEO 实际运行方式填写 Project：

- 目标 Python executable + cwd/cmdline substring；
- Playwright/Chromium 进程匹配；
- 实际日志文件路径；
- PostgreSQL Docker container；
- 只读数据库账号；
- FastAPI health endpoint；
- 适合 AutoGEO 的监控规则阈值。

先用 Project 的“测试采集”。该接口是 dry-run，不推进正式日志 cursor。确认数据正确后再启用 monitoring。

## 11. 后端 API Release Gate

当前代码定义 33 个路由，已有静态路由契约测试，但在本地必须再用真 PostgreSQL 做 HTTP 级测试。至少覆盖：Auth、Projects、Conversations/Messages、Incidents/Trace、Metrics、Knowledge/Jobs、Settings、dev synthetic incident（development only）。

推荐补充 `httpx.AsyncClient + ASGITransport` 或启动实际 API 的 integration tests；所有写接口测试后要检查 DB 实际行与 owner scope。

## 12. 必须补的真实环境测试

在目标机器新增/执行：

```text
backend/tests/integration/test_api_postgres.py
backend/tests/integration/test_job_queue_postgres.py
backend/tests/integration/test_langgraph_checkpoint.py
backend/tests/integration/test_rag_milvus.py
backend/tests/integration/test_monitoring_real_sources.py
backend/tests/e2e/test_restart_persistence.py
```

飞书和付费模型可作为 opt-in live tests，通过环境变量开启，默认 CI 不运行。

## 13. Release 判定

只有以下全部成立才将版本从 `1.0.0rc1` 改为 `1.0.0`：

```text
[ ] uv lock / package-lock.json 基于目标机器真实 registry 生成
[ ] Python unit/contract tests 全绿
[ ] PostgreSQL API integration tests 全绿
[ ] Milvus/Docling RAG E2E 全绿
[ ] Vue production build 全绿
[ ] Mock E2E 全绿
[ ] Restart persistence 全绿
[ ] Real LLM Tool/RAG/Diagnosis E2E 全绿
[ ] Real Feishu send/receive/follow-up/retry 全绿
[ ] AutoGEO 实机采集与至少一次异常→报告→恢复闭环通过
[ ] 无 destructive tools；Secret 不泄漏；production config validation 通过
```

任何一项未通过，都继续保留 RC 标记，不能宣称 GA Release。
