from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from oncall.bootstrap.config import get_settings
from oncall.domain.schemas import ToolResult
from oncall.infrastructure.db.models import KnowledgeChunk
from oncall.rag.embedding import get_embedding_provider
from oncall.rag.milvus_store import MilvusKnowledgeIndex
from oncall.rag.rerank import Reranker
from oncall.security.redact import redact_text

logger = logging.getLogger(__name__)


def rrf(lists: list[list[dict]], k: int = 60) -> list[dict]:
    merged = {}
    for hits in lists:
        for rank, item in enumerate(hits, 1):
            key = item["id"]
            m = merged.setdefault(key, dict(item, rrf_score=0.0))
            m["rrf_score"] += 1.0 / (k + rank)
    return sorted(merged.values(), key=lambda x: x["rrf_score"], reverse=True)


class KnowledgeRetriever:
    def __init__(self, session: AsyncSession | None = None):
        self.session = session
        self.settings = get_settings()
        self.embedder = get_embedding_provider()
        self.index = MilvusKnowledgeIndex()
        self.reranker = Reranker()

    @staticmethod
    def _source_view(row: KnowledgeChunk) -> dict:
        return {
            "chunk_id": str(row.id),
            "chunk_index": row.chunk_index,
            "heading_path": row.heading_path or [],
            "page_range": row.page_range,
            "content": row.content or "",
        }

    def _context_text(self, primary: dict, rows: list[KnowledgeChunk]) -> str:
        """Build a bounded, ordered context window around a primary hit."""
        primary_id = str(primary.get("id") or primary.get("chunk_id") or "")
        pieces: list[str] = []
        total = 0
        limit = self.settings.knowledge_context_max_chars
        for row in sorted(rows, key=lambda item: item.chunk_index):
            view = self._source_view(row)
            role = "当前命中分块" if view["chunk_id"] == primary_id else "相邻分块"
            heading = " / ".join(view["heading_path"] or [])
            label = f"【{role}：第 {view['chunk_index'] + 1} 块"
            if view["page_range"]:
                label += f"，页码 {view['page_range']}"
            if heading:
                label += f"，章节 {heading}"
            label += "】"
            piece = f"{label}\n{view['content'].strip()}".strip()
            remaining = limit - total
            if remaining <= 0:
                break
            if len(piece) > remaining:
                piece = piece[:remaining].rstrip() + "…"
            pieces.append(piece)
            total += len(piece)
        return "\n\n".join(pieces)

    async def _expand_context(self, items: list[dict]) -> list[dict]:
        """Attach same-version neighboring DB chunks to each reranked hit.

        Primary hits and their scores remain unchanged for citation and
        evaluation. ``context_text`` is an additional bounded window consumed
        by the agent, so this does not create duplicate Milvus results.
        """
        radius = self.settings.knowledge_context_radius
        if not self.session or radius <= 0 or not items:
            return items

        primary_ids: list[UUID] = []
        for item in items:
            try:
                primary_ids.append(UUID(str(item.get("id"))))
            except (TypeError, ValueError, AttributeError):
                continue
        if not primary_ids:
            return items

        seed_rows = list(
            (
                await self.session.scalars(
                    select(KnowledgeChunk).where(KnowledgeChunk.id.in_(primary_ids))
                )
            ).all()
        )
        if not seed_rows:
            return items

        conditions = [KnowledgeChunk.id.in_(primary_ids)]
        for row in seed_rows:
            conditions.append(
                and_(
                    KnowledgeChunk.version_id == row.version_id,
                    KnowledgeChunk.chunk_index.between(
                        max(0, row.chunk_index - radius), row.chunk_index + radius
                    ),
                )
            )
        rows = list(
            (
                await self.session.scalars(
                    select(KnowledgeChunk)
                    .where(or_(*conditions))
                    .order_by(KnowledgeChunk.version_id, KnowledgeChunk.chunk_index)
                )
            ).all()
        )
        by_id = {str(row.id): row for row in seed_rows}
        by_version: dict[str, list[KnowledgeChunk]] = {}
        for row in rows:
            by_version.setdefault(str(row.version_id), []).append(row)

        expanded: list[dict] = []
        for item in items:
            seed = by_id.get(str(item.get("id")))
            if seed is None:
                expanded.append(item)
                continue
            window = [
                row
                for row in by_version.get(str(seed.version_id), [])
                if abs(row.chunk_index - seed.chunk_index) <= radius
            ]
            enriched = dict(item)
            enriched["context_text"] = self._context_text(item, window)
            enriched["context_chunk_count"] = len(window)
            expanded.append(enriched)
        return expanded

    async def _expand_context_safe(self, items: list[dict]) -> list[dict]:
        """Keep primary retrieval available if the optional DB lookup fails."""
        try:
            return await self._expand_context(items)
        except Exception as exc:
            logger.warning("knowledge context expansion failed: %s", redact_text(str(exc)))
            return items

    async def search(self, query: str, top_k: int = 5) -> ToolResult:
        try:
            query = " ".join(str(query).strip().split())[:1000]
            if not query:
                return ToolResult(
                    ok=False, summary="知识库检索参数为空", error_code="INVALID_QUERY"
                )
            top_k = max(1, min(int(top_k), 10))
            vector = (await self.embedder.embed([query]))[0]
            dense, bm25 = await __import__("asyncio").gather(
                self.index.dense_search(vector, 20), self.index.bm25_search(query, 20)
            )
            candidates = rrf([dense, bm25])[:30]
            # An empty knowledge base is a valid state during onboarding.  Do
            # not send an empty documents array to the remote reranker (some
            # providers reject it with HTTP 400); return an auditable empty hit
            # set and let the Agent continue with monitoring evidence.
            if not candidates:
                return ToolResult(ok=True, summary="知识库暂无相关内容", data=[])
            try:
                items = await self.reranker.rerank(query, candidates, top_k=top_k)
            except Exception:
                # Dense + BM25 is still useful when the optional remote reranker
                # is temporarily unavailable.  Preserve a stable response shape
                # so the Agent can cite the retrieved chunks and the trace can
                # show that this was a fallback result.
                items = [
                    dict(item, rerank_score=None, rerank_fallback=True)
                    for item in candidates[:top_k]
                ]
                items = await self._expand_context_safe(items)
                return ToolResult(
                    ok=True,
                    summary=f"知识库命中 {len(items)} 条（未完成重排，已使用混合检索结果）",
                    data=items,
                )
            if not items:
                items = [
                    dict(item, rerank_score=None, rerank_fallback=True)
                    for item in candidates[:top_k]
                ]
            items = await self._expand_context_safe(items)
            return ToolResult(ok=True, summary=f"知识库命中 {len(items)} 条", data=items)
        except Exception as e:
            return ToolResult(
                ok=False,
                summary="知识库检索不可用",
                error_code="RAG_UNAVAILABLE",
                data={"error": redact_text(str(e))},
            )
