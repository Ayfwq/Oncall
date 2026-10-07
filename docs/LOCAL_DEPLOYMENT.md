# Windows 本地部署

## 准备

需要 Python 3.13、uv、Node.js 22+ 与 Docker Desktop。

```powershell
Copy-Item .env.example .env
uv sync --locked --extra dev
npm --prefix frontend ci
```

在 `.env` 中设置以下配置；模型调用还需要填写 `ONCALL_MODEL_BASE_URL`、`ONCALL_MODEL_API_KEY` 和 `ONCALL_MODEL_NAME`。知识库需要对应 Embedding/Rerank 凭证。

```dotenv
ONCALL_SECRET_MASTER_KEY=<随机长密钥>
ONCALL_PROMETHEUS_URL=http://127.0.0.1:19090
```

本地监控 Compose 映射 Prometheus 到 `19090`、Alertmanager 到 `19093`。若只验证开发链路，可将 `ONCALL_MODEL_PROVIDER=mock`；真实诊断使用 `openai-compatible`。

## 启动

```powershell
docker compose up -d
docker compose -f compose.local.monitoring.yaml up -d
uv run --no-sync alembic -c backend/alembic.ini upgrade head
.\scripts\start-all.ps1
```

`start-all.ps1` 在后台启动 API、Agent Worker、Notification Worker、RAG Worker 和前端；日志位于 `logs/local`。内部 MCP 集成在现有进程中，无须额外配置。

RAG Worker 启动时会以 PostgreSQL 中的 active document version 为事实源校准 Milvus：清理已删除/旧版本向量，并自动补建缺失索引。因此 Milvus 数据卷丢失后，只要原始文件和 PostgreSQL 仍在，重启 RAG Worker 即可恢复索引。

手动启动时分别执行：

```powershell
uv run --no-sync oncall-api
uv run --no-sync oncall-agent-worker
uv run --no-sync oncall-notification-worker
uv run --no-sync oncall-rag-worker
npm --prefix frontend run dev
```

## 验证

离线检查使用 `.\scripts\test.ps1`；以下完整验证需要 PostgreSQL、Milvus、API、监控服务与相关模型接口可用：

```powershell
Invoke-RestMethod http://127.0.0.1:9900/api/health
Invoke-WebRequest http://127.0.0.1:19090/-/ready
Invoke-WebRequest http://127.0.0.1:19093/-/ready
.\scripts\validate-local.ps1
.\scripts\e2e-local.ps1
.\scripts\test.ps1 -Layer rag
```

RAG 统一使用 `backend/tests/rag` 验证 Docling 分块、Embedding、Milvus 混合检索、Rerank、引用与审计；需要配置真实 Embedding/Rerank。测试会清理自身创建的文档和索引。

访问地址：

- Web：`http://localhost:5173`
- API：`http://127.0.0.1:9900`
- Prometheus：`http://127.0.0.1:19090`
- Alertmanager：`http://127.0.0.1:19093`

## 生产部署

生产步骤见 [deploy/README.md](../deploy/README.md)。远程 MCP 配置见 [MCP.md](MCP.md)，当前验证状态见 [VALIDATION.md](VALIDATION.md)。

目标服务器只需按“添加服务器”页面执行 Node Exporter、cAdvisor、Collector 和可选 DCGM Exporter 命令；同一服务器只安装一次。
