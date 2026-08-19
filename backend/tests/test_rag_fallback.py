import pytest

from oncall.rag.embedding import HashEmbeddingProvider
from oncall.rag.rerank import lexical_score


@pytest.mark.asyncio
async def test_hash_embedding_handles_unsegmented_chinese():
    vec = (await HashEmbeddingProvider(64).embed(['CPU使用率过高处理方案']))[0]
    assert any(abs(x) > 0 for x in vec)
    assert abs(sum(x * x for x in vec) - 1.0) < 1e-6


def test_lexical_fallback_has_chinese_overlap():
    assert lexical_score('CPU使用率过高', 'CPU 使用率过高告警处理方案') > 0
