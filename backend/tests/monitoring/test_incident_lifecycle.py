"""Incident lifecycle and fingerprint, verified against PostgreSQL.

Covers: fingerprint stability + != snapshot hash, single Incident per sustained
firing (last_seen refresh, no duplicate), manual resolve followed by a still
abnormal metric re-triggering a brand-new Incident, and the engine-level
sustained-firing path.
"""
from __future__ import annotations

import hashlib
import time

import pytest
from helpers import SYNTH, make_project_with_rule, project_incidents, rule_id, synth_snapshot
from oncall.application.incident_service import IncidentService, incident_fingerprint
from oncall.infrastructure.db.models import Incident
from oncall.infrastructure.db.session import SessionFactory
from oncall.monitoring.engine import MonitoringEngine


async def test_fingerprint_stable_deterministic():
    import uuid as _uuid

    p, r = _uuid.uuid4(), _uuid.uuid4()
    a = incident_fingerprint(p, r, "host", "cpu")
    b = incident_fingerprint(p, r, "host", "cpu")
    c = incident_fingerprint(p, r, "host", "mem")
    assert a == b and len(a) == 64
    assert a != c
    # fingerprint is NOT a snapshot hash: it keys on identity, not on content
    snapshot_blob = {"project_id": str(p), "rule_id": str(r), "signals": {"host.cpu.percent": 95.0}}
    snapshot_hash = hashlib.sha256(
        (snapshot_blob["project_id"] + snapshot_blob["rule_id"] + "95.0").encode()
    ).hexdigest()
    assert a != snapshot_hash
    assert a == hashlib.sha256(f"{p}|{r}|host|cpu".encode()).hexdigest()


@pytest.mark.integration
async def test_on_firing_creates_single_incident_and_refreshes_last_seen(db, test_user):
    project = await make_project_with_rule(db, test_user)
    rid = await rule_id(db, project.id)
    svc = IncidentService(db)

    first = await svc.on_firing(project.id, rid, "default", SYNTH, "warning", 95.0)
    assert first.status == "open" and first.fingerprint == incident_fingerprint(project.id, rid, "default", SYNTH)
    assert first.anomaly_type == SYNTH
    await db.refresh(first)
    first_seen = first.last_seen
    time.sleep(1.05)

    second = await svc.on_firing(project.id, rid, "default", SYNTH, "warning", 96.0)
    assert second.id == first.id, "sustained firing must reuse the same Incident"
    await db.refresh(second)
    assert second.last_seen > first_seen, "last_seen must be refreshed on sustained firing"

    rows = await project_incidents(db, project.id, rid)
    assert len(rows) == 1, "sustained firing must not create duplicate Incidents"


@pytest.mark.integration
async def test_manual_resolve_then_retrigger_creates_new_incident(db, test_user):
    project = await make_project_with_rule(db, test_user)
    rid = await rule_id(db, project.id)
    svc = IncidentService(db)

    inc_a = await svc.on_firing(project.id, rid, "default", SYNTH, "warning", 95.0)
    await svc.resolve(inc_a.id, "manual_resolve")
    await db.refresh(inc_a)
    assert inc_a.status == "resolved" and inc_a.resolved_at is not None

    # the metric is still abnormal -> a fresh Incident must be created
    inc_b = await svc.on_firing(project.id, rid, "default", SYNTH, "warning", 95.0)
    assert inc_b.id != inc_a.id

    rows = await project_incidents(db, project.id, rid)
    assert len(rows) == 2
    assert rows[0].status == "resolved" and rows[1].status == "open"


@pytest.mark.integration
async def test_resolve_manual_resets_rule_state(db, test_user):
    from oncall.infrastructure.db.models import MonitoringRuleState

    project = await make_project_with_rule(db, test_user)
    rid = await rule_id(db, project.id)
    engine = MonitoringEngine(db)
    svc = IncidentService(db)
    await engine.evaluate_rules(project.id, synth_snapshot(project.id, 5.0))  # PENDING
    await engine.evaluate_rules(project.id, synth_snapshot(project.id, 6.0))  # FIRING
    inc = (await project_incidents(db, project.id, rid))[0]
    st = await db.get(MonitoringRuleState, rid)
    assert st is not None and st.state == "firing"
    await svc.resolve(inc.id, "manual_resolve")
    st = await db.get(MonitoringRuleState, rid)
    assert st.state == "normal" and st.abnormal_hits == 0 and st.recovery_hits == 0


