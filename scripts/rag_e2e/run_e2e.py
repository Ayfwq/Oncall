"""RAG end-to-end validation: upload SOP docs via API -> poll jobs -> search -> citation check.

Run with:  uv run --no-sync python scripts/rag_e2e/run_e2e.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import httpx

API = "http://127.0.0.1:9900"
DOCS_DIR = Path(__file__).parent / "docs"

DOCS = [
    "cpu-high-load-sop.md",
    "postgresql-connection-refused-sop.md",
    "playwright-chromium-sop.md",
]

SEARCH_QUERIES = [
    ("CPU 负载很高怎么排查", "cpu-high-load-steps", "cpu-high-load-sop.md", "CPU 高负载"),
    (
        "load average 超过核数 4 倍 怎么办",
        "cpu-high-load-alert",
        "cpu-high-load-sop.md",
        "告警触发条件",
    ),
    (
        "PostgreSQL 连接被拒绝 connection refused 怎么处理",
        "postgresql-connection",
        "postgresql-connection-refused-sop.md",
        "connection refused",
    ),
    (
        "SQLSTATE 28000 认证失败 密码错误",
        "postgresql-auth",
        "postgresql-connection-refused-sop.md",
        "pg_hba",
    ),
    (
        "playwright 报 Executable doesn't exist 浏览器缺失",
        "playwright-executable",
        "playwright-chromium-sop.md",
        "Executable",
    ),
    (
        "libnss3 缺失 无法启动 chromium 沙箱",
        "playwright-dependency",
        "playwright-chromium-sop.md",
        "libnss3",
    ),
    (
        "pg_isready 端口 5432 没监听 listen_addresses",
        "postgresql-listen",
        "postgresql-connection-refused-sop.md",
        "listen_addresses",
    ),
]


def log(*args) -> None:
    print(f"[{time.strftime('%H:%M:%S')}]", *args, flush=True)


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
    results = []
    retrieval_ok = True
    for query, tag, expected_title, marker in SEARCH_QUERIES:
        res = await retriever.search(query, top_k=5)
        results.append((query, tag, res))
        if not res.ok:
            log("  SEARCH FAILED:", query, "->", res.error_code, res.data)
            retrieval_ok = False
            continue
        top = res.data[0] if res.data else None
        log(
            f"  [{tag}] hits={len(res.data)} top_title={top.get('title') if top else None} "
            f"rrf={top.get('rrf_score') if top else None} rerank={top.get('rerank_score') if top else None}"
        )
        if not res.data or expected_title not in [x.get("title") for x in res.data]:
            retrieval_ok = False
            log(f"  [{tag}] WRONG DOCUMENT: expected {expected_title!r}")
        if not any(marker in x.get("content", "") for x in res.data[:3]):
            retrieval_ok = False
            log(f"  [{tag}] MISSING MARKER IN TOP-3: {marker!r}")

    # citation structure validation
    log("validating citation structure ...")
    required = {"document_id", "version_id", "id", "title", "page_range", "content"}
    ok_all = True
    for _query, tag, res in results:
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
    if not retrieval_ok or not ok_all:
        sys.exit(3)

    # ---- Milvus collection state ----
    from oncall.rag.milvus_store import MilvusKnowledgeIndex
    from pymilvus import MilvusClient

    idx = MilvusKnowledgeIndex()
    c = MilvusClient(uri=idx.settings.milvus_uri, token=idx.settings.milvus_token)
    log("milvus collections:", c.list_collections())
    stats = c.get_collection_stats(idx.collection)
    log(f"collection {idx.collection}: {stats}")

    out = {
        "queries": [
            {
                "query": query,
                "tag": tag,
                "expected_title": expected_title,
                "expected_marker": marker,
                "hit_count": len(res.data) if res.ok and res.data else 0,
            }
            for (query, tag, expected_title, marker), (_, _, res) in zip(
                SEARCH_QUERIES, results, strict=True
            )
        ],
        "collection": {"name": idx.collection, "stats": stats},
    }
    Path("scripts/rag_e2e/results.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log("results written to scripts/rag_e2e/results.json")
    log("E2E PASS")


if __name__ == "__main__":
    asyncio.run(main())
