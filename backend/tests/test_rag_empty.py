"""Small RAG edge-case contracts that do not require Milvus or a remote API."""

from oncall.rag.rerank import Reranker


async def test_reranker_skips_empty_candidate_list():
    # An empty knowledge base is expected during initial deployment.  The
    # reranker must not make an HTTP request with an empty documents array.
    assert await Reranker().rerank("any query", [], top_k=5) == []
