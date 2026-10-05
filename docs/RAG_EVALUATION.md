# 知识库 Ragas 评估

## 评估边界与目标

本流程评估当前工作区共享知识库的单轮问答质量。采集直接运行生产 `AgentService → OncallGraphRuntime → KnowledgeRetriever`，因此真实覆盖意图路由、Embedding、Milvus Dense/BM25、RRF、Rerank、相邻分块扩展、输入裁剪和回答生成。每道题使用独立、已归档的评估会话和独立评估用户，不注入项目或 Incident，不读取个人长期记忆。对话、AgentRun、RetrievalTrace 保留在 PostgreSQL，完整评估产物写入本地 `output/evaluation/`。

评估记录的 `retrieved_contexts` 来自回答模型实际收到的上下文，经过生产 Agent 的字符和 Token 预算裁剪；保留 `model_context`、原始命中、引用、查询、延迟与重排降级标记。`reference` 只送给裁判，绝不送给被评估的回答模型。自动上传语料、修改在线模型配置和执行处置动作不属于评估命令。

入库质量通过成功版本、原文件 checksum、解析器版本、分块数量与内容 hash 固定，并反映在问答召回评分中。故障调查、多轮记忆与通知链路仍应由项目现有 E2E 验证；这里的分数不代表这些链路已通过。

## 数据集

- `evaluation/datasets/runbooks.jsonl`：迁移已有 12 类运维手册的 36 题，覆盖根因判断、分支处置、恢复验证。
- `evaluation/datasets/edge_cases.jsonl`：6 题，覆盖口语化提问、跨文档关联及知识缺失。
- `evaluation/datasets/knowledge.jsonl`：默认使用上述合并后的 42 题。
- 三份文件都作为可版本管理的草稿提交，不依赖旧脚本的 `D:\Oncall\tmp` 文件。模型生成的参考答案和迁移样本都保持 `reviewed=false`。

每行至少包含 `id`、`user_input`、`reference`、`reference_contexts`。可回答题必须具有参考证据。`source_titles` 用于可移植的源文档检查，正式数据应补全 `source_document_ids` 和 `source_version_ids`，防止用新版本资料检验旧答案。`expected_answerable=false` 表示应明确说明知识不足；此类题评估 `safe_abstention`，不混入正向题的召回和忠实度均值。

审核人应逐题核对原文、参考答案是否充分且没有多余事实、跨文档关系是否成立、不可回答问题是否确实不在完整语料中，再填写 `reviewed=true`。不要因分数低而修改标准答案以迎合模型。建议增加真实用户问题，并按文档或主题划分开发集和保留测试集；检索和提示词调优只能使用开发集。

## 指标与初始门槛

| 指标 | 评估对象 | 初始门槛 |
|---|---|---:|
| Context Precision with Reference | 相关上下文在召回顺序中的精度 | 0.70 |
| Context Recall | 上下文能否覆盖参考答案中的事实 | 0.80 |
| Faithfulness | 回答声明能否由实际上下文支持 | 0.90 |
| Answer Relevancy | 回答与问题的相关性，使用独立生成问题和 Embedding 相似度 | 0.80 |
| Factual Correctness（F1） | 回答事实相对参考答案的精度与完整性 | 0.80 |
| Safe Abstention（扩展裁判指标） | 知识不足时明确承认未知、不编造细节 | 1.00 |

门槛保存在 `evaluation/thresholds.json`，是项目初始配置，需要结合人工标注和业务风险校准。Safe Abstention 使用 Ragas 的结构化 LLM 接口，是项目扩展指标；其余五项直接使用 Ragas 0.4.3 collections 指标。

报告提供逐题得分、按类别与题型分组统计、500 次固定种子的 Bootstrap 95% 区间、端到端延迟、错误明细和基线差值。区间只反映当前样本的抽样变化，不衡量裁判本身的随机误差；少量样本和同类问题高度相关时不能据此得出统计显著结论。

## 安装和独立裁判配置

