# Windows 本地部署

## 准备

需要 Python 3.13、uv、Node.js 22+ 与 Docker Desktop。

```powershell
Copy-Item .env.example .env
uv sync --all-extras
npm --prefix frontend install --no-audit --no-fund
```

至少配置 `ONCALL_SECRET_MASTER_KEY`。真实模型和飞书按需配置对应环境变量。

## 启动

```powershell
docker compose up -d
docker compose -f compose.local.monitoring.yaml up -d
uv run alembic -c backend/alembic.ini upgrade head
.\scripts\start-all.ps1
```

`start-all.ps1` 启动 API、Agent Worker、Notification Worker、RAG Worker 和前端；不存在本地监控 worker。

RAG Worker 启动时会以 PostgreSQL 中的 active document version 为事实源校准 Milvus：清理已删除/旧版本向量，并自动补建缺失索引。因此 Milvus 数据卷丢失后，只要原始文件和 PostgreSQL 仍在，重启 RAG Worker 即可恢复索引。

手动启动时分别执行：

```powershell
uv run oncall-api
uv run oncall-agent-worker
uv run oncall-notification-worker
uv run oncall-rag-worker
npm --prefix frontend run dev
```

## 验证

```powershell
Invoke-RestMethod http://127.0.0.1:9900/api/health
Invoke-WebRequest http://127.0.0.1:19090/-/ready
Invoke-WebRequest http://127.0.0.1:19093/-/ready
.\scripts\validate-local.ps1
.\scripts\e2e-local.ps1
uv run --no-sync python scripts/rag_e2e/run_e2e.py
```

最后一个命令验证知识库的 HTTP 上传、后台入库、Docling 分块、Embedding、Milvus 混合检索、Rerank 和引用字段；需要已配置真实 Embedding/Rerank。测试文档属于当前工作区共享知识库，测试结束后会清理测试用户、文档版本、原始文件和 Milvus 索引，不会留下测试引用。

访问地址：

- Web：`http://localhost:5173`
- API：`http://127.0.0.1:9900`
- Prometheus：`http://127.0.0.1:19090`
- Alertmanager：`http://127.0.0.1:19093`

## 生产部署

`compose.server.yaml` 已包含 PostgreSQL、Milvus、Prometheus、Alertmanager、API、三个 worker 和前端。设置强随机的 `PROMETHEUS_SCRAPE_TOKEN` 与 `ALERTMANAGER_WEBHOOK_TOKEN`，然后执行迁移和 Compose 启动。

目标服务器只需按“添加服务器”页面执行 Node Exporter、cAdvisor、Collector 和可选 DCGM Exporter 命令；同一服务器只安装一次。
