"""Shared helpers for the PostgreSQL-backed monitoring tests."""
from __future__ import annotations

from datetime import datetime

from oncall.application.dtos import MonitoringRuleDTO, ProjectCreateDTO, SnapshotDTO
from oncall.application.incident_service import incident_fingerprint
from oncall.application.project_service import ProjectService
from oncall.infrastructure.db.models import Incident, MonitoringRule
from sqlalchemy import select

SYNTH = "zz.test.synthetic"


async def make_project_with_rule(db, user, *, trigger_for=2, recovery_for=2,
                                 trigger_threshold=-1.0, recovery_threshold=-2.0,
                                 severity="warning", metric_key=SYNTH):
    """A disabled project (kept away from the live monitor-worker) with one rule."""
    dto = ProjectCreateDTO(
        name=f"proj-{user.username}",
        enabled=False,
        poll_interval=30,
        rules=[
            MonitoringRuleDTO(
                metric_key=metric_key, resource_key="default", operator=">",
                trigger_threshold=trigger_threshold, trigger_for=trigger_for,
                recovery_threshold=recovery_threshold, recovery_for=recovery_for,
                severity=severity,
            )
        ],
    )
    return await ProjectService(db).create(user.id, dto)


async def rule_id(db, project_id):
    return await db.scalar(select(MonitoringRule.id).where(MonitoringRule.project_id == project_id))


def synth_snapshot(pid, value, metric_key=SYNTH):
    return SnapshotDTO(
        project_id=pid,
        observed_at=datetime.now().astimezone(),
        signals={metric_key: value},
        resources={},
        collector_status={},
    )


async def project_incidents(db, project_id, rule_id, metric_key=SYNTH):
    fp = incident_fingerprint(project_id, rule_id, "default", metric_key)
    return list(
        (
            await db.scalars(
                select(Incident)
                .where(Incident.project_id == project_id, Incident.fingerprint == fp)
                .order_by(Incident.first_seen.asc())
            )
        ).all()
    )
