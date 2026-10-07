# 验证记录

2026-10-07。已删除重复测试脚本和过时文档，正式回归测试统一保留。

| 检查 | 结果 |
| --- | --- |
| 后端离线回归 | `116 passed`；19 个服务测试不在离线范围内 |
| 后端 Ruff | 通过 |
| Python 依赖锁 | `uv lock --check` 通过；195 个解析包，含可选依赖 |
| 前端测试 | 7 项通过 |
| 前端生产构建 | 通过；现有主包超过 500 kB 的提示仍存在 |
| 文档本地链接 | 无失效链接 |
| PostgreSQL / 在线联调 | 本机服务不可用，未通过在线验证 |

| 入口 | 范围 |
| --- | --- |
| `scripts/test.ps1` | 正式 pytest；默认 offline，可选 local/integration/rag/all |
| `scripts/validate-local.ps1` | 基础设施、后端检查、集成测试和前端构建 |
| `scripts/e2e-local.ps1` | 在线端到端冒烟 |
| `scripts/evaluate-rag.ps1` | 可选 Ragas 质量评估 |

服务测试必须真实连接 PostgreSQL/Milvus/API；缺少服务不计为通过。本机 PostgreSQL 拒绝连接、Docker 引擎未运行，在线数据库审计和完整诊断链路尚未复验。

历史 RAG 实测（2026-10-04）：3 题真实 Agent 问答因 Embedding HTTP 402 未完成有效评分，独立 Rerank 也返回 HTTP 402，质量门禁失败。裁判校准和草稿生成曾通过，但不代表生产问答质量；本次未重新调用这些外部服务。
