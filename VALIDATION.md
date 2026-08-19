# Validation Report — Oncall AI SRE V1.0

> 本文件已被 `RELEASE_VALIDATION.md` 取代，后者是本版本唯一、只记录真实执行项的验收口径。

历史口径（RC1 阶段）为 16 passed 的离线单测；V1.0 Release 阶段在本地真实环境（真实 PostgreSQL + Milvus + 真实 LLM + 前端构建 + 本机集成）完成验收：

- 全量 pytest：**76 passed**。
- 真实 LLM / 流式 token / 上下文压缩 / 重启持久化 / RAG E2E / 监控 E2E 均跑通。

详见 `RELEASE_VALIDATION.md` 与 `IMPLEMENTATION_STATUS.md`。
