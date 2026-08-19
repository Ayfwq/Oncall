"""POST /api/projects/{id}/test is a true dry-run.

Verified at the live HTTP layer (skip when the API is not running): the response
carries real collected signals (host.cpu.percent included), and the call must
not write MonitoringRun / MetricSample / MonitoringRuleState rows nor advance a
persisted log cursor.
"""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from oncall.bootstrap.config import get_settings
from oncall.infrastructure.db.models import (
    LogCursor,
    MetricSample,
    MonitoringRuleState,
    MonitoringRun,
    Project,
    User,
)
from oncall.monitoring.engine import MonitoringEngine
from sqlalchemy import func, select

ROOT = Path(__file__).resolve().parents[3]
API = "http://127.0.0.1:9900"

pytestmark = pytest.mark.integration


async def _api_up() -> bool:
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            r = await client.get(f"{API}/api/health")
        return r.status_code == 200
    except Exception:
        return False


@pytest.fixture
async def api_client(pg_ready):
    if not await _api_up():
        pytest.skip("live API not reachable")
    s = get_settings()
    async with httpx.AsyncClient(base_url=API, timeout=30, follow_redirects=True) as client:
        r = await client.post("/api/auth/login", json={"username": s.admin_username, "password": s.admin_password})
        assert r.status_code == 200, f"login failed: {r.status_code} {r.text[:200]}"
        yield client


async def _count(db, model, project_id):
    return await db.scalar(select(func.count()).select_from(model).where(model.project_id == project_id))


async def _count_states(db, project_id):
    from oncall.infrastructure.db.models import MonitoringRule

    return await db.scalar(
        select(func.count())
        .select_from(MonitoringRuleState)
        .join(MonitoringRule, MonitoringRule.id == MonitoringRuleState.rule_id)
        .where(MonitoringRule.project_id == project_id)
    )


async def test_http_test_endpoint_returns_real_signals_without_persistence(db, api_client):
    r = await api_client.post(
        "/api/projects",
        json={
            "name": "dryrun-http",
            "enabled": False,
            "poll_interval": 30,
            "rules": [
                {
                    "metric_key": "host.cpu.percent",
                    "resource_key": "default",
                    "operator": ">",
                    "trigger_threshold": 200.0,
                    "trigger_for": 2,
                    "recovery_threshold": 0.0,
                    "recovery_for": 2,
                    "severity": "warning",
                }
            ],
        },
    )
    assert r.status_code == 200, r.text[:300]
    pid = r.json()["id"]
    project_id = __import__("uuid").UUID(pid)
    try:
        t = await api_client.post(f"/api/projects/{pid}/test")
        assert t.status_code == 200, t.text[:500]
        body = t.json()
        signals = body.get("signals", {})
        cpu = signals.get("host.cpu.percent")
        assert isinstance(cpu, int | float), f"host.cpu.percent missing/not numeric: {signals}"
        assert 0 <= float(cpu) <= 100
        assert body.get("collector_status"), "collector_status missing from dry-run response"
        # no persistence side effects
        assert await _count(db, MonitoringRun, project_id) == 0
        assert await _count(db, MetricSample, project_id) == 0
        assert await _count_states(db, project_id) == 0
        # detector state untouched: evaluating the same snapshot later must start at NORMAL
    finally:
        await db.execute(
            __import__("sqlalchemy").delete(Project).where(Project.id == project_id)
        )
        await db.commit()


async def test_http_test_does_not_advance_log_cursor(db, api_client):
    s = get_settings()
    admin_id = await db.scalar(select(User.id).where(User.username == s.admin_username))
    log_path = str(ROOT / "VALIDATION.md")
    r = await api_client.post(
        "/api/projects",
        json={
            "name": "dryrun-cursor",
            "enabled": False,
            "poll_interval": 30,
            "log_sources": [{"path": log_path, "encoding": "utf-8"}],
            "rules": [
                {
                    "metric_key": "host.cpu.percent",
                    "resource_key": "default",
                    "operator": ">",
                    "trigger_threshold": 200.0,
                    "trigger_for": 2,
                    "recovery_threshold": 0.0,
                    "recovery_for": 2,
                    "severity": "warning",
                }
            ],
        },
    )
    assert r.status_code == 200, r.text[:300]
    pid = r.json()["id"]
    project_id = __import__("uuid").UUID(pid)
    try:
        from oncall.application.project_service import ProjectService

        project = await ProjectService(db).get(project_id, admin_id)
        cfg = await ProjectService(db).runtime_config(project.id)
        src_id = cfg.log_sources[0].id
        # a real run creates the durable cursor
        await MonitoringEngine(db).run_project(project.id)
        cursor = await db.get(LogCursor, src_id)
        assert cursor is not None and cursor.offset > 0, "real run must persist a log cursor"
        offset_before = cursor.offset

        t = await api_client.post(f"/api/projects/{pid}/test")
        assert t.status_code == 200, t.text[:500]
        db.expire_all()
        cursor_after = await db.get(LogCursor, src_id)
        assert cursor_after.offset == offset_before, "dry-run advanced the persisted log cursor"
        # and dry-run wrote no MonitoringRun for this project
        assert await _count(db, MonitoringRun, project_id) == 1  # only the real run
    finally:
        await db.execute(__import__("sqlalchemy").delete(Project).where(Project.id == project_id))
        await db.commit()
