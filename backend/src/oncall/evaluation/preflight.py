from __future__ import annotations

import asyncio

from pydantic import BaseModel

from oncall.evaluation.collector import corpus_snapshot
from oncall.evaluation.judge import RagasJudge
from oncall.infrastructure.db.session import SessionFactory
from oncall.rag.rerank import Reranker
from oncall.rag.retrieval import KnowledgeRetriever
from oncall.security.redact import redact_text


class JudgeProbe(BaseModel):
    ready: bool


async def preflight() -> dict:
    result = {"checks": {}, "ok": True}
    try:
        corpus = await corpus_snapshot()
        result["corpus"] = corpus
        result["checks"]["corpus"] = {"ok": bool(corpus), "ready_documents": len(corpus)}
    except Exception as exc:
        result["checks"]["corpus"] = {"ok": False, "error": redact_text(str(exc))}
    judge = None
    try:
        judge = RagasJudge(["answer_relevancy"])
        answer = await asyncio.wait_for(
            judge.llm.agenerate(
                "This is a structured output connectivity check. Return ready=true.", JudgeProbe
            ),
            judge.timeout,
        )
        result["checks"]["judge"] = {"ok": answer.ready, "configuration": judge.metadata}
    except Exception as exc:
        result["checks"]["judge"] = {"ok": False, "error": redact_text(str(exc))[:2000]}
    try:
        if judge is None:
            raise ValueError("judge configuration failed")
        vector = await asyncio.wait_for(
            judge.metrics["answer_relevancy"].embeddings.aembed_text(
                "knowledge evaluation connectivity check"
            ),
            judge.timeout,
        )
        result["checks"]["judge_embeddings"] = {"ok": bool(vector), "dimension": len(vector)}
    except Exception as exc:
        result["checks"]["judge_embeddings"] = {"ok": False, "error": redact_text(str(exc))[:2000]}
    finally:
        if judge is not None:
            await judge.close()
    try:
        async with SessionFactory() as session:
            retrieval = await KnowledgeRetriever(session).search("指标采集链路异常排查", top_k=1)
        hits = retrieval.data if isinstance(retrieval.data, list) else []
        result["checks"]["retrieval"] = {
            "ok": retrieval.ok and bool(hits) and not any(h.get("rerank_fallback") for h in hits),
            "summary": retrieval.summary,
            "error_code": retrieval.error_code,
            "hit_count": len(hits),
        }
    except Exception as exc:
        result["checks"]["retrieval"] = {"ok": False, "error": redact_text(str(exc))[:2000]}
    try:
        reranked = await Reranker().rerank(
            "指标采集异常",
            [{"id": "probe", "content": "指标缺失时应检查采集目标状态，不能把缺失值当作零。"}],
            top_k=1,
        )
        result["checks"]["reranker"] = {"ok": bool(reranked)}
    except Exception as exc:
        result["checks"]["reranker"] = {"ok": False, "error": redact_text(str(exc))[:2000]}
    result["ok"] = all(check["ok"] for check in result["checks"].values())
    return result
