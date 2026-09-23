# 系统架构

```text
Server A
  ├─ Node Exporter ───────────────┐
  ├─ cAdvisor ────────────────────┤
  ├─ Python /metrics ─────────────┤
  └─ Collector ─ DB numbers/logs ─┤
                                   ▼
Server B                       Prometheus
                                   │ rules + for
                                   ▼
                              Alertmanager
                                   │ grouped webhook
                                   ▼
Web / Feishu ───────────────► Oncall API ─► PostgreSQL
                                   │
                                   ├─ Agent Worker ─► Prometheus / Collector / RAG
                                   ├─ Notification Worker ─► Feishu
                                   └─ RAG Worker ─► Milvus
```

## 设计约束

1. 数字告警只由 Prometheus 判断。
2. Oncall 不保存本地规则状态或指标样本，历史指标读取 Prometheus。
3. Alertmanager fingerprint 保存单条告警身份，`groupKey` 保存用户可见事件身份。
4. 日志、SQL 明细和容器进程只在诊断阶段读取。
5. 所有 Agent Tool 只读，生产凭证由环境变量或加密字段管理。
6. PostgreSQL 保存业务事实、会话、事件、证据、诊断、任务和通知；Milvus 仅保存可重建索引。
7. 知识库是当前工作区共享资源，所有项目共用；项目范围只约束监控数据工具，不约束知识库检索。