在仓库根目录执行（更新依赖时先停止使用 `.venv` 启动的 Python 服务，安装后运行 `scripts/start-all.ps1`）：

```powershell
uv sync --locked --extra dev --extra evaluation
Copy-Item evaluation/judge.env.example .env.ragas
```

`.env.ragas` 被 Git 忽略。专用裁判必须同时配置：

```env
RAGAS_JUDGE_MODEL=<judge-model>
RAGAS_JUDGE_BASE_URL=https://<judge-endpoint>/v1
RAGAS_JUDGE_API_KEY=<secret>
```

三项全部留空则使用当前应用模型，通过单独客户端调用；报告明确记录是否与回答模型相同。同模型裁判可能存在偏差，正式基线建议用独立模型并抽样人工复核。Answer Relevancy 默认使用应用 Embedding，也可用 `RAGAS_EMBEDDING_MODEL/BASE_URL/API_KEY` 单独配置。HTTP 402 属于上游计费故障，不能通过修改评分逻辑规避。

依赖锁定了 Ragas 0.4.3 与 `langchain-community==0.3.31`：后续 community 版本删除了该 Ragas 版本仍导入的 VertexAI 模块。PyTorch CPU 索引改用官方、显式索引，以修复旧镜像无法完成依赖解析的问题。评估 extra 不会被正常应用导入；不运行评估时无需安装。

## 全流程操作

1. 先通过知识库页面或已有上传 API 上传源文档，等待 RAG 入库任务完成。确认参考数据集依赖的全部文档与版本可用。
2. 校验数据并检查真实服务：

```powershell
uv run --no-sync python -m oncall.evaluation validate
uv run --no-sync python -m oncall.evaluation doctor --output output/evaluation/preflight.json
```

`doctor` 检查数据库语料、裁判结构化输出、裁判 Embedding、真实检索和独立重排接口，失败返回非零退出码。

3. 需要扩充数据时，从当前成功入库的真实分块生成草稿：

```powershell
uv run --no-sync python -m oncall.evaluation generate --size 12 --seed 42 --output output/evaluation/new-drafts.jsonl
uv run --no-sync python -m oncall.evaluation merge --input evaluation/datasets/knowledge.jsonl --input output/evaluation/new-drafts.jsonl --output output/evaluation/expanded.jsonl
```

生成器使用 Ragas `llm_factory().agenerate()`，在固定种子选择的分块上生成中文问题、标准答案和原文引用；引用按 Unicode、空白规范化后必须确实存在于原文，最多重试三次，否则拒绝该草稿。证据字段保存完整真实分块。此路径不需要额外构建知识图谱，降低对现有小型运维手册的成本，且不依赖生产问答结果产生标准答案。生成结果仍需审核。单跳/流程/恢复问题自动生成；跨文档和不可回答问题应在审阅完整语料后设计。

4. 用正确及明显错误的合成例子校准裁判基本方向：

```powershell
uv run --no-sync python -m oncall.evaluation calibrate --output output/evaluation/judge-calibration.json
```

该输出只验证裁判，不代表生产系统质量。可用 `--metrics faithfulness,factual_correctness,context_precision,context_recall` 扩展连接检查。

5. 正式采集并评分：

```powershell
.\scripts\evaluate-rag.ps1 -Output output/evaluation/baseline
# 用于发布/回归门禁：
.\scripts\evaluate-rag.ps1 -Output output/evaluation/release -Gate
```

等价入口为 `uv run --no-sync python -m oncall.evaluation run` 或 `oncall-rag-eval`。默认并发 1，每题 Agent 超时 240 秒，每项裁判超时 120 秒，可通过 `.env.ragas` 和 CLI 调整。先使用 `--limit 3` 或多次 `--sample-id <id>` 做小批次验证，再运行完整数据集。

采集与评分也可分开执行，从而更换裁判时无需重复调用生产回答模型：

```powershell
uv run --no-sync python -m oncall.evaluation collect --output output/evaluation/experiment
uv run --no-sync python -m oncall.evaluation score --run-dir output/evaluation/experiment
```

