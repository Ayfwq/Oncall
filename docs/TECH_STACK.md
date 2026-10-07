# 技术栈与代码目录

版本以 `pyproject.toml` / `uv.lock` 和 `frontend/package.json` / `package-lock.json` 为准。

| 层 | 技术 | 用途 |
| --- | --- | --- |
| 后端 | Python 3.13、FastAPI、Uvicorn、Pydantic | HTTP API、配置与数据校验 |
| Agent | LangGraph、MCP Python SDK、HTTPX | 调查编排、工具调用、模型接口 |
| 数据 | PostgreSQL、SQLAlchemy、Alembic | 业务数据、审计、持久化与迁移 |
| 知识库 | Docling、Milvus | 文档分块、Dense/BM25 检索与 RRF；远程 Embedding/Rerank |
| 监控 | Prometheus、Alertmanager | 指标历史、规则、告警聚合 |
| 服务器采集 | Node Exporter、cAdvisor、Collector、可选 DCGM Exporter | 主机、容器、日志、数据库与 GPU 信息 |
| 前端 | Vue 3、TypeScript、Vite、Element Plus、Vue Router | 管理页面与流式会话 |
| 通道 | lark-oapi | 飞书 WebSocket 与消息发送 |
| 部署 | Docker Compose、Nginx、systemd | 服务运行、反向代理、Git 拉取更新 |
| 开发验证 | pytest、Ruff、Vitest；可选 Ragas | 回归、代码检查、前端测试与 RAG 评估 |

PyTorch / torchvision 为 Docling 的锁定依赖；评估依赖放在 `evaluation` extra，不随生产服务安装。

| 目录 | 职责 |
| --- | --- |
| `backend/src/oncall/api` | FastAPI 路由 |
| `backend/src/oncall/agent` | 模型调用、路由、状态与调查图 |
| `backend/src/oncall/mcp` | 工具契约、Server、Client、HTTP 接入与执行审计 |
| `backend/src/oncall/application` | 项目、会话、事件、配置与记忆业务 |
| `backend/src/oncall/collector` | 目标服务器采集服务 |
| `backend/src/oncall/integrations`、`monitoring` | Prometheus 接入与配置探测 |
| `backend/src/oncall/rag`、`evaluation` | 知识库与可选质量评估 |
| `backend/src/oncall/workers`、`jobs`、`channels` | 后台任务、队列与飞书通道 |
| `backend/src/oncall/infrastructure`、`domain`、`bootstrap`、`security` | 数据模型、配置、日志与安全基础 |
| `backend/alembic` | 完整数据库迁移链，支持已有部署升级 |
| `backend/tests`、`frontend/src/*.test.ts` | 正式回归测试 |
| `frontend/src` | 前端页面、组件与 API 客户端 |
| `deploy`、`scripts` | 生产部署和维护入口 |
| `evaluation`、`examples` | 评估数据与项目配置示例 |

`.env`、`data`、`logs` 和 `output` 是本地配置、运行数据或已有产物，不属于源码。

本地开发安装 `uv sync --locked --extra dev` 和 `npm --prefix frontend ci`；RAG 评估另加 `evaluation` extra。维护入口统一为 `start-all.ps1`、`test.ps1`、`validate-local.ps1`、`e2e-local.ps1`、`evaluate-rag.ps1` 和 `setup-feishu.ps1`，均位于 `scripts`。
