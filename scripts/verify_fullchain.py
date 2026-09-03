"""Full-chain self-test: live /metrics -> scrape -> detect -> alert -> notify -> defer diagnosis.

A real FastAPI app serves a Prometheus exposition. The first scrape establishes the
delta cursor (baseline). The second scrape returns a higher-error exposition, so the
project-level app.http.error_rate rises above threshold and a rule fires.

Asserts the whole pipeline end-to-end:
  - scrape produced app.* golden signals (live integration, not synthetic).
  - a firing rule created an Incident (severity critical).
  - a Notification was queued (the two-stage alert outbox).
  - an incident_investigate BackgroundJob was enqueued (diagnosis deferred to the
    agent worker -- the rule path itself does NOT call the LLM inline).
"""
from __future__ import annotations

import asyncio
import threading
import time
from uuid import uuid4

PASS = []
FAIL = []


def check(name: str, ok: bool, detail: str = '') -> None:
    (PASS if ok else FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name} -- {detail}")


TEXT_A = """# TYPE http_requests counter
http_requests{handler="/api",method="GET",status="2xx"} 100.0
http_requests{handler="/api",method="GET",status="5xx"} 0.0
# TYPE http_request_duration_seconds histogram
http_request_duration_seconds_bucket{handler="/api",le="0.05"} 100.0
http_request_duration_seconds_bucket{handler="/api",le="0.1"} 100.0
http_request_duration_seconds_bucket{handler="/api",le="+Inf"} 100.0
http_request_duration_seconds_count{handler="/api"} 100.0
http_request_duration_seconds_sum{handler="/api"} 5.0
"""

TEXT_B = """# TYPE http_requests counter
http_requests{handler="/api",method="GET",status="2xx"} 500.0
http_requests{handler="/api",method="GET",status="5xx"} 50.0
# TYPE http_request_duration_seconds histogram
http_request_duration_seconds_bucket{handler="/api",le="0.05"} 400.0
http_request_duration_seconds_bucket{handler="/api",le="0.1"} 550.0
http_request_duration_seconds_bucket{handler="/api",le="+Inf"} 550.0
http_request_duration_seconds_count{handler="/api"} 550.0
http_request_duration_seconds_sum{handler="/api"} 50.0
"""


async def main() -> None:
    from fastapi import FastAPI
    from fastapi.responses import PlainTextResponse
    import uvicorn

    from oncall.application.auth_service import AuthService
    from oncall.application.dtos import (
        MetricsSourceDTO, MonitoringRuleDTO, ProjectCreateDTO,
    )
    from oncall.application.project_service import ProjectService
    from oncall.bootstrap.config import get_settings
    from oncall.infrastructure.db.models import BackgroundJob, Incident, Notification
    from oncall.infrastructure.db.session import SessionFactory
    from oncall.monitoring.engine import MonitoringEngine
    from sqlalchemy import select

    # ---- live /metrics app ----
    # /health is used only for the readiness poll so it does NOT consume the
    # exposition flip; only the monitor's real /metrics scrape flips A -> B.
    state = {'first': True}
    app = FastAPI()

    @app.get('/health')
    def health():
        return PlainTextResponse('ok')

    @app.get('/metrics')
    def metrics():
        text = TEXT_A if state['first'] else TEXT_B
        state['first'] = False
        return PlainTextResponse(text, media_type='text/plain; version=0.0.4')

    port = 18199
    cfg = uvicorn.Config(app, host='127.0.0.1', port=port, log_level='warning')
    server = uvicorn.Server(cfg)
    threading.Thread(target=server.run, daemon=True).start()
    base = f'http://127.0.0.1:{port}'
    for _ in range(50):
        try:
            async with __import__('httpx').AsyncClient(timeout=1) as c:
                await c.get(base + '/health')
            break
        except Exception:
            time.sleep(0.1)

    try:
        async with SessionFactory() as db:
            await AuthService(db).ensure_admin()
            admin = await AuthService(db).user_from_token(
                (await AuthService(db).login('admin', get_settings().admin_password))[1]
            )
            svc = ProjectService(db)
            engine = MonitoringEngine(db)
            proj = await svc.create(
                admin.id,
                ProjectCreateDTO(
                    name='fullchain-' + uuid4().hex[:6],
                    metrics_sources=[MetricsSourceDTO(
                        name='app', url=base + '/metrics', auth_type='none',
                        route_label='handler', scrape_timeout_ms=5000, enabled=True,
                    )],
                    rules=[MonitoringRuleDTO(
                        metric_key='app.http.error_rate', resource_key='default', operator='>',
                        trigger_threshold=0.1, trigger_for=1, recovery_threshold=0.05,
                        recovery_for=1, severity='critical', enabled=True,
                    )],
                ),
            )
            pid = proj.id
            try:
                # run 1: baseline cursor (error_rate 0)
                snap1 = await engine.run_project(pid)
                check('scrape produced app.up', snap1.signals.get('app.up') == 1.0, str(snap1.signals.get('app.up')))
                # run 2: higher-error exposition -> delta raises error_rate
                await asyncio.sleep(1.1)
                snap2 = await engine.run_project(pid)
                err = snap2.signals.get('app.http.error_rate')
                check('project error_rate computed from delta', isinstance(err, (int, float)) and err > 0.1,
                      f'error_rate={err}')
                inc = (await db.scalars(select(Incident).where(Incident.project_id == pid))).all()
                check('Incident created (rule fired)', len(inc) == 1, f'{len(inc)} incident(s)')
                if inc:
                    check('Incident severity critical', inc[0].severity == 'critical', inc[0].severity)
                notif = (await db.scalars(select(Notification).where(Notification.incident_id == (inc[0].id if inc else None)))).all() if inc else []
                check('Notification queued (alert outbox)', len(notif) >= 1, f'{len(notif)} notification(s)')
                job = (await db.scalars(select(BackgroundJob).where(BackgroundJob.type == 'incident_investigate'))).all()
                check('diagnosis deferred to job queue (no inline LLM)', len(job) >= 1, f'{len(job)} job(s)')
            finally:
                await svc.delete(pid, admin.id)
    finally:
        server.should_exit = True

    print('\n=== SUMMARY ===')
    print(f'{len(PASS)}/{len(PASS) + len(FAIL)} checks passed')
    if FAIL:
        print('FAILED:', FAIL)
        raise SystemExit(1)
    print('ALL FULL-CHAIN CHECKS PASSED')


if __name__ == '__main__':
    asyncio.run(main())
