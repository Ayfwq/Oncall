# PulseOps · 巡脉智能运维平台：Agent 流程、记忆与工具设计说明

这份说明以当前代码为准，配套架构图是 [pulseops-architecture-v2.svg](pulseops-architecture-v2.svg)。原来的 “Oncall AI SRE” 是示意图标题；项目当前在代码、API 和文档中的正式名称是 **PulseOps · 巡脉智能运维平台**。

## 1. 先记住三条边界

1. **MonitoringEngine / Detector 决定是否异常**。采集、阈值、基线、连续命中和恢复都是确定性代码；LLM 不负责产生首条告警。
2. **PostgreSQL 是业务事实源**。Conversation、Message、Incident、Evidence、Diagnosis、AgentRun、ToolRun、RetrievalTrace、BackgroundJob 和 Notification 都落在 PostgreSQL。
3. **LangGraph Checkpoint 只保存运行时状态**，Milvus 只保存可重建的 RAG 索引。二者都不能替代业务事实库。

因此，这不是“多个 Agent 互相聊天”的 Agent 集群，而是一个统一 `OncallAgent`：同一张 StateGraph，根据 `CHAT / INVESTIGATE / FOLLOW_UP / DEEP` 选择不同入口上下文、权限和预算；异步 Worker 负责把它可靠地调度起来。

## 2. 一次请求从哪里进入

### Web / API

`POST /api/conversations/{cid}/messages:stream` 由 FastAPI 接收。API 为一次调用创建独立的数据库 session，调用 `AgentService.run()`，再通过 SSE 推送 `status`、`tool_started`、`tool_finished`、`rag_retrieved`、`diagnosis_ready`、`token` 和 `final` 事件。

### 飞书

飞书 WebSocket 收到消息后，`FeishuGateway` 用 `ChannelBinding` 找到或创建 Conversation。入站事件先由 `ProcessedChannelEvent` 去重，再运行 Agent；回复不会在事件处理函数里直接发网络请求，而是写入 Notification outbox。回复告警消息时，`FeishuMessageLink` 的 `root_id / parent_id / message_id` 把消息重新定位到原来的 Incident Conversation，所以后续会进入 `FOLLOW_UP`。

### 监控内部事件

`monitor-worker` 周期性调用 `MonitoringEngine.run_project()`。规则进入 FIRING 后，`IncidentService` 会在一个事务中写入 Incident、IncidentSignal、首条 IncidentEvidence、Incident Conversation、首条 Notification 和 `incident_investigate` BackgroundJob。之后由 `agent-worker` 消费 job，执行 `INVESTIGATE`，而 `notification-worker` 独立发送首条告警。

### 知识库接入

文档上传只负责登记版本并创建 durable RAG job。`rag-worker` 后台完成解析、切块、Embedding、Milvus upsert。Agent 的 `search_knowledge` 只检索与当前 Project scope 可见的片段。

## 3. Agent StateGraph 的实际流转

实现位置：`backend/src/oncall/agent/graph.py`；状态定义：`backend/src/oncall/agent/state.py`。

```text
START
  ↓
load_context
  ↓
route_intent ── clarification ──→ finalize
  │
  ├─ requires_knowledge → retrieve_knowledge → reason
  └─ otherwise → reason
                    ↓
             action = tool ?
              ├─ no  → finalize
              └─ yes → guard_tools → execute_tools → record_observations
                                                        └──────────────→ reason
                    finalize → persist_result → END
```

### 3.1 `load_context`：装配进入模型的上下文

`ContextBuilder.build()` 并行读取：

- 当前 Conversation 的最近 30 条消息和最新压缩摘要；
- Project 名称与描述；
- 当前 Incident 的状态、级别、异常类型、资源、时间窗口；
- IncidentEvidence；
- 最近一次 Diagnosis。

随后 `load_context` 为本次 AgentRun 重置临时计数器：工具调用次数、reason loop 次数、已调用工具集合、RAG 命中、待执行工具等都不会从上一次运行继承。

### 3.2 `route_intent`：先用确定性路由缩小范围

`classify_intent()` 不调用 LLM，也不调用工具。它根据当前消息、上一条用户消息、Project/Incident 绑定以及运行模式，给出可审计的第一跳：

