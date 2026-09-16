# Oncall 诊断工具与 Agent 流程

## 设计结论

监控系统每个周期采集 41 个基础指标（配置 GPU 后为 47 个），确定性规则负责判断是否异常。指标是“数据字段”，不是 Agent Tool；Agent 使用 8 个按数据域划分的只读工具下钻证据。

完整链路：

`定时采集 → 指标快照 → 规则连续命中 → Incident 聚合 → Agent 按需取证 → 大模型诊断 → 飞书同一事件线程通知 → 连续恢复 → 恢复通知`

规则判断与大模型诊断职责分离：规则必须可重复、可审计，负责及时发现；大模型不能决定是否产生首条告警，只负责在告警之后选择工具、解释证据和生成处置建议。

## 8 个工具

| 工具 | 用途 | 主要参数 | 返回结果 |
|---|---|---|---|
| `query_incident_context` | 查看为什么告警 | 无；事件范围由运行时注入 | 触发指标、值、规则阈值、连续次数、已有证据 |
| `query_current_metrics` | 查看最新指标 | `metrics`、`groups`、`fresh` | 项目级和资源级指标、采集器状态、快照年龄 |
| `query_metric_history` | 判断突发/持续/恢复 | `metrics`、`resource_key`、`minutes`、`include_samples` | 当前值、最小/最大/均值、变化量、趋势和可选采样 |
| `query_service_health` | 主动检查 HTTP API | `endpoint`、`include_body` | 可达性、状态码、延迟；响应体脱敏且最多 2000 字符 |
| `search_logs` | 查具体错误日志 | `query`、`level`、`services`、时间和条数 | 脱敏日志、容器列表、归一化错误特征及次数 |
| `query_database_health` | 下钻 PostgreSQL | `checks`、`slow_query_limit` | 连接、长事务、锁等待、阻塞链、慢 SQL、复制和缓存 |
| `query_runtime_resources` | 下钻容器/进程 | `services`、`checks`、`top_n` | 状态、CPU、内存、重启、OOM、进程和端口 |
| `search_knowledge` | 查处置 SOP | `query`、`top_k` | 知识片段和引用；不作为实时事实 |

所有工具均只读。`project_id`、`incident_id` 和 `agent_run_id` 由运行时注入，模型不能传入或修改。参数会经过类型、枚举、数量、范围和额外字段校验；每次调用都记录参数哈希、耗时、结果规模和错误码。

## Agent 编排

1. `query_incident_context`：确认触发源和阈值，避免看到“CPU 高”却不知道是哪个资源、什么值。
2. `query_current_metrics`：读取最近完成的快照；只有需要复核瞬时状态时使用 `fresh=true`，避免一次诊断重复触发整套远程采集。
3. `query_metric_history`：只查询触发指标及相关指标，判断它是单点抖动、持续恶化还是已经恢复。
4. 根据异常选择专项工具：
   - HTTP/应用异常：服务健康 + 日志，必要时容器资源。
   - 数据库异常：数据库诊断 + 日志。
   - 日志异常：日志特征 + 容器资源。
   - 主机/进程异常：容器资源 + 日志。
5. `search_knowledge`：针对已经观察到的症状检索 SOP。
6. 大模型输出“已证实 / 推测 / 未知”、根因置信度、处理步骤、风险与恢复验证；通知 Worker 发送到飞书事件线程。

Agent 不要求融合全部 47 个指标。首次规则判断是每条规则分别判断，关联窗口内多个触发信号再合并为一个 Incident；诊断阶段才把相关信号组合起来解释。同一参数的工具调用不能重复，调查有工具预算和循环预算，工具失败会成为带错误码的可审计结果，不会使整个 Agent 图崩溃。

## 部署要求

- 目标服务器运行当前版本 `oncall-collector`，并配置与平台一致的 Collector Token。
- 日志源配置 `compose_project` 或受控 `services`；指定服务超出配置范围时拒绝查询。
- PostgreSQL 账号建议使用只读监控账号；`pg_stat_statements` 未安装时慢 SQL 能力会明确标记为不可用，其余检查仍可工作。
- 修改代码后需同时重启 API、Agent Worker、Monitoring Worker、Notification Worker 和目标服务器 Collector，确保工具契约与采集端版本一致。
