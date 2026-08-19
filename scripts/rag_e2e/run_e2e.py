"""RAG end-to-end validation: upload SOP docs via API -> poll jobs -> search -> citation check.

Run with:  uv run python scripts/rag_e2e/run_e2e.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import httpx

from oncall.bootstrap.config import get_settings

API = "http://127.0.0.1:9900"
DOCS_DIR = Path(__file__).parent / "docs"
USERNAME = "admin"
PASSWORD = os.environ.get("ONCALL_ADMIN_PASSWORD") or get_settings().admin_password

DOCS = [
    "cpu-high-load-sop.md",
    "postgresql-connection-refused-sop.md",
    "playwright-chromium-sop.md",
]

SEARCH_QUERIES = [
    ("CPU 负载很高怎么排查", "cpu-high-load"),
    ("load average 超过核数 4 倍 怎么办", "cpu-high-load"),
    ("PostgreSQL 连接被拒绝 connection refused 怎么处理", "postgresql"),
    ("SQLSTATE 28000 认证失败 密码错误", "postgresql"),
    ("playwright 报 Executable doesn't exist 浏览器缺失", "playwright"),
    ("libnss3 缺失 无法启动 chromium 沙箱", "playwright"),
    ("pg_isready 端口 5432 没监听 listen_addresses", "postgresql"),
]


def log(*args) -> None:
    print(f"[{time.strftime('%H:%M:%S')}]", *args, flush=True)


async def login(client: httpx.AsyncClient) -> None:
    r = await client.post(f"{API}/api/auth/login", json={"username": USERNAME, "password": PASSWORD})
    r.raise_for_status()
    log("login ok ->", r.json())


async def upload(client: httpx.AsyncClient, path: Path) -> dict:
    with path.open("rb") as f:
        r = await client.post(
            f"{API}/api/knowledge/documents",
            files={"file": (path.name, f, "text/markdown")},
        )
    r.raise_for_status()
    return r.json()


async def wait_job(client: httpx.AsyncClient, job_id: str, timeout: float = 180) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        r = await client.get(f"{API}/api/knowledge/jobs/{job_id}")
        r.raise_for_status()
        job = r.json()
        if job["status"] in ("done", "failed", "cancelled"):
            return job
        if time.monotonic() > deadline:
            raise TimeoutError(f"job {job_id} still {job['status']} after {timeout}s")
        await asyncio.sleep(2)


async def main() -> None:
    async with httpx.AsyncClient(base_url=API, timeout=30) as client:
        await login(client)
        log("uploading docs ...")
        jobs = []
        for name in DOCS:
            p = DOCS_DIR / name
            resp = await upload(client, p)
            log("uploaded", name, "-> version", resp["version_id"][:8], "job", resp["job_id"][:8])
            jobs.append((name, resp))

        log("waiting for ingest jobs ...")
        for name, resp in jobs:
            job = await wait_job(client, resp["job_id"])
            status = "OK" if job["status"] == "done" else f"FAIL: {job.get('last_error')}"
            log(f"  {name}: {status}")
            if job["status"] != "done":
                sys.exit(2)

        log("listing knowledge documents ...")
        docs = (await client.get(f"{API}/api/knowledge/documents")).json()
        for d in docs:
            log("  doc", d["id"][:8], d["title"], d["status"])

    # ---- direct retriever checks (same process, real Milvus) ----
    from oncall.rag.retrieval import KnowledgeRetriever

    log("running KnowledgeRetriever.search ...")
    retriever = KnowledgeRetriever()
    results = {}
    for query, tag in SEARCH_QUERIES:
        res = await retriever.search(query, project_id=None, top_k=5)
        results[tag] = res
        if not res.ok:
            log("  SEARCH FAILED:", query, "->", res.error_code, res.data)
            continue
        top = res.data[0] if res.data else None
        log(
            f"  [{tag}] hits={len(res.data)} top_title={top.get('title') if top else None} "
            f"rrf={top.get('rrf_score') if top else None} rerank={top.get('rerank_score') if top else None}"
        )

    # citation structure validation
    log("validating citation structure ...")
    required = {"document_id", "version_id", "id", "title", "page_range", "content"}
    ok_all = True
    for tag, res in results.items():
        if not res.ok or not res.data:
            ok_all = False
            log(f"  [{tag}] NO DATA")
            continue
        item = res.data[0]
        missing = required - set(item.keys())
        if missing:
            ok_all = False
            log(f"  [{tag}] MISSING FIELDS: {missing}")
        else:
            log(
                f"  [{tag}] citation complete: doc={item['document_id'][:8]} "
                f"ver={item['version_id'][:8]} chunk={item['id'][:8]} "
                f"title={item['title']!r} page_range={item['page_range']!r} content_len={len(item['content'])}"
            )
    if not ok_all:
        sys.exit(3)

    # ---- Milvus collection state ----
    from pymilvus import MilvusClient
    from oncall.rag.milvus_store import MilvusKnowledgeIndex

    idx = MilvusKnowledgeIndex()
    c = MilvusClient(uri=idx.settings.milvus_uri, token=idx.settings.milvus_token)
    log("milvus collections:", c.list_collections())
    stats = c.get_collection_stats(idx.collection)
    log(f"collection {idx.collection}: {stats}")

    out = {
        "queries": [{"query": q, "tag": t, "hit_count": len(r.data) if r.ok and r.data else 0} for (q, t), r in zip(SEARCH_QUERIES, results.values())],
        "collection": {"name": idx.collection, "stats": stats},
    }
    Path("scripts/rag_e2e/results.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    log("results written to scripts/rag_e2e/results.json")
    log("E2E PASS")


if __name__ == "__main__":
    asyncio.run(main())
