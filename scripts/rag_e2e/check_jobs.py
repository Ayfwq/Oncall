import asyncio
from sqlalchemy import text
from oncall.infrastructure.db.session import SessionFactory


async def main():
    async with SessionFactory() as db:
        rows = (await db.execute(text(
            "select type, status, attempts, last_error from background_jobs order by created_at desc limit 8"
        ))).all()
        for r in rows:
            print(r.type, r.status, 'attempts=', r.attempts, 'err=', (r.last_error or '')[:300])


asyncio.run(main())
