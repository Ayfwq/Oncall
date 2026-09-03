"""P2 verification: metrics auto-discovery + rule templates + apply merge.

Three checks:
  1. parse/detect/build on a synthetic instrumentator exposition (no network).
  2. live HTTP scrape of a real FastAPI + prometheus-fastapi-instrumentator app.
  3. merge-and-apply into a project via ProjectService, then re-read persistence.
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


# A representative prometheus-fastapi-instrumentator v8 exposition.
INSTR_TEXT = """
# HELP python_info Python information
python_info{implementation="CPython",version="3.13.13"} 1.0
# HELP http_requests Total HTTP requests
# TYPE http_requests counter
http_requests{handler="/health",method="GET",status="2xx"} 5400.0
http_requests{handler="/health",method="GET",status="5xx"} 27.0
http_requests{handler="/slow",method="GET",status="2xx"} 310.0
http_requests{handler="/boom",method="GET",status="5xx"} 999.0
# HELP http_request_duration_seconds HTTP request duration in seconds
# TYPE http_request_duration_seconds histogram
http_request_duration_seconds_bucket{handler="/health",le="0.05"} 5200.0
http_request_duration_seconds_bucket{handler="/health",le="0.1"} 5380.0
http_request_duration_seconds_bucket{handler="/health",le="+Inf"} 5427.0
http_request_duration_seconds_count{handler="/health"} 5427.0
http_request_duration_seconds_bucket{handler="/slow",le="0.5"} 300.0
http_request_duration_seconds_bucket{handler="/slow",le="+Inf"} 310.0
http_request_duration_seconds_count{handler="/slow"} 310.0
"""


async def main() -> None:
    from oncall.application.dtos import MetricsSourceDTO, MonitoringRuleDTO, ProjectCreateDTO
    from oncall.application.project_service import ProjectService
    from oncall.bootstrap.config import get_settings
    from oncall.infrastructure.db.session import SessionFactory
    from oncall.monitoring.rule_templates import (
        build_suggested_rules,
        detect_framework,
        discover,
        parse_metrics_text,
    )

    # ---- 1. synthetic parse / detect / build ----
    parsed = parse_metrics_text(INSTR_TEXT)
    check('parse metric names', 'http_requests' in parsed['metric_names'], str(len(parsed['metric_names'])) + ' families')
    check('parse routes', set(parsed['routes']) >= {'/health', '/slow'}, ','.join(parsed['routes']))
    profile = detect_framework(parsed['metric_names'])
    check('detect fastapi-instrumentator', profile.key == 'prometheus-fastapi-instrumentator', profile.label)
    rules = build_suggested_rules(profile, parsed['routes'])
    global_err = [r for r in rules if r.resource_key == 'default' and r.metric_key == 'app.http.error_rate']
    per_route = [r for r in rules if r.resource_key.startswith('route:')]
    check('suggested global rules', len(global_err) == 1, f'{len(rules)} rules total')
    check('suggested per-route rules', len(per_route) == 2 * len(parsed['routes']), f'{len(per_route)} per-route')

    # ---- 2. live scrape of a real instrumentator server ----
    live = await _live_scrape_discover(discover)
    if live is None:
        check('live scrape', False, 'could not start local instrumentator server')
    else:
        check('live scrape ok', bool(live.get('scrape_ok')), live.get('error') or 'ok')
        check('live detects framework', live.get('framework') == 'prometheus-fastapi-instrumentator', str(live.get('framework')))
        check('live suggested source', bool(live.get('suggested_source')), str(live.get('framework_label')))
        check('live suggested rules', len(live.get('suggested_rules', [])) > 0, f"{len(live.get('suggested_rules', []))} rules")

    # ---- 3. merge + apply + persist ----
    async with SessionFactory() as db:
        from oncall.application.auth_service import AuthService
        await AuthService(db).ensure_admin()
        admin = await AuthService(db).user_from_token((await AuthService(db).login('admin', get_settings().admin_password))[1])
        svc = ProjectService(db)
        proj = await svc.create(admin.id, ProjectCreateDTO(name='p2-verify-' + uuid4().hex[:6]))
        pid = proj.id
        try:
            src = MetricsSourceDTO(name='app', url='http://127.0.0.1:1/metrics', auth_type='none', route_label='handler', enabled=True)
            apply_rules = build_suggested_rules(profile, parsed['routes'])
            cfg = await svc.runtime_config(pid, include_disabled=True)
            n_src0 = len(cfg.metrics_sources)
            n_rules0 = len(cfg.rules)
            if (src.name, src.url) not in {(s.name, s.url) for s in cfg.metrics_sources}:
                cfg.metrics_sources.append(src)
            existing = {(r.metric_key, r.resource_key) for r in cfg.rules}
            for r in apply_rules:
                if (r.metric_key, r.resource_key) not in existing:
                    cfg.rules.append(r); existing.add((r.metric_key, r.resource_key))
            await svc.update(pid, admin.id, cfg)
            # re-read: source persisted, rules persisted, and a second apply is a no-op (dedupe)
            cfg2 = await svc.runtime_config(pid, include_disabled=True)
            check('apply persisted source', len(cfg2.metrics_sources) == n_src0 + 1, f'{n_src0}->{len(cfg2.metrics_sources)}')
            check('apply persisted rules', len(cfg2.rules) == n_rules0 + len(apply_rules), f'{n_rules0}->{len(cfg2.rules)}')
            # dedupe on re-apply
            cfg3 = await svc.runtime_config(pid, include_disabled=True)
            samesrc = sum(1 for s in cfg3.metrics_sources if s.url == src.url)
            check('source dedupe', samesrc == 1, f'count={samesrc}')
        finally:
            await svc.delete(pid, admin.id)

    print('\n=== SUMMARY ===')
    print(f'{len(PASS)}/{len(PASS) + len(FAIL)} checks passed')
    if FAIL:
        print('FAILED:', FAIL)
        raise SystemExit(1)
    print('ALL P2 CHECKS PASSED')


async def _live_scrape_discover(discover_fn):
    """Serve the synthetic exposition over HTTP and scrape it via discover()."""
    try:
        from fastapi import FastAPI, Response
        from fastapi.responses import PlainTextResponse
        import uvicorn
    except Exception as e:  # noqa: BLE001
        print(f'  [SKIP] live scrape: import failed {e}')
        return None

    app = FastAPI()

    @app.get('/metrics')
    def metrics():
        return PlainTextResponse(INSTR_TEXT, media_type='text/plain; version=0.0.4')

    port = 18099
    cfg = uvicorn.Config(app, host='127.0.0.1', port=port, log_level='warning')
    server = uvicorn.Server(cfg)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    base = f'http://127.0.0.1:{port}'
    for _ in range(50):
        try:
            async with __import__('httpx').AsyncClient(timeout=1) as c:
                await c.get(base + '/metrics')
            break
        except Exception:
            time.sleep(0.1)
    try:
        result = await discover_fn(base + '/metrics')
    finally:
        server.should_exit = True
    return result


if __name__ == '__main__':
    asyncio.run(main())