| 路由 | 典型问题 | 允许的工具 |
|---|---|---|
| `casual_chat` | 问候、身份问题 | 无 |
| `ops_qa` | 运维原理、SOP | 仅 `search_knowledge` |
| `project_query` | “当前 CPU / 日志 / 健康状态怎样” | 8 个工具 |
| `incident_investigation` | 监控触发的主动调查 | 8 个工具 |
| `incident_followup` | 原告警线程继续追问 | 按消息需要读取实时状态 |
| `clarification` | 实时问题但没有 Project | 无，先要求选择 Project |

实时查询没有 Project 时不会猜目标，也不会让模型自行传入另一个 `project_id`。

### 3.3 `retrieve_knowledge`：把短追问改造成可检索上下文

如果路由要求知识库，系统调用一次 `search_knowledge`。短句如“那怎么处理”“继续看一下”会和当前 Incident 的 `anomaly_type`、摘要以及上一条用户消息拼接后再检索，避免直接拿低信号短句搜 SOP。

RAG 返回 `knowledge_refs`，包含 document/version/chunk/page/score；这些引用会进入最终诊断的 `knowledge_refs`，但不会被当成实时系统事实。

### 3.4 `reason`：LLM 只做下一步选择或收敛

`ModelProvider.decide()` 接收裁剪后的上下文、Evidence、知识命中和当前允许的工具 schema，输出严格的 `AgentDecision`：

- `action = tool`：给出工具名、参数和理由；
- `action = final`：普通回答，或包含结构化 `DiagnosisReport`。

真实模型使用 OpenAI-compatible `/chat/completions`；开发环境可用 MockProvider 验证图、工具、持久化和 E2E wiring。普通回答正文通过 `stream_answer()` 流式生成，避免把完整正文塞进决策 JSON。

### 3.5 `guard_tools`：运行时控制权高于模型

每次工具调用执行前都会检查：

- 工具是否在 `ALLOWED_TOOLS`，且是否在当前路由允许集合；
- 参数是否满足 JSON schema：类型、枚举、最小/最大值、数组去重、禁止额外字段；
- 同一个 `tool_name + sorted(args)` 是否已经调用过；
- 本次 AgentRun 是否超过 tool budget。

`project_id`、`incident_id`、`agent_run_id` 不在模型可控参数里，而由 `ToolExecutionContext` 注入。

### 3.6 `execute_tools` → `record_observations`：结果必须成为证据

`ToolRegistry.execute()` 为每个工具设置 15 秒超时。超时、参数错误、权限错误、外部数据源失败都会变成带 `error_code` 的 `ToolResult`，不会直接把整个 Graph 打崩。

成功的实时工具结果转成 `EvidenceItem`，追加到内存状态；如果 Incident 仍存在，则追加到 `IncidentEvidence`。知识库结果单独追加引用和 RetrievalTrace。然后回到 `reason`，让模型基于新证据决定“继续查哪一项”还是“已经足够，直接收敛”。

### 3.7 `finalize` → `persist_result`：回答、报告与通知

- `CHAT / FOLLOW_UP`：必要时用流式 LLM 生成普通回答；没有有效结果则诚实降级。
- `INVESTIGATE / DEEP`：要求 `DiagnosisReport`，包括摘要、症状、Evidence、根因、置信度、处理步骤、风险、验证方式、知识引用和 unknowns。
- 没有诊断 JSON 时，系统生成“预算内收敛”的低置信度报告，明确说明根因尚未确认。
- `persist_result` 写入 assistant Message；Incident 诊断写入 Diagnosis，并为飞书回复创建 Notification outbox；完成 AgentRun 的状态和 usage 元数据。

## 4. 记忆如何管理

PulseOps 有四层记忆，职责不混用。

### 4.1 会话业务记忆：完整消息 + 滚动摘要

`Conversation / Message` 保留完整原始历史，永远不会因为压缩被删除。`ConversationMemoryService.compact_if_needed()` 在消息数超过 40 时，用模型把旧消息压缩成事实摘要，保留最近 20 条原文；下一次上下文装配携带“最新摘要 + 最近 30 条消息”。摘要通过 `through_message_id` 标记已覆盖位置，二次压缩只处理新增旧消息，不重复摘要。

这解决的是上下文窗口增长问题，不是数据丢失问题：审计、重启恢复和页面历史仍从 PostgreSQL 全量读取。

