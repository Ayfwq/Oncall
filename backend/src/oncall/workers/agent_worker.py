from __future__ import annotations

import asyncio
from uuid import UUID

from oncall.application.agent_service import AgentService
from oncall.bootstrap.config import get_settings
from oncall.channels.feishu import FeishuOutboxSender
from oncall.domain.enums import AgentMode
from oncall.infrastructure.db.session import SessionFactory
from oncall.jobs.queue import JobQueue


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
        print("checkpointer unavailable", exc)

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
                    except Exception as exc:
                        await queue.fail(job_id, str(exc))

                # Delivery is an outbox operation. Agent completion and Feishu network I/O
                # are deliberately decoupled so transient Feishu failures do not lose reports.
                try:
                    sent = await FeishuOutboxSender(db).send_pending()
                    did_work = did_work or sent > 0
                except Exception as exc:
                    print("feishu outbox error", exc)

            if not did_work:
                await asyncio.sleep(settings.job_poll_seconds)
    finally:
        if checkpointer_cm:
            await checkpointer_cm.__aexit__(None, None, None)


def run() -> None:
    asyncio.run(loop())
