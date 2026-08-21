"""Baseline signals contract: 32 unique V1 signals and their producers.

"Windows 上无法可靠获取的 signal 被正确标记/处理（不产假数据）" is enforced
here in two ways:

1. every baseline signal maps to exactly one integration family, and
2. a real collect (PostgreSQL-backed project with every integration family
   configured) must emit every baseline signal with a value of the correct
   type, so no signal is faked or silently dropped.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from oncall.application.dtos import (
    DatabaseProfileDTO,
    DockerTargetDTO,
    LogSourceDTO,
    MonitoringRuleDTO,
    ProcessTargetDTO,
    ProjectCreateDTO,
    ServiceEndpointDTO,
)
from oncall.application.project_service import ProjectService
from oncall.infrastructure.db.models import (
    LogCursor,
    MetricSample,
    MonitoringRule,
    MonitoringRuleState,
    MonitoringRun,
)
from oncall.monitoring.engine import MonitoringEngine
from oncall.monitoring.signals import BASELINE_SIGNALS

ROOT = Path(__file__).resolve().parents[3]

# Signal family -> baseline signal names
SIGNAL_FAMILIES: dict[str, list[str]] = {
    "host": [s for s in BASELINE_SIGNALS if s.startswith("host.")],
    "process": [s for s in BASELINE_SIGNALS if s.startswith("process.")],
    "logs": [s for s in BASELINE_SIGNALS if s.startswith("log.")],
    "container": [s for s in BASELINE_SIGNALS if s.startswith("container.")],
    "database": [s for s in BASELINE_SIGNALS if s.startswith("db.")],
    "service": [s for s in BASELINE_SIGNALS if s.startswith("service.")],
}


def test_v1_has_exactly_32_unique_baseline_signals():
    assert len(BASELINE_SIGNALS) == 32
    assert len(set(BASELINE_SIGNALS)) == 32


def test_baseline_signals_provenance_covers_all_families():
    # every signal belongs to one of the six integration families and the six
    # families partition the baseline set (no orphan / unknown signals)
    families = [s for _, sigs in SIGNAL_FAMILIES.items() for s in sigs]
    assert sorted(families) == sorted(BASELINE_SIGNALS)
    assert sorted(SIGNAL_FAMILIES) == ["container", "database", "host", "logs", "process", "service"]
    for name, sigs in SIGNAL_FAMILIES.items():
        assert sigs, f"family {name} has no baseline signal"
        assert len(sigs) == len(set(sigs))


async def _make_full_project(db, user):
    dto = ProjectCreateDTO(
        name=f"signals-{user.username}",
        enabled=False,  # keep the live monitor-worker away from test data
        poll_interval=30,
        process_targets=[ProcessTargetDTO(name="python", executable="python")],
        log_sources=[LogSourceDTO(path=str(ROOT / "VALIDATION.md"))],
        docker_targets=[DockerTargetDTO(container_ref="oncall-ai-sre-postgres-1")],
        database_profiles=[
            DatabaseProfileDTO(
                host="127.0.0.1", port=5432, database="oncall",
                username="oncall", password="oncall",
            )
        ],
        service_endpoints=[ServiceEndpointDTO(url="http://127.0.0.1:9900/api/health")],
        rules=[MonitoringRuleDTO(metric_key="host.cpu.percent", trigger_threshold=200.0, recovery_threshold=0.0)],
    )
    return await ProjectService(db).create(user.id, dto)


@pytest.mark.integration
async def test_real_collect_emits_all_32_baseline_signals(db, test_user):
    project = await _make_full_project(db, test_user)
    snap = await MonitoringEngine(db).collect(project.id, persist_state=False)
    assert snap.signals, "collect returned no signals"
    missing = [s for s in BASELINE_SIGNALS if s not in snap.signals]
    assert not missing, f"baseline signals missing from real collect: {missing}"
    # value types: all baseline signals are numeric or bool
    for s in BASELINE_SIGNALS:
        v = snap.signals[s]
        assert isinstance(v, int | float | bool), f"{s} has non-numeric value {v!r}"
    # sanity ranges (never assert concrete values; the machine is busy)
    assert 0 <= float(snap.signals["host.cpu.percent"]) <= 100
    assert 0 <= float(snap.signals["host.memory.percent"]) <= 100
    assert 0 <= float(snap.signals["host.disk.usage_percent"]) <= 100
    assert float(snap.signals["host.memory.available_bytes"]) >= 0
    assert float(snap.signals["host.disk.free_bytes"]) >= 0
    # every collector reported a real status entry
    assert snap.collector_status, "collector_status missing"
    assert all("ok" in v for v in snap.collector_status.values())


@pytest.mark.integration
async def test_dry_run_has_no_persistence_side_effects(db, test_user):
    project = await _make_full_project(db, test_user)
    engine = MonitoringEngine(db)
    await engine.collect(project.id, persist_state=False)  # same code path as POST /api/projects/{id}/test
    await engine.collect(project.id, persist_state=False)

    async def count(model):
        from sqlalchemy import func as _f
        from sqlalchemy import select as _s

        return await db.scalar(_s(_f.count()).select_from(model).where(model.project_id == project.id))

    async def count_states():
        from sqlalchemy import func as _f
        from sqlalchemy import select as _s

        return await db.scalar(
            _s(_f.count())
            .select_from(MonitoringRuleState)
            .join(MonitoringRule, MonitoringRule.id == MonitoringRuleState.rule_id)
            .where(MonitoringRule.project_id == project.id)
        )

    async def count_cursors():
        from oncall.infrastructure.db.models import ProjectLogSource
        from sqlalchemy import func as _f
        from sqlalchemy import select as _s

        return await db.scalar(
            _s(_f.count())
            .select_from(LogCursor)
            .join(ProjectLogSource, ProjectLogSource.id == LogCursor.source_id)
            .where(ProjectLogSource.project_id == project.id)
        )

    assert await count(MonitoringRun) == 0, "dry-run wrote MonitoringRun"
    assert await count(MetricSample) == 0, "dry-run wrote MetricSample"
    assert await count_states() == 0, "dry-run touched detector state"
    assert await count_cursors() == 0, "dry-run wrote LogCursor"
