"""Shared fixtures for the RAG integration test suite.

These tests exercise the full RAG pipeline against the real local dev stack
(PostgreSQL + Milvus + docling + hash embeddings).  They are skipped
automatically when PostgreSQL or Milvus is unreachable, and every run ingests
the three SOP fixtures under a dedicated throwaway user so the suite is
self-contained and idempotent (upload is deduplicated by checksum).
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
SOP_FILES = [
    "cpu-high-load-sop.md",
    "postgresql-connection-refused-sop.md",
    "playwright-chromium-sop.md",
]


async def _pg_probe() -> bool:
    import asyncpg
    from oncall.bootstrap.config import get_settings
    from sqlalchemy.engine import make_url

    url = make_url(get_settings().database_url)
    conn = None
    try:
        conn = await asyncpg.connect(
            host=url.host, port=url.port, database=url.database,
            user=url.username, password=url.password or "", timeout=3,
        )
        await conn.fetchval("select 1")
        return True
    except Exception:
        return False
    finally:
        if conn is not None:
            await conn.close()


def _milvus_probe() -> bool:
    from oncall.rag.milvus_store import MilvusKnowledgeIndex

    idx = MilvusKnowledgeIndex()
    try:
        idx._client().list_collections()
        return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def rag_ready() -> bool:
    import asyncio

    try:
        pg = asyncio.run(_pg_probe())
    except Exception:
        pg = False
    return pg and _milvus_probe()


@pytest.fixture
async def db(rag_ready, service_gate):
    service_gate(rag_ready, "PostgreSQL/Milvus unreachable; skipping RAG integration test")
    from oncall.infrastructure.db.session import SessionFactory

    async with SessionFactory() as session:
        yield session


@pytest.fixture(scope="session")
async def rag_kb(rag_ready, service_gate):
    """Ingest the three SOP fixtures under a dedicated user; cleaned up after."""
    service_gate(rag_ready, "PostgreSQL/Milvus unreachable; skipping RAG integration test")
    from oncall.infrastructure.db.models import User
    from oncall.infrastructure.db.session import SessionFactory
    from oncall.rag.ingestion import KnowledgeIngestor
    from oncall.rag.milvus_store import MilvusKnowledgeIndex
    from oncall.security.passwords import hash_password
    from sqlalchemy import delete

    username = f"ragtest_{uuid.uuid4().hex[:8]}"
    async with SessionFactory() as session:
        user = User(username=username, password_hash=hash_password("test-password"))
        session.add(user)
        await session.commit()
        await session.refresh(user)
        user_id = user.id
        ingestor = KnowledgeIngestor(session)
        versions = []
        try:
            for name in SOP_FILES:
                ver = await ingestor.register_upload(user_id, FIXTURES / name, title=name)
                await ingestor.ingest_version(ver.id)
                versions.append(ver)
        except Exception:
            # best-effort cleanup on setup failure
            index = MilvusKnowledgeIndex()
            for ver in versions:
                try:
                    await index.delete_version(str(ver.id))
                except Exception:
                    pass
            await session.execute(delete(User).where(User.id == user_id))
            await session.commit()
            raise
        yield {"user_id": user_id, "versions": versions, "titles": SOP_FILES}
        index = MilvusKnowledgeIndex()
        for ver in versions:
            try:
                await index.delete_version(str(ver.id))
            except Exception:
                pass
        await session.execute(delete(User).where(User.id == user_id))
        await session.commit()