### 4.2 Incident 记忆：事实、证据与诊断分离

- `Incident`：事件生命周期、fingerprint、级别、异常类型、资源和时间窗口；
- `IncidentSignal`：哪些规则/资源属于这个事件，以及每条信号的 firing/recovered 状态；
- `IncidentEvidence`：监控触发、工具观察和恢复事实；
- `Diagnosis`：一次 AgentRun 产出的结构化诊断报告。

因此 `FOLLOW_UP` 不依赖模型“记得上次说了什么”，而是重新读取 Incident、Evidence 和最新 Diagnosis，再按需复查实时工具。

### 4.3 Agent Runtime 记忆：LangGraph Checkpoint

API 和 `agent-worker` 都使用 `AsyncPostgresSaver`。`thread_id` 使用 `conversation_id`，让同一会话的 StateGraph 可以在重启后恢复。Checkpoint 主要服务于运行时中断/恢复；一次新的 AgentRun 会把工具次数、loop 次数和已调用集合重置，避免旧运行的预算污染新运行。

### 4.4 取证与知识记忆

`AgentRun`、`ToolRun` 和 `RetrievalTrace` 记录谁在何时调用了什么、参数哈希、耗时、结果规模、截断状态、错误码和引用。知识库则保留 raw 文件、canonical JSON/Markdown、PostgreSQL chunks；Milvus 中的 Dense/BM25 只是派生索引，可删除后重建。

## 5. 工具如何设计

当前只有 8 个 READ tool，按“证据域”而不是按“单个指标”拆分：

| 工具 | 解决的问题 | 真实数据来源 |
|---|---|---|
| `query_incident_context` | 为什么触发、阈值、已有证据是什么 | Incident / Signal / Rule / Evidence |
| `query_current_metrics` | 最近快照现在是什么值 | MonitoringRun 快照；必要时才 fresh collect |
| `query_metric_history` | 突发、持续、恶化还是恢复 | MetricSample 时间序列 |
| `query_service_health` | HTTP 可达性、状态码、延迟 | 已配置 health endpoints |
| `search_logs` | 哪些错误日志集中出现 | 受 Token 保护的 Collector |
| `query_database_health` | 连接、长事务、锁、慢 SQL、复制、缓存 | 受控 PostgreSQL 诊断 |
| `query_runtime_resources` | 容器/进程状态、CPU、内存、OOM、端口 | 受 Token 保护的 Collector |
| `search_knowledge` | SOP、处置手册和引用 | Embedding + Milvus Dense/BM25 + RRF + Rerank |

推荐调查顺序是：

```text
Incident 上下文
  → 当前快照 / 触发指标历史
  → 与异常类型最相关的专项工具
  → SOP / 知识库
  → Evidence-driven Diagnosis
```

例如：

- HTTP/API 异常：`incident_context → current_metrics/history → service_health → search_logs → runtime_resources`；
- 数据库异常：`incident_context → current_metrics/history → database_health → search_logs`；
- 日志异常：`incident_context → current_metrics/history → search_logs → runtime_resources`；
- 主机/进程异常：`incident_context → current_metrics/history → runtime_resources → search_logs`。

不要求也不应该把 47 个指标全部调用一遍；指标是数据字段，Tool 是有语义的数据域入口。

## 6. 异步编排如何保证可靠

`BackgroundJob` 是 PostgreSQL-backed durable queue：按 priority/created_at 领取，使用 `SELECT ... FOR UPDATE SKIP LOCKED`，设置 lease，超时后可 reclaim；失败按延迟重试，超过 max attempts 进入 dead；idempotency key 防重复 job。

### 监控链路

`monitor-worker` 每 5 秒扫描 enabled Project，使用 PostgreSQL advisory lock 保证同一主机只有一个监控 leader；到期项目才执行采集。`MonitoringEngine` 并行执行 4 条 Integration，写入 Snapshot/MetricSample 后评估规则。Detector 的 FIRING、RECOVERING 等状态持久化。

### Agent 链路

`agent-worker` 只消费 `incident_investigate`，每个任务调用一次 `AgentService(..., mode=INVESTIGATE)`；在 Incident 升级、增加新信号、人工 DEEP 或 stale recheck 时重新排队。同一 Incident 持续 FIRING 不会每个轮询周期都重复调用 LLM。

### 通知链路

