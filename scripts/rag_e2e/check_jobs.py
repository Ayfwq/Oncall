import asyncio

from oncall.infrastructure.db.session import SessionFactory
from sqlalchemy import text


async def main():
    async with SessionFactory() as db:
        rows = (
            await db.execute(
                text(
                    "select type, status, attempts, last_error from background_jobs order by created_at desc limit 8"
                )
            )
        ).all()
        for r in rows:
            print(r.type, r.status, "attempts=", r.attempts, "err=", (r.last_error or "")[:300])


asyncio.run(main())
