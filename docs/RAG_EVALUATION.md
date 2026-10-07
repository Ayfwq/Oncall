# RAG 质量评估

独立评估工作区知识库单轮问答：`AgentService → OncallGraphRuntime → MCP Client/Server → KnowledgeRetriever → 回答模型`。记录模型实际收到的裁剪后上下文、引用、延迟与降级信息；参考答案只交给裁判。

每题使用独立评估用户与归档会话，不绑定项目或事件。PostgreSQL 保存运行与检索审计，结果写入 `output/evaluation`。告警调查、多轮记忆和通知链路由端到端测试验证。

## 安装与配置

```powershell
uv sync --locked --extra dev --extra evaluation
Copy-Item evaluation/judge.env.example .env.ragas
```

配置 `RAGAS_JUDGE_MODEL`、`RAGAS_JUDGE_BASE_URL`、`RAGAS_JUDGE_API_KEY`；三项留空时使用应用模型作为裁判。可通过 `RAGAS_EMBEDDING_MODEL/BASE_URL/API_KEY` 指定裁判 Embedding。正式评估需应用模型、Embedding、Rerank、PostgreSQL、Milvus 和参考文档全部可用。

Ragas 与兼容依赖以 `evaluation` extra 和 `uv.lock` 为准；生产服务无需安装该 extra。以下命令使用 `--no-sync` 保留已安装的评估依赖。

## 数据与门槛

| 文件 | 用途 |
| --- | --- |
| `evaluation/datasets/knowledge.jsonl` | 默认合并数据集 |
| `evaluation/datasets/runbooks.jsonl` | 运维手册题 |
| `evaluation/datasets/edge_cases.jsonl` | 跨文档、口语与不可回答题 |
| `evaluation/thresholds.json` | 评分门槛 |

每题至少包含 `id`、`user_input`、`reference`、`reference_contexts`。正式门禁需要审核参考答案（`reviewed=true`）并绑定实际源文档/版本；生成的草稿需人工审阅。不可回答题使用 `expected_answerable=false`，单独评估拒答。

| 指标 | 初始门槛 |
| --- | ---: |
| Context Precision | 0.70 |
| Context Recall | 0.80 |
| Faithfulness | 0.90 |
| Answer Relevancy | 0.80 |
| Factual Correctness | 0.80 |
| Safe Abstention | 1.00 |

## 常用命令

```powershell
# 数据校验与真实服务检查
uv run --no-sync python -m oncall.evaluation validate
uv run --no-sync python -m oncall.evaluation doctor --output output/evaluation/preflight.json
# 小批次实验
.\scripts\evaluate-rag.ps1 -Limit 3 -Output output/evaluation/trial
# 正式门禁与基线比较
.\scripts\evaluate-rag.ps1 -Output output/evaluation/baseline -Gate
.\scripts\evaluate-rag.ps1 -Output output/evaluation/candidate -Baseline output/evaluation/baseline/report.json -Gate
# 同配置断点续跑
.\scripts\evaluate-rag.ps1 -Output output/evaluation/candidate -Resume -Gate
```

可分开执行 `collect --output <目录>` 与 `score --run-dir <目录>`，更换裁判时重评已有回答。其他子命令为 `generate`、`merge`、`calibrate`、`compare`、`import-template`；完整参数使用 `uv run --no-sync python -m oncall.evaluation --help`。

报告包含 `manifest.json`、`collected.json`、`report.json`、`report.md`、`scores.csv` 和采集/评分断点。续跑要求对应数据、语料、配置与代码 hash 一致；比较要求样本、语料、指标和裁判可比。

采集失败、参考未审核/缺失、重排降级、评分不完整或低于门槛、基线不一致/退化超限都会使门禁失败。无有效评分时记录 `null` 和原因。普通实验可退出 0 并保留 FAIL 报告；发布使用 `-Gate`，失败退出 2，配置/入口错误退出 1。

历史真实评估与当前回归状态见 [VALIDATION.md](VALIDATION.md)。
