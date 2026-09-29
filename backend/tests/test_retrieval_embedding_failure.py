from types import SimpleNamespace

import httpx
import pytest
from oncall.rag.retrieval import KnowledgeRetriever


@pytest.mark.asyncio
async def test_embedding_failure_reports_unavailable_without_keyword_fallback():
    class Embedder:
        async def embed(self, _texts):
            request = httpx.Request("POST", "https://example.invalid/embeddings")
            response = httpx.Response(402, request=request)
            raise httpx.HTTPStatusError("payment required", request=request, response=response)

    class Index:
        async def bm25_search(self, _query, _limit):
            raise AssertionError("Knowledge search must stop when Embedding is unavailable")

        async def dense_search(self, _vector, _limit):
            raise AssertionError("Knowledge search must stop when Embedding is unavailable")

    retriever = KnowledgeRetriever.__new__(KnowledgeRetriever)
    retriever.embedder = Embedder()
    retriever.index = Index()
    retriever.session = None
    retriever.settings = SimpleNamespace(knowledge_context_radius=0)

    result = await retriever.search("Docker cAdvisor", top_k=5)
    assert not result.ok
    assert result.error_code == "EMBEDDING_UNAVAILABLE"
    assert "HTTP 402" in result.summary
