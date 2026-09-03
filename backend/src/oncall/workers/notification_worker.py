from __future__ import annotations

import asyncio
import logging

from oncall.bootstrap.config import get_settings
from oncall.bootstrap.logging import configure_logging
from oncall.channels.feishu import FeishuOutboxSender
from oncall.infrastructure.db.session import SessionFactory

logger = logging.getLogger(__name__)


async def loop() -> None:
    """Delivery-only worker: drains the notification outbox and nothing else.

    This exists because alert delivery must never sit behind slow work. Previously
    the outbox was drained inside the Agent worker loop, immediately after a job that
    calls the LLM several times and can run for minutes. An alert queued at T was
    therefore not delivered until the investigation finished at T+minutes, which
    defeats the point of alerting.

    Keeping delivery in its own process also means a crash or backlog in the Agent
    worker cannot stop alerts from going out.
    """
    settings = get_settings()
    idle_seconds = max(0.2, settings.notification_poll_seconds)
    while True:
        try:
            async with SessionFactory() as db:
                sent = await FeishuOutboxSender(db).send_pending()
            if sent:
                logger.info("notification outbox delivered count=%s", sent)
        except Exception as exc:
            # Never let a transient DB or Feishu error kill the loop.
            logger.exception("notification worker iteration failed: %s", exc)
        await asyncio.sleep(idle_seconds)


def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_dir, settings.log_retention_days)
    asyncio.run(loop())


if __name__ == '__main__':  # pragma: no cover
    run()
