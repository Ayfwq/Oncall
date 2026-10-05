from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import time
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select

from oncall.agent.model_gateway import ModelProvider, get_model_provider
from oncall.application.agent_service import AgentService
from oncall.bootstrap.config import get_settings
from oncall.evaluation.dataset import EvalSample
from oncall.infrastructure.db.models import (
    AgentRun,
    Conversation,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeDocumentVersion,
    RetrievalTrace,
    User,
)
from oncall.infrastructure.db.session import SessionFactory
from oncall.security.redact import redact_text


def context_passages(context: dict) -> list[str]:
    """Only text actually passed to the model, after Agent input trimming."""
    passages = []
    seen_ids = set()
    for hit in context.get("knowledge_hits") or []:
        seen_ids.add(str(hit.get("id") or hit.get("chunk_id")))
        parts = [str(hit.get(key) or "").strip() for key in ("content", "context_text")]
        text = "\n\n".join(dict.fromkeys(part for part in parts if part))
        if text:
            passages.append(text)
    # Citation excerpts can remain in the prompt after token trimming drops a hit.
    for ref in context.get("knowledge_refs") or []:
        if str(ref.get("chunk_id")) not in seen_ids and ref.get("excerpt"):
            passages.append(str(ref["excerpt"]))
    return passages


class RecordingProvider(ModelProvider):
    def __init__(self, delegate: ModelProvider):
        self.delegate = delegate
        self.contexts: list[dict] = []
        self.answer_context: dict | None = None

    async def decide(self, context: dict):
        self.contexts.append(copy.deepcopy(context))
        decision = await self.delegate.decide(context)
        if decision.action == "final" and decision.answer:
            self.answer_context = copy.deepcopy(context)
        return decision

    async def stream_answer(self, context: dict, on_token=None):
        self.contexts.append(copy.deepcopy(context))
        self.answer_context = copy.deepcopy(context)
        return await self.delegate.stream_answer(context, on_token=on_token)

    async def summarize(self, text: str):
        return await self.delegate.summarize(text)


async def corpus_snapshot() -> list[dict]:
    async with SessionFactory() as db:
        rows = (
            await db.execute(
                select(KnowledgeDocument, KnowledgeDocumentVersion)
                .join(
                    KnowledgeDocumentVersion,
                    KnowledgeDocumentVersion.id == KnowledgeDocument.active_version_id,
                )
                .where(
                    KnowledgeDocument.status == "ready", KnowledgeDocumentVersion.status == "ready"
                )
                .order_by(KnowledgeDocument.id)
            )
        ).all()
        result = []
        for doc, version in rows:
            chunks = (
                await db.scalars(
                    select(KnowledgeChunk)
                    .where(KnowledgeChunk.version_id == version.id)
                    .order_by(KnowledgeChunk.chunk_index)
                )
            ).all()
            result.append(
                {
                    "document_id": str(doc.id),
                    "version_id": str(version.id),
                    "title": doc.title,
                    "checksum": version.checksum,
                    "parser_version": version.parser_version,
                    "chunk_count": len(chunks),
                    "chunk_hash": hashlib.sha256(
                        json.dumps(
                            [(str(c.id), c.chunk_index, c.content) for c in chunks],
                            ensure_ascii=False,
                        ).encode()
                    ).hexdigest(),
                }
            )
        return result


def source_available(sample: EvalSample, corpus: list[dict]) -> bool | None:
    if not (sample.source_titles or sample.source_document_ids or sample.source_version_ids):
        return None
    return (
        all(
            any("".join(title.split()) in "".join(doc["title"].split()) for doc in corpus)
            for title in sample.source_titles
        )
        and all(
            any(doc["document_id"] == source for doc in corpus)
            for source in sample.source_document_ids
        )
        and all(
            any(doc["version_id"] == source for doc in corpus)
            for source in sample.source_version_ids
        )
    )


async def collect_sample(sample: EvalSample, user_id, timeout: float) -> dict:
    row = {**sample.model_dump(), "response": "", "retrieved_contexts": [], "status": "failed"}
    started = time.monotonic()
    try:
        provider = RecordingProvider(get_model_provider())
        async with SessionFactory() as db:
            # Archived, independent conversations keep answers and traces auditable without
            # introducing history, workspace long-term memory, incidents or notifications.
            conv = Conversation(user_id=user_id, title=f"Ragas · {sample.id}", archived=True)
            db.add(conv)
            await db.commit()
            row["conversation_id"] = str(conv.id)
            state = await asyncio.wait_for(
                AgentService(db, model=provider).run(
                    conv.id, sample.user_input, channel="evaluation"
                ),
                timeout=timeout,
            )
            traces = (
                await db.scalars(
                    select(RetrievalTrace)
                    .where(RetrievalTrace.agent_run_id == UUID(state["run_id"]))
                    .order_by(RetrievalTrace.created_at)
                )
            ).all()
            context = provider.answer_context or (
                provider.contexts[-1] if provider.contexts else {}
            )
            row.update(
                {
                    "status": "ok",
                    "response": state.get("final_response") or "",
                    "retrieved_contexts": context_passages(context),
                    "retrieved_hits": state.get("knowledge_hits", []),
                    "model_context": context,
                    "agent_run_id": state["run_id"],
                    "knowledge_status": state.get("knowledge_status"),
                    "knowledge_error": state.get("knowledge_error"),
                    "citations": state.get("retrieved_citations", []),
                    "used_citations": state.get("used_citations", []),
                    "intent": state.get("intent"),
                    "retrieval_traces": [
                        {
                            "query": t.query,
                            "latency_ms": t.latency_ms,
                            "status": t.status,
                            "error_code": t.error_code,
                            "hit_count": t.hit_count,
                        }
                        for t in traces
                    ],
                    "rerank_fallback": any(
                        h.get("rerank_fallback") for h in state.get("knowledge_hits", [])
                    ),
                }
            )
            if not row["response"] or state.get("knowledge_status") == "unavailable":
                row["status"] = "failed"
                row["error"] = state.get("knowledge_error") or "empty Agent response"
    except Exception as exc:
        row["error"] = redact_text(f"{type(exc).__name__}: {exc}")
        # wait_for cancellation does not enter AgentService's Exception handler.
        # Close this isolated run in the audit log instead of leaving it running forever.
        if row.get("conversation_id"):
            try:
                async with SessionFactory() as cleanup:
                    abandoned = (
                        await cleanup.scalars(
                            select(AgentRun).where(
                                AgentRun.conversation_id == UUID(row["conversation_id"]),
                                AgentRun.status == "running",
                            )
                        )
                    ).all()
                    for run in abandoned:
                        run.status = "failed"
                        run.finished_at = datetime.now(UTC)
                        row["agent_run_id"] = str(run.id)
                    await cleanup.commit()
            except Exception as cleanup_error:
                row["cleanup_error"] = redact_text(str(cleanup_error))
    row["latency_ms"] = (time.monotonic() - started) * 1000
    return row


async def create_eval_user():
    async with SessionFactory() as db:
        user = User(username=f"ragas-eval-{uuid4().hex}")
        db.add(user)
        await db.commit()
        return user.id


def runtime_metadata() -> dict:
    s = get_settings()
    return {
        "model": s.model_name,
        "provider": s.model_provider,
        "embedding_model": s.embedding_model,
        "embedding_dimension": s.embedding_dimension,
        "rerank_model": s.rerank_model,
        "top_k": 5,
        "context_radius": s.knowledge_context_radius,
        "context_max_chars": s.knowledge_context_max_chars,
        "memory_input_hard_limit_tokens": s.memory_input_hard_limit_tokens,
    }
