import asyncio
from sqlalchemy import text
from oncall.infrastructure.db.session import SessionFactory


async def main():
    async with SessionFactory() as db:
        rows = (await db.execute(text(
            "select d.id, d.title, d.status, u.username, d.created_at "
            "from knowledge_documents d join users u on u.id = d.user_id "
            "where d.title like '%.md' order by d.created_at desc"
        ))).all()
        for r in rows:
            print(r.id, '|', r.title, '|', r.status, '| user:', r.username, '|', r.created_at)


asyncio.run(main())
