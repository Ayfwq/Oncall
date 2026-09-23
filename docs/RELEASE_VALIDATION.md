# Release Validation — Oncall / 巡脉

最新复验：2026-09-22
环境：Windows 本地开发机，Docker Desktop，PostgreSQL 16，Prometheus 2.55.1，Alertmanager 0.27.0。

本文件只记录当前 Prometheus / Alertmanager 重构后在本机真实执行过的结果。

## 验收结果

| 验收项 | 结果 |
|---|---|
| Python 源码编译 | 通过 |
| 后端核心测试（不含独立 RAG 套件） | `63 passed` |
| RAG 集成测试 | `11 passed` |
| 前端类型检查与生产构建 | 通过 |
| Server / Local Compose 配置解析 | 通过 |
| Prometheus 配置 | `promtool` 通过 |
| Prometheus 规则 | `promtool` 通过，共 14 条（启用 PostgreSQL 与 cAdvisor 时） |
| Alertmanager 配置 | `amtool` 通过 |
| LLM 全链路冒烟 | `E2E SMOKE: PASS` |
| 前端测试 | `7 passed` |
| 数据库迁移 | Alembic `0012`，`alembic check` 无新增迁移 |

## 实际验证命令

```powershell
.\.venv\Scripts\python.exe -m pytest -q backend\tests --ignore=backend\tests\rag
.\.venv\Scripts\python.exe -m pytest -q backend\tests\rag
.\.venv\Scripts\python.exe -m pytest -q -m integration --require-services backend\tests\integration\test_prometheus_alertmanager_live.py
cd frontend
npm run build
cd ..
docker compose -f compose.server.yaml config --quiet
docker compose -f compose.local.monitoring.yaml config --quiet
docker compose -f compose.local.monitoring.yaml exec -T prometheus promtool check config /etc/prometheus/prometheus.yml
docker compose -f compose.local.monitoring.yaml exec -T prometheus promtool check rules /prometheus-data/rules/oncall.yml
docker compose -f compose.local.monitoring.yaml exec -T alertmanager amtool check-config /etc/alertmanager/alertmanager.yml
.\.venv\Scripts\python.exe -u scripts\e2e_smoke.py --timeout 240
```

## 已验证的数据链路

1. 项目和服务器变更会重新生成 Prometheus target 与规则文件，并热加载 Prometheus。
2. Prometheus 负责数字指标阈值和持续时间判断。
3. Alertmanager 按 `project_id + alertname + category` 分组、去重并发送恢复通知。
4. Oncall Webhook 按 fingerprint 幂等入库，同组多个实例只创建一个 Incident。
5. Incident 原子创建证据、会话、飞书 outbox 和调查任务。
6. Agent 可读取 Prometheus 当前值和历史趋势，并按需查询 Collector 日志、容器及 PostgreSQL 诊断信息。
7. 真实 LLM 完成告警诊断、工具轨迹持久化、事故会话追问和恢复流程。

## 当前本地运行状态

- Web：`http://127.0.0.1:5173`
- API：`http://127.0.0.1:9900`
- Prometheus：`http://127.0.0.1:19090`
- Alertmanager：`http://127.0.0.1:19093`
- 实际三个 target（应用、Node Exporter、PostgreSQL 数字指标）均为 `up`。
- 测试数据清理完成：本地只保留工作区用户；知识库文档、原始测试文件和 Milvus 测试索引均为 0。

## 外部联调边界

- 飞书在本地未配置 App ID、Secret 和默认接收目标，因此未执行真实飞书收发；通知 outbox 与 worker 链路已实现。
- cAdvisor 地址是服务器绑定的必填项；项目填写 Docker Compose 项目名后生成容器范围规则和 cAdvisor 不可用告警。
- RAG 独立套件已纳入本次复验并通过；LLM 冒烟和 RAG 测试均真实调用当前 embedding 与 rerank 服务。知识库按当前工作区共享，不按项目隔离。
