"""Shared fixtures for the monitoring test suite.

PostgreSQL-backed tests use the real local development database (see
oncall.infrastructure.db.session).  They are skipped automatically when the
database is unreachable so the suite still passes in offline sandboxes.
Every test gets its own user; teardown deletes the user, which cascades to the
project, rules, rule states, runs, samples, incidents and conversations.
"""
from __future__ import annotations

import uuid

import pytest
from oncall.infrastructure.db.models import BackgroundJob, Incident, Project, User
from oncall.security.passwords import hash_password
from sqlalchemy import delete, select

# Project root (backend/tests/monitoring/conftest.py -> repo root)
REPO_ROOT = __import__("pathlib").Path(__file__).resolve().parents[3]


async def _probe() -> bool:
    """Probe PostgreSQL with a dedicated asyncpg connection so the shared
    SQLAlchemy pool never holds a connection bound to a throwaway event loop."""
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


@pytest.fixture(scope="session")
def pg_ready() -> bool:
    """Probe the local PostgreSQL once per session."""
    import asyncio

    try:
        return asyncio.run(_probe())
    except Exception:
        return False


@pytest.fixture
async def db(pg_ready, service_gate):
    service_gate(pg_ready, "PostgreSQL unreachable; skipping database-backed test")
    from oncall.infrastructure.db.session import SessionFactory

    async with SessionFactory() as session:
        yield session


@pytest.fixture
async def test_user(db) -> User:
    """A dedicated user per test; deleted on teardown (cascades to all data)."""
    username = f"test_{uuid.uuid4().hex[:12]}"
    user = User(username=username, password_hash=hash_password("test-password"))
    db.add(user)
    await db.commit()
    await db.refresh(user)
    yield user
    # Remove durable jobs referencing incidents owned by this user before the
    # cascade wipes the incidents themselves (background_jobs has no FK).
    project_ids = list(
        (await db.scalars(select(Project.id).where(Project.user_id == user.id))).all()
    )
    if project_ids:
        inc_ids = list(
            (
                await db.scalars(
                    select(Incident.id).where(Incident.project_id.in_(project_ids))
                )
            ).all()
        )
        for inc_id in inc_ids:
            await db.execute(
                delete(BackgroundJob).where(
                    BackgroundJob.payload["incident_id"].astext == str(inc_id)
                )
            )
    await db.delete(user)
    await db.commit()


@pytest.fixture
def repo_root() -> __import__("pathlib").Path:
    return REPO_ROOT
