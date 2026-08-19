from __future__ import annotations

import asyncio
from uuid import UUID
from oncall.bootstrap.config import get_settings
from oncall.infrastructure.db.session import SessionFactory
from oncall.jobs.queue import JobQueue
from oncall.rag.ingestion import KnowledgeIngestor

async def loop():
    s=get_settings()
    while True:
        did_work=False
        async with SessionFactory() as db:
            q=JobQueue(db);job=await q.claim(['rag_ingest','knowledge_reindex'],s.job_lease_seconds)
            if job:
                did_work=True
                job_id=job.id
                try:
                    await KnowledgeIngestor(db).ingest_version(UUID(job.payload['version_id']));await q.complete(job_id)
                except Exception as e:
                    await q.fail(job_id,str(e),30)
        if not did_work:await asyncio.sleep(s.job_poll_seconds)

def run():asyncio.run(loop())
