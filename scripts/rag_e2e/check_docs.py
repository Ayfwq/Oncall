import asyncio
from sqlalchemy import text
from oncall.infrastructure.db.session import SessionFactory


async def main():
    async with SessionFactory() as db:
        rows = (await db.execute(text(
            "select d.title, d.status, v.status as vstatus, v.parser_version, "
            "(select count(*) from knowledge_chunks c where c.version_id = v.id) as chunks "
            "from knowledge_documents d join knowledge_document_versions v on v.document_id = d.id "
            "where d.title like '%.md' order by d.created_at desc"
        ))).all()
        for r in rows:
            print(r.title, '| doc:', r.status, '| ver:', r.vstatus, '| parser:', r.parser_version, '| chunks:', r.chunks)


asyncio.run(main())
