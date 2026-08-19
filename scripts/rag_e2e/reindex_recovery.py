"""Validate the drop-collection -> reindex -> recovery path.

1. Drop the Milvus collection with MilvusClient (simulating index loss).
2. Call the API reindex endpoint for each SOP doc (worker re-runs ingest_version,
   which recreates the collection via ensure() and re-upserts chunks).
3. Assert the collection is back with the same chunks and search works again.

Run with:  uv run --no-sync python scripts/rag_e2e/reindex_recovery.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import time

import httpx

from oncall.bootstrap.config import get_settings

API = "http://127.0.0.1:9900"
USERNAME = "admin"
PASSWORD = os.environ.get("ONCALL_ADMIN_PASSWORD") or get_settings().admin_password

EXPECTED_TITLES = [
    "cpu-high-load-sop.md",
    "postgresql-connection-refused-sop.md",
    "playwright-chromium-sop.md",
]


def log(*args) -> None:
    print(f"[{time.strftime('%H:%M:%S')}]", *args, flush=True)


async def wait_job(client: httpx.AsyncClient, job_id: str, timeout: float = 180) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        r = await client.get(f"{API}/api/knowledge/jobs/{job_id}")
        r.raise_for_status()
        job = r.json()
        if job["status"] in ("done", "failed", "cancelled"):
            return job
        if time.monotonic() > deadline:
            raise TimeoutError(f"job {job_id} still {job['status']}")
        await asyncio.sleep(2)


async def main() -> None:
    from pymilvus import MilvusClient

    from oncall.rag.milvus_store import MilvusKnowledgeIndex

    idx = MilvusKnowledgeIndex()
    client = MilvusClient(uri=idx.settings.milvus_uri, token=idx.settings.milvus_token)
    collection = idx.collection

    if not client.has_collection(collection):
        log("collection", collection, "missing before test; aborting")
        sys.exit(1)
    before = len(client.query(collection_name=collection, filter="", output_fields=["id"], limit=1000))
    log("before drop: entities =", before)

    # ---- 1. simulate index loss ----
    client.drop_collection(collection)
    assert not client.has_collection(collection)
    log("collection dropped:", collection, "-> exists =", client.has_collection(collection))

    # ---- 2. reindex via the API (worker consumes knowledge_reindex) ----
    async with httpx.AsyncClient(base_url=API, timeout=30) as hx:
        r = await hx.post(f"{API}/api/auth/login", json={"username": USERNAME, "password": PASSWORD})
        r.raise_for_status()
        docs = (await hx.get(f"{API}/api/knowledge/documents")).json()
        by_title = {d["title"]: d for d in docs}
        missing = [t for t in EXPECTED_TITLES if t not in by_title]
        assert not missing, f"docs missing: {missing}"
        log("reindexing", len(EXPECTED_TITLES), "docs via API ...")
        for title in EXPECTED_TITLES:
            doc = by_title[title]
            r = await hx.post(f"{API}/api/knowledge/documents/{doc['id']}/reindex")
            r.raise_for_status()
            job = await wait_job(hx, r.json()["job_id"])
            status = "OK" if job["status"] == "done" else f"FAIL: {job.get('last_error')}"
            log(f"  {title}: {status}")
            if job["status"] != "done":
                sys.exit(2)

    # ---- 3. assert recovery ----
    assert client.has_collection(collection), "collection not recreated"
    rows = client.query(collection_name=collection, filter="", output_fields=["title"], limit=1000)
    after = len(rows)
    log("after reindex: entities =", after, "(expect", before, ")")
    assert after == before, f"entity count mismatch: {before} -> {after}"
    titles = {r["title"] for r in rows}
    for t in EXPECTED_TITLES:
        assert t in titles, f"{t} missing after recovery"

    from oncall.rag.retrieval import KnowledgeRetriever

    res = await KnowledgeRetriever().search("SQLSTATE 28000 认证失败 密码错误", project_id=None, top_k=5)
    assert res.ok and res.data, res
    top = res.data[0]
    log("recovery search ok: top_title =", top.get("title"), "| rerank =", top.get("rerank_score"))
    assert top.get("title") == "postgresql-connection-refused-sop.md"
    log("RECOVERY PASS")


if __name__ == "__main__":
    asyncio.run(main())
