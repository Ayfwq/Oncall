from __future__ import annotations

import asyncio
import logging
import time
from uuid import UUID

from oncall.bootstrap.config import get_settings
from oncall.bootstrap.logging import configure_logging
from oncall.infrastructure.db.session import SessionFactory
from oncall.jobs.queue import JobQueue
from oncall.rag.ingestion import KnowledgeIngestor

logger = logging.getLogger(__name__)


async def loop():
    s = get_settings()
    last_reconciled_at = 0.0
    reconcile_interval_seconds = 300
    while True:
        did_work = False
        try:
            async with SessionFactory() as db:
                ingestor = KnowledgeIngestor(db)
                now = time.monotonic()
                if now - last_reconciled_at >= reconcile_interval_seconds:
                    stats = await ingestor.reconcile_index()
                    logger.info("rag index reconciled stats=%s", stats)
                    last_reconciled_at = time.monotonic()

                q = JobQueue(db)
                job = await q.claim(["rag_ingest", "knowledge_reindex"], s.job_lease_seconds)
                if job:
                    did_work = True
                    job_id = job.id
                    try:
                        await ingestor.ingest_version(UUID(job.payload["version_id"]))
                        await q.complete(job_id)
                        logger.info("rag job completed job=%s", job_id)
                    except Exception as e:
                        await q.fail(job_id, str(e), 30)
                        logger.exception("rag job failed job=%s: %s", job_id, e)
        except asyncio.CancelledError:
            raise
        except Exception:
            # A transient database/Milvus/provider outage must not permanently
            # stop ingestion.  The durable job remains pending/running and is
            # reclaimed after its lease expires.
            last_reconciled_at = 0.0
            logger.exception("rag worker loop iteration failed")
            await asyncio.sleep(max(1.0, s.job_poll_seconds))
        if not did_work:
            await asyncio.sleep(s.job_poll_seconds)


def run():
    s = get_settings()
    configure_logging(s.log_level, s.log_dir, s.log_retention_days, "rag-worker")
    asyncio.run(loop())