6. 查看输出：`manifest.json` 固定配置与语料；`samples/` 保存采集断点；`collected.json` 保存全部问题、实际回答与模型上下文；`scored/` 保存评分断点和裁判明细；`report.json`、`report.md`、`scores.csv` 用于审阅和分析。时间戳以 UTC 保存，运行目录默认带时间和随机 ID。

## 续跑、基线和门禁

```powershell
.\scripts\evaluate-rag.ps1 -Output output/evaluation/experiment -Resume
uv run --no-sync python -m oncall.evaluation score --run-dir output/evaluation/experiment --resume
.\scripts\evaluate-rag.ps1 -Output output/evaluation/candidate -Baseline output/evaluation/baseline/report.json -Gate
# 对已经完成的两份报告比较，不产生模型调用：
uv run --no-sync python -m oncall.evaluation compare --current output/evaluation/candidate/report.json --baseline output/evaluation/baseline/report.json --output output/evaluation/comparison --gate
```

采集续跑要求数据集、知识库内容、生产配置与代码 hash 相同；成功采集的样本直接复用，失败样本重新运行。评分续跑要求指标、裁判与提示词 hash 相同；成功评分直接复用，评分失败的样本重算。更换裁判后使用不带 `--resume` 的 `score` 重评已有上下文。资料、模型配置或代码改变后应新建实验目录，保留此前产物。

基线比较只允许相同评估样本、语料快照、指标、裁判配置与裁判提示词；不满足条件时报告不可比较，不给出误导性的差值。被评估的检索/回答链路配置可以变化，这正是实验比较的目的。默认单项允许下降不超过 0.03，绝对门槛也必须满足。

以下任一情况使门禁失败：生产采集失败、参考来源缺失或未绑定、参考答案未审核、重排降级、必需的五项指标不齐、有效评分不完整、均值低于配置门槛、基线不一致或退化超限。未取得有效评分时使用 `null` 并保存原因，不将异常伪装成得分或成功。可回答题的真实空召回对 Precision/Recall 记 0，Faithfulness 无上下文时为未定义。

默认实验命令会输出报告和 FAIL 结论但退出 0，便于收集诊断产物；使用 `--gate` 时失败退出 2；配置/数据/执行入口错误退出 1。在 CI 或发布脚本里务必使用 `--gate` 并保存整个运行目录。不要将尚未达到门禁的报告作为有效基线。

## 当前实测记录（2026-10-04）

已运行 3 道当前唯一可用手册的真实 Agent 问答。三题均因应用 Embedding 服务 HTTP 402 失败，报告保留了原始失败原因和 Agent 实际返回的通用建议，五项评分均为未完成，门禁 FAIL。知识库当前仅有一份 ready 手册（23 分块），CPU 手册此前入库同样因 HTTP 402 失败；其余参考手册尚未成功入库。

裁判模型的真实结构化调用已通过；真实分块生成已成功产出 3 条草稿。合成裁判校准中，正确回答的 Faithfulness/Factual Correctness 均为 1.0，刻意错误回答均为 0.0；这只是裁判验证，不是生产分数。独立重排接口也实测返回 HTTP 402。自动化离线测试覆盖数据泄漏校验、生产裁剪、缺失评分、NaN/超时、空召回、参考版本、负向样本和基线不一致等边界。正式 42 题的有效质量分数仍需要恢复 Embedding/Rerank、完整语料入库及参考数据审核，不能由当前失败报告推断。

## 官方依据

- [Ragas 0.4 迁移说明](https://docs.ragas.io/en/stable/howtos/migrations/migrate_from_v03_to_v04/)：使用 collections、`ascore(**kwargs)` 与 `MetricResult.value`。
- [Context Precision](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/context_precision/)、[Context Recall](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/context_recall/)。
- [Faithfulness](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/faithfulness/)、[Answer Relevancy](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/answer_relevance/)、[Factual Correctness](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/factual_correctness/)。
