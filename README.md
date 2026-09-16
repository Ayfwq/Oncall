# PulseOps · 巡脉智能运维平台

PulseOps（巡脉）是一个本地优先的智能运维平台。当前实现包含真实 LLM、RAG、Monitoring、PostgreSQL、Milvus、前端、流式输出、上下文压缩与重启持久化（见 [`docs/RELEASE_VALIDATION.md`](docs/RELEASE_VALIDATION.md)）。平台采用单工作区免登录模式；真实飞书与 AutoGEO 实机采集依赖外部凭证/应用，属显式阻塞项（见 [`docs/IMPLEMENTATION_STATUS.md`](docs/IMPLEMENTATION_STATUS.md)）。

> 验收口径：本仓库只把「真实执行过」的项记为通过；未执行的外部联调（飞书、AutoGEO、生产 Embedding/Rerank）单列标注，不做推断。

## 架构基线

详细文档入口：[`docs/README.md`](docs/README.md)；按当前代码重绘的架构图：[`docs/pulseops-architecture-v2.svg`](docs/pulseops-architecture-v2.svg)；Agent 详解：[`docs/PULSEOPS_AGENT_GUIDE.md`](docs/PULSEOPS_AGENT_GUIDE.md)。

监控指标分层、Python 快速接入和告警规则设计见 [`docs/MONITORING_DESIGN.md`](docs/MONITORING_DESIGN.md)；Agent 工具和诊断编排见 [`docs/AGENT_TOOL_FLOW.md`](docs/AGENT_TOOL_FLOW.md)。

- **模块化单体 + 多运行进程**：`api` / `monitor-worker` / `agent-worker` / `rag-worker`。
- **统一 OncallAgent**：LangGraph StateGraph；`CHAT / INVESTIGATE / FOLLOW_UP / DEEP` 共用同一套 RAG、Tools、Memory 和模型网关。
- **远程 Python Monitoring Engine**：41 个基础 signal + 可选 6 个 NVIDIA GPU signal；覆盖服务器、健康检查、应用、Python 进程、Docker 日志和 PostgreSQL。规则命中先作为 Detector 信号，同项目 120 秒内的可操作信号合并为一个 Incident；CPU、内存等共享资源压力仅作为诊断证据。
- **8 个只读 Agent Tool**：事故上下文 / 当前指标 / 指标历史 / 服务健康 / 日志搜索 / 数据库诊断 / 容器运行资源 / 知识库。Agent 按异常类型选择必要工具，不会把 47 个指标拆成 47 个工具，也不会每次机械调用全部工具。
- **完整 RAG 主链**：Docling → Canonical JSON/Markdown → HybridChunker → Dense + Milvus BM25 → RRF → Rerank → Citation；Milvus 只是可重建索引。
- **持久化**：PostgreSQL 保存业务事实、会话、消息、Incident、Evidence、Diagnosis、Tool/RAG Trace、durable jobs/outbox；LangGraph 使用 PostgreSQL checkpointer。
- **飞书**：自建应用机器人 + WebSocket 入站 + PostgreSQL Outbox 出站；主动 Incident 报告可绑定 Incident Conversation，用户回复后进入 `FOLLOW_UP`。
- **Web**：Vue 3 + Vite + TypeScript，ChatGPT 式多会话；服务重启后由 PostgreSQL 恢复历史会话。
- **访问方式**：单工作区免登录，建议仅绑定本机或可信内网；如需外网访问，应由反向代理、网络策略或其他边界设施负责访问控制。
- **安全边界**：只读调查，不提供 restart/kill/write-SQL 等破坏性 Action Tool；未来动作必须显式人工确认。

## 运行拓扑

```text
Web / Feishu
      │
      ▼
FastAPI Gateway ───── Conversation / Incident / Knowledge API
      │
      ├──────────────► OncallAgent (LangGraph)
      │                    │
      │                    ├── 4 Read-only Tools
      │                    ├── RAG
      │                    └── PostgreSQL Checkpoint
      │
Monitoring Worker ─► Snapshot ─► Detector ─► Incident ─► Durable Job
                                                   │
Agent Worker ◄─────────────────────────────────────┘
      │
      └── Evidence / Diagnosis ─► PostgreSQL Outbox ─► Feishu

RAG Worker ─► Docling ─► Chunk ─► Embedding ─► Milvus Dense/BM25 Index
```

## Windows 本地首次运行

### 1. 准备

需要：Python 3.13、`uv`、Node.js 22+、Docker Desktop。

```powershell
Copy-Item .env.example .env
```

至少修改：

```env
ONCALL_SECRET_MASTER_KEY=<随机长密钥>
```

### 2. 启动基础设施

