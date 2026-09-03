from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from oncall.application.agent_service import AgentService
from oncall.bootstrap.config import get_settings
from oncall.bootstrap.logging import configure_logging
from oncall.domain.enums import AgentMode
from oncall.infrastructure.db.session import SessionFactory
from oncall.jobs.queue import JobQueue

logger = logging.getLogger(__name__)


async def loop() -> None:
    settings = get_settings()
    checkpointer_cm = None
    checkpointer = None
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        checkpointer_cm = AsyncPostgresSaver.from_conn_string(settings.langgraph_database_url)
        checkpointer = await checkpointer_cm.__aenter__()
        await checkpointer.setup()
    except Exception as exc:
        logger.exception("checkpointer unavailable: %s", exc)

    try:
        while True:
            did_work = False
            async with SessionFactory() as db:
                queue = JobQueue(db)
                job = await queue.claim(["incident_investigate"], settings.job_lease_seconds)
                if job:
                    did_work = True
                    job_id = job.id
                    try:
                        conversation_id = UUID(job.payload["conversation_id"])
                        await AgentService(db, checkpointer).run(
                            conversation_id,
                            "请基于当前 Incident 主动调查并生成完整故障报告。",
                            channel="monitor",
                            mode=AgentMode.INVESTIGATE,
                        )
                        await queue.complete(job_id)
                        logger.info("incident investigation completed job=%s", job_id)
                    except Exception as exc:
                        await queue.fail(job_id, str(exc))
                        logger.exception("incident investigation failed job=%s: %s", job_id, exc)

                # Alert delivery is deliberately NOT done here. It now belongs to
                # oncall-notification-worker, so an alert queued the moment an Incident
                # is created goes out in seconds instead of waiting for this
                # investigation (which calls the LLM repeatedly and can take minutes).

            if not did_work:
                await asyncio.sleep(settings.job_poll_seconds)
    finally:
        if checkpointer_cm:
            await checkpointer_cm.__aexit__(None, None, None)


def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_dir, settings.log_retention_days)
    asyncio.run(loop())
