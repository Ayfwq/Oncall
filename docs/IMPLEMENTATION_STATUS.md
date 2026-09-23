# 实现状态

更新日期：2026-09-22

| 模块 | 状态 | 验证 |
| --- | --- | --- |
| Prometheus file-SD 目标生成 | DONE | 项目/服务器变更自动重写并 reload |
| Prometheus 统一规则 | DONE | 应用、主机、容器、PostgreSQL 规则加载健康 |
| Alertmanager 聚合与恢复 | DONE | group/repeat 策略 + `send_resolved` |
| Alertmanager Webhook | DONE | firing/重复/resolved 实测 |
| Incident 组级去重 | DONE | 两个实例合并为一个 Incident，重复请求幂等 |
| 应用指标转发 | DONE | Prometheus 实测 target `up` |
| PostgreSQL 数字指标 | DONE | Collector → Oncall `/metrics` → Prometheus 实测 |
| Node Exporter | DONE | 真实远程 target `up` |
| cAdvisor | DONE | 安装命令、必填地址、Compose 标签范围规则和采集器不可用告警 |
| Docker 日志与容器诊断 | DONE | Collector 只读工具保留 |
| PostgreSQL 深度诊断 | DONE | 锁链、长事务、慢 SQL、复制等只读工具保留 |
| LLM Prometheus 工具 | DONE | 当前值与历史改读 Prometheus API |
| 旧本地规则引擎 | REMOVED | worker、Detector、规则/状态/样本表及测试已删除 |
| Alembic | DONE | 数据库版本 `0012` |
| 前端 | DONE | 无手写规则页；服务器安装命令和 Prometheus 状态页；生产构建通过 |
| Agent 只读工具 | DONE | 7 个；知识库按当前工作区共享 |
| 后端核心测试 | DONE | `63 passed`（不含独立 RAG 套件） |
| RAG 集成测试 | DONE | `11 passed`，测试数据会清理 |
| 前端测试与构建 | DONE | `7 passed`，生产构建通过 |
| 本地集成测试 | DONE | API + Prometheus + Alertmanager 真实分组链路 |
| 飞书真实发送 | 需外部配置 | 需要 App ID、Secret 与目标会话 |

本地运行入口：Web `http://localhost:5173`，API `http://127.0.0.1:9900`，Prometheus `http://127.0.0.1:19090`，Alertmanager `http://127.0.0.1:19093`。
