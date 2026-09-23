# Prometheus 告警设计

## 职责边界

告警主链只有一条：

```text
数字指标 → Prometheus 规则与持续时间 → Alertmanager 合并/去重
         → Oncall Webhook → Incident → LLM 诊断 → 飞书
```

- Prometheus 是唯一的数字指标异常判断入口。
- Alertmanager 按 `project_id + alertname + category` 聚合，控制首次等待、重复间隔和恢复通知。
- Oncall 不重复计算阈值；只保存 Alertmanager 事件、创建事件并启动诊断。
- LLM 不决定是否告警。它读取告警与 Prometheus 指标，再按需查询日志、数据库明细、容器状态和知识库。

旧的 `monitor-worker`、本地 Detector、基线状态机、规则表、样本表和游标表已经删除。

知识库只在诊断阶段使用。Agent 根据具体告警名、异常类型、摘要和用户补充内容动态生成检索词，并按需搜索当前工作区共享的 SOP；它不是项目专属知识库，也不是实时指标来源。没有知识库文档或没有检索命中时，报告应明确说明，不生成虚假的引用。

## 数据来源

| 数据域 | 目标服务器组件 | Prometheus 用途 | LLM 用途 |
| --- | --- | --- | --- |
| 整机 | Node Exporter，每台服务器一次 | CPU、内存、磁盘、网络、采集器存活 | 查看故障上下文 |
| 容器 | cAdvisor，每台服务器一次 | 按 Compose 项目标签过滤容器 CPU/内存 | Collector 查询重启、OOM、进程和端口 |
| 应用 | Python 项目自己的 `/metrics` | 可达性、请求量、5xx、P95、进程指标 | 查询当前值与历史趋势 |
| PostgreSQL | Oncall API 将 Collector 的只读数字结果转换为 `/metrics` | 可用性、连接率、锁等待、长事务、复制延迟 | Collector 进一步查询锁链、慢 SQL 和活动会话 |
| Docker stdout | Collector，每台服务器一次 | 不直接进入 Prometheus | 搜索错误文本、异常堆栈和错误特征 |

同一台服务器的 Node Exporter、cAdvisor 和 Collector 只需安装一次，可以被多个项目复用。项目通过 Compose 项目名限定自己的容器和日志范围。

## 第一版统一规则

规则由 Oncall 根据项目配置生成到 `data/prometheus/rules/oncall.yml`，用户不在页面手工编辑。

| 类别 | 条件 | 持续时间 |
| --- | --- | --- |
| 可用性 | 应用 `/metrics` 或 Node Exporter 抓取失败 | 2 分钟 |
| 应用 | 5xx 错误率超过 10% | 5 分钟 |
| 应用 | P95 超过 1.5 秒 | 5 分钟 |
| 主机 | 内存或磁盘超过 90% | 10 分钟 |
| 容器 | 项目容器 CPU 或内存持续过高 | 10 分钟 |
| PostgreSQL | 数据库数字采集失败 | 2 分钟 |
| PostgreSQL | 连接率超过 85% | 10 分钟 |
| PostgreSQL | 锁等待、长事务或复制延迟 | 5 分钟 |

瞬时波动不会立即告警。Alertmanager 默认等待 30 秒聚合，同类告警 10 分钟内合并，普通告警 4 小时才重复，严重告警 2 小时才重复。Oncall 还会按 Alertmanager `groupKey` 将同一组多个实例保存为一个 Incident 和一条首告。

## 配置生成

项目或服务器创建、修改、删除后，Oncall 自动重写：

- `data/prometheus/targets/oncall.json`：Prometheus file-SD 抓取目标。
- `data/prometheus/rules/oncall.yml`：项目级告警规则。

随后调用 Prometheus `/-/reload`。应用 `/metrics` 和 PostgreSQL 数字指标通过 Oncall 内部只读转发入口暴露给 Prometheus；这样凭证不会写入 Prometheus 配置，且 Server B 只需能访问 Oncall API。

## 安全边界

- Collector 只有只读 Docker Socket 和只读数据库账号。
- Prometheus 转发入口只返回数字，不返回连接串、SQL 文本或数据库结构。
- 日志和数据库明细仅由受限 Agent Tool 在事件诊断中查询。
- 生产环境使用 `PROMETHEUS_SCRAPE_TOKEN` 和 `ALERTMANAGER_WEBHOOK_TOKEN`，并限制 exporter/Collector 端口只对 Server B 开放。
