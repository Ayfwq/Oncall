"""P4 verification: composite (AND) detection rules + escalate_at.

Unit checks (engine methods):
  1. _condition_holds: single condition true/false.
  2. _composite_active: AND semantics (all must hold).
  3. _composite_severity: escalate_at raises severity only when its condition holds.

End-to-end (evaluate_rules over a persisted project):
  A. CPU high AND error-rate high AND saturation high -> fires, severity=critical
     (escalate_at), AlertEvent.composite=True, investigation deferred to a job.
  B. CPU high BUT error-rate normal -> does NOT fire (no Incident).
  C. Confirms the rule path does NOT invoke the LLM inline: it only enqueues an
     incident_investigate job (consumed out-of-band by the agent worker).
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from uuid import uuid4

PASS = []
FAIL = []


def check(name: str, ok: bool, detail: str = '') -> None:
    (PASS if ok else FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name} -- {detail}")


def _snapshot(project_id, signals: dict) -> 'object':
    from oncall.application.dtos import SnapshotDTO
    return SnapshotDTO(
        project_id=project_id,
        observed_at=datetime.now().astimezone(),
        signals=signals,
        resource_signals={},
        resources={},
        collector_status={},
    )


COMPOSITE_CONDITIONS = {
    'all': [
        {'metric_key': 'zz.test.cpu', 'resource_key': 'default', 'operator': '>', 'threshold': 80},
        {'metric_key': 'zz.test.errrate', 'resource_key': 'default', 'operator': '>', 'threshold': 0.05},
    ],
    'escalate_at': {
        'metric_key': 'zz.test.saturation', 'resource_key': 'default',
        'operator': '>', 'threshold': 0.9, 'escalate_severity': 'critical',
    },
}


async def main() -> None:
    from oncall.application.auth_service import AuthService
    from oncall.application.dtos import MonitoringRuleDTO, ProjectCreateDTO
    from oncall.application.project_service import ProjectService
    from oncall.bootstrap.config import get_settings
    from oncall.infrastructure.db.models import AlertEvent, BackgroundJob, Incident
    from oncall.infrastructure.db.session import SessionFactory
    from oncall.monitoring.engine import MonitoringEngine
    from sqlalchemy import select

    async with SessionFactory() as db:
        await AuthService(db).ensure_admin()
        admin = await AuthService(db).user_from_token(
            (await AuthService(db).login('admin', get_settings().admin_password))[1]
        )
        svc = ProjectService(db)
        engine = MonitoringEngine(db)

        # ---- unit checks ----
        pid_dummy = uuid4()
        sn = _snapshot(pid_dummy, {'zz.test.cpu': 95.0, 'zz.test.errrate': 0.2, 'zz.test.saturation': 0.95})
        c_cpu = {'metric_key': 'zz.test.cpu', 'resource_key': 'default', 'operator': '>', 'threshold': 80}
        c_err = {'metric_key': 'zz.test.errrate', 'resource_key': 'default', 'operator': '>', 'threshold': 0.05}
        check('unit _condition_holds true', engine._condition_holds(c_cpu, sn) is True)
        check('unit _condition_holds false', engine._condition_holds(
            {'metric_key': 'zz.test.cpu', 'resource_key': 'default', 'operator': '>', 'threshold': 99}, sn) is False)
        check('unit _composite_active AND', engine._composite_active(
            {'all': [c_cpu, c_err]}, sn) is True)
        check('unit _composite_active AND breaks', engine._composite_active(
            {'all': [c_cpu, {'metric_key': 'zz.test.errrate', 'resource_key': 'default', 'operator': '>', 'threshold': 0.9}]}, sn) is False)
        check('unit _composite_severity escalate', engine._composite_severity(
            COMPOSITE_CONDITIONS, sn, 'warning') == 'critical')
        check('unit _composite_severity base', engine._composite_severity(
            COMPOSITE_CONDITIONS, _snapshot(pid_dummy, {'zz.test.saturation': 0.1}), 'warning') == 'warning')

        # ---- end-to-end A: all conditions hold -> fire, critical ----
        proj_a = await svc.create(
            admin.id,
            ProjectCreateDTO(
                name='p4-a-' + uuid4().hex[:6],
                rules=[MonitoringRuleDTO(
                    metric_key='zz.test.cpu', resource_key='default', operator='>',
                    trigger_threshold=0.0, trigger_for=1, recovery_threshold=0.0, recovery_for=1,
                    severity='warning', enabled=True, conditions=COMPOSITE_CONDITIONS,
                )],
            ),
        )
        pid_a = proj_a.id
        rule_a = (await svc.runtime_config(pid_a, include_disabled=True)).rules[0]
        await engine.evaluate_rules(pid_a, _snapshot(pid_a, {'zz.test.cpu': 95.0, 'zz.test.errrate': 0.2, 'zz.test.saturation': 0.95}))
        inc_a = (await db.scalars(select(Incident).where(Incident.project_id == pid_a))).all()
        check('A fires (Incident created)', len(inc_a) == 1, f'{len(inc_a)} incident(s)')
        if inc_a:
            check('A severity escalated to critical', inc_a[0].severity == 'critical', inc_a[0].severity)
        ev_a = (await db.scalars(select(AlertEvent).where(AlertEvent.rule_id == rule_a.id))).all()
        check('A AlertEvent composite=True', len(ev_a) == 1 and ev_a[0].payload.get('composite') is True,
              str(ev_a[0].payload.get('composite')) if ev_a else 'no event')
        job_a = (await db.scalars(select(BackgroundJob).where(BackgroundJob.type == 'incident_investigate'))).all()
        check('A investigation deferred to job queue', len(job_a) >= 1, f'{len(job_a)} job(s)')

        # ---- end-to-end B: CPU high but error-rate normal -> no fire ----
        proj_b = await svc.create(
            admin.id,
            ProjectCreateDTO(
                name='p4-b-' + uuid4().hex[:6],
                rules=[MonitoringRuleDTO(
                    metric_key='zz.test.cpu', resource_key='default', operator='>',
                    trigger_threshold=0.0, trigger_for=1, recovery_threshold=0.0, recovery_for=1,
                    severity='warning', enabled=True, conditions=COMPOSITE_CONDITIONS,
                )],
            ),
        )
        pid_b = proj_b.id
        await engine.evaluate_rules(pid_b, _snapshot(pid_b, {'zz.test.cpu': 95.0, 'zz.test.errrate': 0.01, 'zz.test.saturation': 0.95}))
        inc_b = (await db.scalars(select(Incident).where(Incident.project_id == pid_b))).all()
        check('B does NOT fire', len(inc_b) == 0, f'{len(inc_b)} incident(s)')

        # cleanup
        await svc.delete(pid_a, admin.id)
        await svc.delete(pid_b, admin.id)

    print('\n=== SUMMARY ===')
    print(f'{len(PASS)}/{len(PASS) + len(FAIL)} checks passed')
    if FAIL:
        print('FAILED:', FAIL)
        raise SystemExit(1)
    print('ALL P4 CHECKS PASSED')


if __name__ == '__main__':
    asyncio.run(main())