`notification-worker` 只负责 drain Notification outbox。它与 Agent 调查进程分离，因此 LLM 调查耗时或 Agent worker 堵塞不会拖住首条告警。飞书发送失败会记录错误并按策略重试；同一阶段通过 dedupe key 保证不重复发送。

### RAG 链路

`rag-worker` 消费 `rag_ingest / knowledge_reindex`，完成 Docling 转换、HybridChunker、Embedding、Milvus Dense/BM25 建索引。上传/索引失败是文档版本状态，不会把实时监控诊断误判为成功。

## 7. 真实告警的一条完整时间线

```text
1. monitor-worker 采集 41/47 个信号
2. threshold / baseline / hybrid + 连续命中 → Detector FIRING
3. IncidentService 用 fingerprint + 120s 窗口合并信号
4. 同一事务写 Incident、Conversation、首条 Notification、investigate job
5. notification-worker 尽快发送简短事实告警
6. agent-worker 加载 Incident 上下文，按预算取证
7. 每次工具结果写 IncidentEvidence；RAG 写 Citation / RetrievalTrace
8. LLM 输出 Evidence-driven Diagnosis，写 Diagnosis
9. 诊断通知写入同一 Incident 的 Feishu 线程
10. 用户回复同一告警 → FOLLOW_UP，读取原 Incident/Evidence 并按需复查
11. 所有 IncidentSignal 恢复 → resolved + recovery outbox
```

## 8. 模式预算与收敛策略

当前 `BUDGETS`：

| 模式 | Tool budget | Reason loop budget | 适用场景 |
|---|---:|---:|---|
| `CHAT` | 5 | 4 | 普通问答和轻量实时查询 |
| `FOLLOW_UP` | 6 | 5 | 已有关联 Incident 的追问 |
| `INVESTIGATE` | 10 | 8 | 监控触发的标准调查 |
| `DEEP` | 14 | 12 | 人工发起的深度调查 |

预算耗尽、重复调用或无授权工具时，Agent 必须收敛，明确已有证据、未知项和建议的下一步；不会无限循环。

## 9. 可直接对照代码的入口

| 能力 | 代码 |
|---|---|
| StateGraph、路由、预算、Evidence、持久化 | `backend/src/oncall/agent/graph.py` |
| State 字段 | `backend/src/oncall/agent/state.py` |
| 确定性意图分类 | `backend/src/oncall/agent/router.py` |
| 上下文装配 | `backend/src/oncall/agent/context_builder.py` |
| 8 个工具 schema | `backend/src/oncall/agent/tool_contracts.py` |
| 工具执行、scope、timeout、审计 | `backend/src/oncall/agent/tool_registry.py` |
| LLM / Mock / streaming | `backend/src/oncall/agent/model_gateway.py` |
| 会话压缩 | `backend/src/oncall/application/memory_service.py` |
| AgentRun 入口 | `backend/src/oncall/application/agent_service.py` |
| 监控采集与规则评估 | `backend/src/oncall/monitoring/engine.py` |
| Detector 状态机 | `backend/src/oncall/monitoring/detector.py` |
| Incident 聚合、升级、恢复 | `backend/src/oncall/application/incident_service.py` |
| PG durable jobs | `backend/src/oncall/jobs/queue.py` |
| Agent worker | `backend/src/oncall/workers/agent_worker.py` |
| 通知 worker | `backend/src/oncall/workers/notification_worker.py` |
| RAG worker / retrieval | `backend/src/oncall/workers/rag_worker.py` / `backend/src/oncall/rag/retrieval.py` |

## 10. 当前实现状态与阅读结论

根据 `docs/RELEASE_VALIDATION.md` 和 `docs/IMPLEMENTATION_STATUS.md`，本地真实环境已验证 Monitoring、真实 LLM、RAG、PostgreSQL Checkpoint、流式输出、记忆压缩、重启持久化、工具审计和前端构建；真实飞书消息闭环仍需要目标环境提供 App ID / Secret / receive_id，AutoGEO 实机采集仍依赖目标机安装对应应用。

最重要的理解方式是：**规则负责发现，Agent 负责解释；Evidence 连接两者；PostgreSQL 负责记住事实；Checkpoint 负责恢复运行；Milvus 负责加速检索；Outbox/Job 负责把慢操作变成可靠的异步流程。**
