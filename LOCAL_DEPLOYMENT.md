# Local Deployment — Oncall AI SRE V1.0

本指南覆盖在 Windows 本机从零部署 Oncall 的完整步骤。架构为**模块化单体 + 多进程**：PostgreSQL/Milvus 等基础设施跑在 Docker，Oncall 的 4 个 Python 进程跑在 Windows 宿主机（以便直接观测本机进程、日志与 Docker）。

## 0. 前置条件

| 依赖 | 版本（实测） |
|---|---|
| Python | 3.13（建议用 `uv` 托管） |
| uv | 0.11.8+ |
| Node.js / npm | 22+ / 11+ |
| Docker Desktop | 29+（Linux 容器 / WSL2） |

## 1. 配置

```powershell
Copy-Item .env.example .env
```

编辑 `.env`，至少设置：

```env
ONCALL_SECRET_MASTER_KEY=<随机长密钥>
ONCALL_ADMIN_PASSWORD=<新管理员密码>
```

真实 LLM（可选；缺省为 Mock Provider，可离线联调工程链路）：

```env
ONCALL_MODEL_PROVIDER=openai-compatible
ONCALL_MODEL_BASE_URL=https://<your-endpoint>/v1
ONCALL_MODEL_API_KEY=<secret>
ONCALL_MODEL_NAME=<model>
```

飞书（可选；缺省关闭，不影响其它功能）：

```env
ONCALL_FEISHU_ENABLED=true
ONCALL_FEISHU_APP_ID=<app-id>
ONCALL_FEISHU_APP_SECRET=<secret>
ONCALL_FEISHU_DEFAULT_RECEIVE_ID=<chat-or-open-id>
```

## 2. 启动基础设施

```powershell
docker compose up -d
docker compose ps    # postgres 应为 healthy
```

Compose 仅运行 PostgreSQL / Milvus / etcd / MinIO。

## 3. 后端依赖与数据库

```powershell
uv sync --all-extras
uv run alembic -c backend/alembic.ini upgrade head
uv run oncall-init-admin
```

## 4. 前端

```powershell
cd frontend
npm install --no-audit --no-fund
npm run build
cd ..
```

开发模式（热更新 + `/api` 代理到 9900）：

```powershell
cd frontend
npm run dev     # http://localhost:5173
```

## 5. 启动 4 个进程

```powershell
.\scripts\start-all.ps1
```

等价的手动方式（各开一个终端）：

```powershell
uv run oncall-api
uv run oncall-monitor-worker
uv run oncall-agent-worker
uv run oncall-rag-worker
```

进程职责：

| 进程 | 职责 |
|---|---|
| `oncall-api` | FastAPI 网关（Web/Feishu 入口、33 路由） |
| `oncall-monitor-worker` | 采集 → 32 signals → Detector → Incident → 下发调查 job |
| `oncall-agent-worker` | 消费调查 job → LangGraph Agent → Evidence/Diagnosis |
| `oncall-rag-worker` | 消费知识入库 job → Docling → Chunk → Milvus 索引 |

## 6. 验证

```powershell
# 健康检查
Invoke-RestMethod http://127.0.0.1:9900/api/health
# 期望：database=true, checkpointer=true

# 自动化验收（真实 PostgreSQL + Milvus + 本机集成）
.venv\Scripts\python.exe -m pytest backend/tests -q

# 一键 Mock E2E 冒烟
.\scripts\e2e-local.ps1
```

## 7. 访问入口

```text
Web  http://localhost:5173
API  http://127.0.0.1:9900
```

登录账号：`.env` 中 `ONCALL_ADMIN_PASSWORD` 对应的管理员（用户名 `admin`）。

## 8. 常见问题

- **Windows `ProactorEventLoop` 报错**：`oncall/__init__.py` 已自动切换到 selector loop，无需手动处理。
- **`uv run` 报“文件被占用”**：有 oncall 进程在运行时不要执行 `uv sync`；先停掉进程。
- **Milvus 检索 0 命中**：确认 `docker compose ps` 中 milvus/etcd/minio 均 up，且知识文档状态为 `ready`。
- **Agent 工具返回“需要绑定 Project”**：会话未绑定项目；在新建对话时选择“绑定项目”，或直接在 Incident 会话中追问。

## 9. 已知边界

- V1 为只读诊断：Agent 的 8 个工具均为只读，不提供 restart/kill/write 等破坏性 Action。
- 真实飞书与 AutoGEO 实机采集依赖外部凭证/应用，见 `IMPLEMENTATION_STATUS.md`。
- 生产语义 Embedding/Rerank 需配支持 embedding/rerank 的模型端点；否则 RAG 使用 hash fallback + BM25 + 词法 rerank。
