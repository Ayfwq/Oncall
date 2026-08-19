from __future__ import annotations

import re

import httpx

from oncall.bootstrap.config import get_settings


def _tokens(text: str) -> set[str]:
    lowered = text.lower()
    tokens = set(re.findall(r"[a-z0-9_./:-]+", lowered))
    han = re.findall(r"[\u4e00-\u9fff]", lowered)
    tokens.update(han)
    tokens.update("".join(han[i:i + 2]) for i in range(max(0, len(han) - 1)))
    return tokens


def lexical_score(query: str, text: str) -> float:
    q = _tokens(query)
    t = _tokens(text)
    return len(q & t) / (len(q) or 1)


class Reranker:
    async def rerank(self, query: str, items: list[dict], top_k: int = 5) -> list[dict]:
        s = get_settings()
        if not s.rerank_base_url or not s.rerank_api_key or not s.rerank_model:
            ranked = []
            for item in items:
                copy = dict(item)
                copy['rerank_score'] = lexical_score(query, copy.get('content', ''))
                ranked.append(copy)
            return sorted(
                ranked,
                key=lambda x: (x.get('rrf_score', 0), x.get('rerank_score', 0)),
                reverse=True,
            )[:top_k]
        payload = {
            'model': s.rerank_model,
            'query': query,
            'documents': [x['content'] for x in items],
            'top_n': top_k,
        }
        async with httpx.AsyncClient(timeout=45) as client:
            r = await client.post(
                s.rerank_base_url,
                headers={'Authorization': f'Bearer {s.rerank_api_key}'},
                json=payload,
            )
            r.raise_for_status()
            res = r.json().get('results', [])
        out = []
        for x in res:
            item = dict(items[x['index']])
            item['rerank_score'] = x.get('relevance_score', 0)
            out.append(item)
        return out