@pytest.mark.integration
async def test_engine_sustained_firing_no_duplicate_and_retrigger_after_manual_resolve(db, test_user):
    project = await make_project_with_rule(db, test_user)
    rid = await rule_id(db, project.id)
    engine = MonitoringEngine(db)
    svc = IncidentService(db)

    await engine.evaluate_rules(project.id, synth_snapshot(project.id, 5.0))   # PENDING
    await engine.evaluate_rules(project.id, synth_snapshot(project.id, 6.0))   # FIRING -> incident A
    await engine.evaluate_rules(project.id, synth_snapshot(project.id, 7.0))   # sustained FIRING
    await engine.evaluate_rules(project.id, synth_snapshot(project.id, 8.0))   # sustained FIRING
    rows = await project_incidents(db, project.id, rid)
    assert len(rows) == 1, "engine must not duplicate incidents while FIRING"
    inc_a = rows[0]
    assert inc_a.status == "open"

    await svc.resolve(inc_a.id, "manual_resolve")  # operator override; metric still abnormal
    await engine.evaluate_rules(project.id, synth_snapshot(project.id, 9.0))   # state reset -> PENDING
    await engine.evaluate_rules(project.id, synth_snapshot(project.id, 10.0))  # FIRING -> incident B
    rows = await project_incidents(db, project.id, rid)
    assert len(rows) == 2
    assert rows[0].id == inc_a.id and rows[0].status == "resolved"
    assert rows[1].status == "open" and rows[1].id != inc_a.id


@pytest.mark.integration
async def test_resolve_is_idempotent(db, test_user):
    project = await make_project_with_rule(db, test_user)
    rid = await rule_id(db, project.id)
    svc = IncidentService(db)
    inc = await svc.on_firing(project.id, rid, "default", SYNTH, "warning", 95.0)
    await svc.resolve(inc.id, "manual_resolve")
    again = await svc.resolve(inc.id, "manual_resolve")
    assert again is not None and again.status == "resolved"
    # no duplicate resolved notification: dedupe_key unique constraint
    from oncall.infrastructure.db.models import Notification
    from sqlalchemy import func, select

    n = await db.scalar(
        select(func.count()).select_from(Notification).where(Notification.incident_id == inc.id)
    )
    assert n == 1


@pytest.mark.integration
async def test_restart_preserves_open_incident(db, test_user):
    """An open Incident survives a service restart (fresh session read)."""
    project = await make_project_with_rule(db, test_user)
    rid = await rule_id(db, project.id)
    inc = await IncidentService(db).on_firing(project.id, rid, "default", SYNTH, "warning", 95.0)
    async with SessionFactory() as fresh:
        again = await fresh.get(Incident, inc.id)
        assert again is not None and again.status == "open"
        assert again.fingerprint == incident_fingerprint(project.id, rid, "default", SYNTH)


@pytest.mark.integration
async def test_severity_upgrade_enqueues_reinvestigation(db, test_user):
    """Severity escalation (warning -> critical) re-runs the Agent, not just updates the field."""
    from oncall.infrastructure.db.models import BackgroundJob
    from sqlalchemy import select

    project = await make_project_with_rule(db, test_user)
    rid = await rule_id(db, project.id)
    svc = IncidentService(db)

    inc = await svc.on_firing(project.id, rid, "default", SYNTH, "warning", 95.0)
    assert inc.severity == "warning"

    async def jobs():
        return list(
            (
                await db.scalars(
                    select(BackgroundJob).where(
                        BackgroundJob.type == "incident_investigate",
                        BackgroundJob.payload["incident_id"].astext == str(inc.id),
                    )
                )
            ).all()
        )

    assert len(await jobs()) == 1, "initial firing must enqueue one investigation"

    # same incident, escalated severity
    inc2 = await svc.on_firing(project.id, rid, "default", SYNTH, "critical", 99.0)
    assert inc2.id == inc.id
    await db.refresh(inc2)
    assert inc2.severity == "critical"

    after = await jobs()
    assert len(after) == 2, "severity upgrade must enqueue a re-investigation"
    assert any("upgrade:critical" in (j.idempotency_key or "") for j in after)


@pytest.mark.integration
async def test_same_severity_firing_does_not_reinvestigate(db, test_user):
    """Sustained firing at the same severity refreshes last_seen without new Agent runs."""
    from oncall.infrastructure.db.models import BackgroundJob
    from sqlalchemy import select

    project = await make_project_with_rule(db, test_user)
    rid = await rule_id(db, project.id)
    svc = IncidentService(db)

    inc = await svc.on_firing(project.id, rid, "default", SYNTH, "critical", 99.0)
    await svc.touch_firing(inc, "critical", 99.5)  # same severity -> no new job

    jobs = list(
        (
            await db.scalars(
                select(BackgroundJob).where(
                    BackgroundJob.type == "incident_investigate",
                    BackgroundJob.payload["incident_id"].astext == str(inc.id),
                )
            )
        ).all()
    )
    assert len(jobs) == 1, "same-severity sustained firing must not re-investigate"
