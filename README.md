# PulseOps · 巡脉

智能运维平台：Prometheus 判警，Agent 通过 MCP 查询证据并诊断，Web/飞书接收结果。单工作区免登录，部署在本机或受控内网。

## 技术与代码

- [技术栈与目录](docs/TECH_STACK.md)
- [系统架构](docs/ARCHITECTURE.md)
- [MCP 接入](docs/MCP.md)
- [文档导航](docs/README.md)

## 本地启动

需要 Python 3.13、uv、Node.js 22+、Docker Desktop。

```powershell
Copy-Item .env.example .env
# 在 .env 设置 ONCALL_SECRET_MASTER_KEY、模型凭证
# 本地监控：ONCALL_PROMETHEUS_URL=http://127.0.0.1:19090
uv sync --locked --extra dev
npm --prefix frontend ci
docker compose up -d
docker compose -f compose.local.monitoring.yaml up -d
uv run --no-sync alembic -c backend/alembic.ini upgrade head
.\scripts\start-all.ps1
```

Web：`http://127.0.0.1:5173`；API：`http://127.0.0.1:9900`。完整配置见 [本地部署](docs/LOCAL_DEPLOYMENT.md)，生产部署见 [deploy/README.md](deploy/README.md)。

## 接入服务器

从“添加服务器”页面复制 Node Exporter、cAdvisor、Collector 安装命令；NVIDIA GPU 可加 DCGM Exporter。每台服务器安装一次。随后配置应用 `/metrics`、Docker Compose 项目名和 PostgreSQL 只读连接，验证后启用项目，见 [项目接入](docs/PROJECT_CONFIGURATION_PLAN.md)。

Collector 镜像由 GitHub Actions 发布至 `ghcr.io/ayfwq/oncall-collector`；首次发布后将包设为 Public。目标服务器不需要项目源码、Python 或 Node。

## 验证

```powershell
.\scripts\test.ps1
.\scripts\validate-local.ps1
.\scripts\e2e-local.ps1
```

`test.ps1` 默认只跑离线测试；完整验证需要基础设施和模型服务。实际结果见 [验证记录](docs/VALIDATION.md)。RAG 质量评估独立安装 `evaluation` extra，见 [RAG 评估](docs/RAG_EVALUATION.md)。

## 运行边界

数字异常由 Prometheus 判断；LLM 按证据诊断。PostgreSQL 保存业务事实，Milvus 是可重建索引。8 个 MCP 工具均只读，项目范围由运行时注入。公网访问需由反向代理控制管理 API 权限。
