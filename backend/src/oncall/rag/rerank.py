from __future__ import annotations

import httpx

from oncall.bootstrap.config import get_settings


class Reranker:
    async def rerank(self, query: str, items: list[dict], top_k: int = 5) -> list[dict]:
        if not items:
            return []
        s = get_settings()
        if not s.rerank_base_url or not s.rerank_api_key or not s.rerank_model:
            raise RuntimeError("remote reranker is not configured")
        payload = {
            "model": s.rerank_model,
            "query": query,
            "documents": [x["content"] for x in items],
            "top_n": top_k,
        }
        async with httpx.AsyncClient(timeout=45) as client:
            r = await client.post(
                s.rerank_base_url,
                headers={"Authorization": f"Bearer {s.rerank_api_key}"},
                json=payload,
            )
            r.raise_for_status()
            res = r.json().get("results", [])
        out = []
        for x in res:
            if not isinstance(x, dict):
                continue
            index = x.get("index")
            if not isinstance(index, int) or not 0 <= index < len(items):
                continue
            item = dict(items[index])
            item["rerank_score"] = float(x.get("relevance_score", 0) or 0)
            out.append(item)
        return out
