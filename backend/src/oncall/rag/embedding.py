from __future__ import annotations

import httpx

from oncall.bootstrap.config import get_settings


class EmbeddingProvider:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError


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
    if not s.embedding_base_url or not s.embedding_api_key:
        raise RuntimeError("remote embedding provider is not configured")
    return OpenAIEmbeddingProvider(s.embedding_base_url, s.embedding_api_key, s.embedding_model)
