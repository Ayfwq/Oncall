# PulseOps Agent 指南

## 核心原则

Prometheus 负责“是否异常”，Alertmanager 负责“哪些告警应合并、何时再次通知”，Agent 负责“为什么异常、接下来怎么处理”。这三层不重复做同一件事。

Alertmanager 调用 `POST /api/webhooks/alertmanager` 后，Oncall 使用 `groupKey` 创建或更新一个 Incident。首次 firing 会在同一个数据库事务中写入：

- Incident；
- Alertmanager 证据；
- Incident Conversation；
- 飞书首告 Outbox；
- `incident_investigate` durable job。

同组重复 firing 只更新时间，不重复创建事件和首告；组内所有告警 resolved 后关闭事件并发送一次恢复通知。

## 调查过程

`agent-worker` 消费任务，首先读取告警上下文，再通过 Prometheus API 查询当前值和历史。随后根据类别选择日志、数据库、容器或知识库工具。所有工具均为只读；监控数据工具的参数经过项目范围校验，知识库则检索当前工作区共享内容。结果会脱敏并写入 ToolRun/Evidence。

知识库查询由告警上下文动态生成：告警名、异常类型、摘要和用户补充内容会组成检索词，并补充“排查、处理方案、恢复验证”等意图词。它不是固定的“生成故障报告”查询，也不会因为没有上传文档就生成引用。

模型最终生成结构化 Diagnosis，包括摘要、可能根因、置信度、处置建议和验证步骤。`notification-worker` 将结果回复到首告线程；用户继续回复时复用同一个 Incident Conversation。

## 运行进程

- `oncall-api`：项目配置、Prometheus 转发、Alertmanager Webhook、Web/飞书会话。
- `oncall-agent-worker`：LLM 调查任务。
- `oncall-notification-worker`：飞书 Outbox。
- `oncall-rag-worker`：知识文档索引。
- Prometheus / Alertmanager：独立容器，不存在 `oncall-monitor-worker`。
