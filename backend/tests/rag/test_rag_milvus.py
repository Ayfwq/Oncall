"""RAG integration regression tests against the live local dev stack.

Covered chain: docling HybridChunker -> hash embedding -> Milvus dense + BM25
-> RRF fusion -> lexical rerank -> citation-shaped hits -> RetrievalTrace.
Uses the three SOP fixtures ingested by the `rag_kb` session fixture, so the
fixed queries below are deterministic regression fixtures.

Run with:  uv run --no-sync pytest backend/tests/rag -q
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, select

from oncall.infrastructure.db.models import AgentRun, Conversation, RetrievalTrace
from oncall.rag.embedding import HashEmbeddingProvider, get_embedding_provider
from oncall.rag.milvus_store import MilvusKnowledgeIndex
from oncall.rag.retrieval import KnowledgeRetriever, rrf

# (query, expected top document, section/content marker that must be in the
#  top hits) -- covers natural language, error codes, tools, proprietary terms.
REGRESSION_QUERIES = [
    ("CPU 负载很高怎么排查", "cpu-high-load-sop.md", "CPU 高负载"),
    ("load average 超过核数 4 倍 怎么办", "cpu-high-load-sop.md", "告警触发条件"),
    ("PostgreSQL 连接被拒绝 connection refused 怎么处理", "postgresql-connection-refused-sop.md", "connection refused"),
    ("SQLSTATE 28000 认证失败 密码错误", "postgresql-connection-refused-sop.md", "pg_hba"),
    ("pg_isready 端口 5432 没监听 listen_addresses", "postgresql-connection-refused-sop.md", "listen_addresses"),
    ("playwright 报 Executable doesn't exist 浏览器缺失", "playwright-chromium-sop.md", "Executable"),
    ("libnss3 缺失 无法启动 chromium 沙箱", "playwright-chromium-sop.md", "libnss3"),
]

CITATION_FIELDS = {"id", "document_id", "version_id", "title", "page_range", "content"}


def _hit_titles(res) -> list[str]:
    return [h.get("title", "") for h in (res.data or [])]


def _hit_contents(res) -> list[str]:
    return [h.get("content", "") for h in (res.data or [])]


@pytest.mark.parametrize("query,expected_title,marker", REGRESSION_QUERIES)
@pytest.mark.rag
async def test_retrieval_hits_expected_document(rag_kb, query, expected_title, marker):
    res = await KnowledgeRetriever().search(query, project_id=None, top_k=5)
    assert res.ok, (res.error_code, res.data)
    assert res.data and len(res.data) > 0, "expected at least one hit"
    assert expected_title in _hit_titles(res), f"expected {expected_title!r} in top hits: {_hit_titles(res)}"
    assert any(marker in content for content in _hit_contents(res)[:3]), (
        f"expected section marker {marker!r} in top-3 contents"
    )


@pytest.mark.rag
async def test_citation_structure_complete(rag_kb):
    retriever = KnowledgeRetriever()
    for query, *_ in REGRESSION_QUERIES:
        res = await retriever.search(query, project_id=None, top_k=5)
        assert res.ok
        assert res.data, query
        hit = res.data[0]
        missing = CITATION_FIELDS - set(hit.keys())
        assert not missing, f"{query}: citation missing {missing}"
        # ids must be valid uuids (stored as strings)
        for key in ("id", "document_id", "version_id"):
            uuid.UUID(hit[key])
        assert isinstance(hit["title"], str) and hit["title"]
        assert isinstance(hit["content"], str) and hit["content"].strip()
        assert hit["rrf_score"] > 0, f"{query}: rrf score missing"
        assert "rerank_score" in hit, f"{query}: rerank score missing"
        # page_range may be '' for markdown fixtures but the key must exist
        assert isinstance(hit["page_range"], str)


@pytest.mark.rag
async def test_search_knowledge_tool_writes_retrieval_trace(rag_kb, db):
    """The search_knowledge tool path must persist citation refs to RetrievalTrace."""
    user_id = rag_kb["user_id"]
    conv = Conversation(user_id=user_id, type="chat", title="rag-tool-test")
    db.add(conv)
    await db.commit()
    await db.refresh(conv)
    run = AgentRun(mode="proactive", conversation_id=conv.id, status="running")
    db.add(run)
    await db.commit()
    await db.refresh(run)
    conv_id, run_id = conv.id, run.id

    from oncall.agent.tool_registry import ToolExecutionContext, ToolRegistry

    ctx = ToolExecutionContext(project_id=None, incident_id=None, agent_run_id=run_id)
    result = await ToolRegistry(db).execute("search_knowledge", {"query": "PostgreSQL connection refused 怎么处理"}, ctx)
    assert result.ok, (result.error_code, result.data)
    assert isinstance(result.data, list) and result.data

    trace = await db.scalar(select(RetrievalTrace).where(RetrievalTrace.agent_run_id == run_id))
    assert trace is not None, "RetrievalTrace row not persisted"
    assert trace.query and trace.hit_count > 0
    assert trace.refs, "citation refs must not be empty"
    ref_required = {"chunk_id", "document_id", "version_id", "title", "page_range", "score"}
    first = trace.refs[0]
    missing = ref_required - set(first.keys())
    assert not missing, f"trace ref missing fields: {missing}"
    assert first["title"], "ref title must be populated"
    assert first["chunk_id"] and first["document_id"], "ref ids must be populated"

    # cleanup
    await db.execute(delete(RetrievalTrace).where(RetrievalTrace.agent_run_id == run_id))
    await db.execute(delete(AgentRun).where(AgentRun.id == run_id))
    await db.execute(delete(Conversation).where(Conversation.id == conv_id))
    await db.commit()


@pytest.mark.rag
async def test_collection_exists_with_entities(rag_kb):
    idx = MilvusKnowledgeIndex()
    assert idx.collection == "oncall_knowledge_v1_1536"
    client = idx._client()
    assert client.has_collection(idx.collection)
    rows = client.query(collection_name=idx.collection, filter="", output_fields=["title"], limit=1000)
    assert len(rows) >= len(rag_kb["titles"]) * 5, f"expected >= {len(rag_kb['titles']) * 5} chunks, got {len(rows)}"
    titles = {r["title"] for r in rows}
    for t in rag_kb["titles"]:
        assert t in titles, f"fixture doc {t!r} not in Milvus collection"


def test_rrf_merges_rankings():
    dense = [{"id": "a", "score": 0.9}, {"id": "b", "score": 0.8}]
    bm25 = [{"id": "b", "score": 5.0}, {"id": "c", "score": 3.0}]
    merged = rrf([dense, bm25], k=60)
    assert {m["id"] for m in merged} == {"a", "b", "c"}
    by_id = {m["id"]: m for m in merged}
    # b is ranked 2nd in both lists -> highest reciprocal rank
    assert by_id["b"]["rrf_score"] > by_id["a"]["rrf_score"]
    assert by_id["a"]["rrf_score"] > by_id["c"]["rrf_score"]
    # original fields are preserved on the merged item
    assert by_id["b"]["score"] == 0.8


def test_embedding_hash_fallback_contract():
    """Without a dedicated embedding base URL the provider must be the offline
    deterministic hash embedder (dev fallback), never a remote call."""
    from oncall.bootstrap.config import get_settings

    s = get_settings()
    provider = get_embedding_provider()
    if not s.embedding_base_url:
        assert isinstance(provider, HashEmbeddingProvider)
    assert isinstance(provider, HashEmbeddingProvider)