```powershell
docker compose up -d
```

Compose 只运行 PostgreSQL/Milvus/etcd/MinIO；远程 Python 项目由目标服务器的 Node Exporter、可选 DCGM Exporter 和项目自身的 `/health`、`/metrics` 提供观测数据。

目标服务器还需运行轻量 Collector，以只读方式发现 Docker stdout 日志并执行预定义 PostgreSQL 诊断：

```bash
ONCALL_COLLECTOR_TOKEN=<随机令牌> docker compose -f deploy/collector.compose.yaml up -d --build
```

### 3. Python

```powershell
uv sync --all-extras
uv run alembic -c backend/alembic.ini upgrade head
```

### 4. 前端

```powershell
cd frontend
npm install
npm run build
cd ..
```

### 5. 验证与启动

```powershell
.\scripts\validate-local.ps1
.\scripts\start-all.ps1
```

开发地址：

```text
Web  http://127.0.0.1:5173
API  http://127.0.0.1:9900
Collector http://127.0.0.1:9910
```

### 6. Mock E2E

服务和 3 个 worker 启动后：

```powershell
.\scripts\e2e-local.ps1
```

该脚本验证 Conversation、Mock Agent、Tool、Synthetic Incident、Diagnosis、Follow-up、Recovery 等主链路。开发模式下才开放 synthetic Incident API。

## 真实模型 / 飞书联调

项目不要求把任何 Secret 写入源码。你只需在目标机器 `.env` 配置：

```env
ONCALL_MODEL_PROVIDER=openai-compatible
ONCALL_MODEL_BASE_URL=https://<your-endpoint>/v1
ONCALL_MODEL_API_KEY=<secret>
ONCALL_MODEL_NAME=<model>

ONCALL_EMBEDDING_BASE_URL=https://<your-endpoint>/v1
ONCALL_EMBEDDING_API_KEY=<secret>
ONCALL_EMBEDDING_MODEL=<embedding-model>
ONCALL_EMBEDDING_DIMENSION=<dimension>

ONCALL_RERANK_BASE_URL=<rerank-endpoint>
ONCALL_RERANK_API_KEY=<secret>
ONCALL_RERANK_MODEL=<rerank-model>

ONCALL_FEISHU_ENABLED=true
ONCALL_FEISHU_APP_ID=<app-id>
ONCALL_FEISHU_APP_SECRET=<secret>
ONCALL_FEISHU_DEFAULT_RECEIVE_ID=<chat-or-open-id>
```

然后执行：

```powershell
.\scripts\validate-local.ps1 -External
```

这会检查真实 LLM、Embedding、Rerank 和飞书凭证可用性。**模型 API Key 没有由本项目生成者持有或测试，这一层必须由目标机器完成。**

## 文档入口

- `docs/TECHNICAL_SELECTION_REPORT.docx`：技术选型报告。
- `docs/DEVELOPMENT_DESIGN.docx`：完整开发设计文档。
- `docs/RELEASE_VALIDATION.md`：本次真实执行过的验证清单（唯一验收口径）。
- `docs/IMPLEMENTATION_STATUS.md`：模块实现/验证矩阵（DONE 或显式 BLOCKED）。
- `docs/LOCAL_DEPLOYMENT.md`：Windows 本地从零部署步骤。
- `docs/ARCHITECTURE.md`：架构说明。
- `docs/PROJECT_CONFIGURATION_PLAN.md`：项目创建、配置校验与后续版本化规划。
- `examples/autogeo-project.json`：AutoGEO 示例 Project 配置。

## 不可违反的运行规则

1. Monitoring 基础检测必须确定性执行，不能让 LLM 每 5 分钟判断是否异常。
2. PostgreSQL 是业务事实源；Milvus 是可重建 RAG 索引。
3. Conversation 与 Incident Memory 必须独立持久化；LangGraph checkpoint 不是业务事实库。
4. Agent 结论必须基于 Evidence；RAG 文档不是实时系统事实。
5. 当前实现不允许 destructive tool；未来 Action Tool 必须分级并人工确认。
6. Project scope 由 runtime 注入 Tool，不能让模型任意指定目标 Project。
7. 真实 Release 必须通过 `docs/LOCAL_HANDOFF.md` 的目标机器验收门禁。

## 分层验证

不要把缺少 PostgreSQL/Milvus/模型凭证的环境误判为代码失败。使用分层测试：

```powershell
.\scripts\test.ps1 -Layer offline
.\scripts\test.ps1 -Layer local
.\scripts\test.ps1 -Layer integration
.\scripts\test.ps1 -Layer rag
```

外部依赖检查默认只诊断；发布门禁使用：

```powershell
uv run python scripts/check_external.py --required
```
