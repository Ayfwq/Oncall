from __future__ import annotations

import hashlib
import math
import re

import httpx

from oncall.bootstrap.config import get_settings


def _tokens(text: str) -> list[str]:
    """Deterministic multilingual tokens for the development-only hash embedder."""
    lowered = text.lower()
    latin = re.findall(r"[a-z0-9_./:-]+", lowered)
    han = re.findall(r"[\u4e00-\u9fff]", lowered)
    han_bigrams = ["".join(han[i:i + 2]) for i in range(max(0, len(han) - 1))]
    return latin + han + han_bigrams


class EmbeddingProvider:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError


class HashEmbeddingProvider(EmbeddingProvider):
    """Offline deterministic fallback; never use it as the production embedding model."""

    def __init__(self, dimension: int):
        self.dimension = dimension

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for text in texts:
            vec = [0.0] * self.dimension
            for token in _tokens(text):
                h = int(hashlib.sha256(token.encode()).hexdigest(), 16)
                idx = h % self.dimension
                vec[idx] += 1.0 if (h >> 8) & 1 else -1.0
            norm = math.sqrt(sum(x * x for x in vec)) or 1.0
            out.append([x / norm for x in vec])
        return out


class OpenAIEmbeddingProvider(EmbeddingProvider):
    def __init__(self, base_url: str, api_key: str, model: str):
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.model = model

    async def embed(self, texts: list[str]) -> list[list[float]]:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                f'{self.base_url}/embeddings',
                headers={'Authorization': f'Bearer {self.api_key}'},
                json={'model': self.model, 'input': texts},
            )
            r.raise_for_status()
            data = r.json()['data']
            return [x['embedding'] for x in sorted(data, key=lambda x: x.get('index', 0))]


def get_embedding_provider() -> EmbeddingProvider:
    s = get_settings()
    # A remote embedding provider requires a dedicated embeddings endpoint. Falling
    # back to the chat model base URL (when only ONCALL_EMBEDDING_API_KEY is set)
    # silently points /embeddings at a server that returns 503. Without a dedicated
    # endpoint we use the deterministic offline hash embedder (dev fallback).
    base = s.embedding_base_url
    key = s.embedding_api_key
    return (
        OpenAIEmbeddingProvider(base, key, s.embedding_model)
        if base and key
        else HashEmbeddingProvider(s.embedding_dimension)
    )
