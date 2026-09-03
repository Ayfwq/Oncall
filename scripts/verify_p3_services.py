"""P3 verification: service layer binding + cascade SET NULL.

Checks:
  1. create a project with a Service and a process target bound to it.
  2. the binding (service_id) round-trips through runtime_config persistence.
  3. removing the service from the project (cascade delete) leaves the target
     with service_id = NULL (FK ondelete='SET NULL'), never an orphaned FK.
"""
from __future__ import annotations

import asyncio
from uuid import uuid4

PASS = []
FAIL = []


def check(name: str, ok: bool, detail: str = '') -> None:
    (PASS if ok else FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name} -- {detail}")


async def main() -> None:
    from oncall.application.auth_service import AuthService
    from oncall.application.dtos import (
        ProcessTargetDTO,
        ProjectCreateDTO,
        ServiceCreateDTO,
    )
    from oncall.application.project_service import ProjectService
    from oncall.bootstrap.config import get_settings
    from oncall.infrastructure.db.session import SessionFactory

    async with SessionFactory() as db:
        await AuthService(db).ensure_admin()
        admin = await AuthService(db).user_from_token(
            (await AuthService(db).login('admin', get_settings().admin_password))[1]
        )
        svc = ProjectService(db)
        proj = await svc.create(
            admin.id,
            ProjectCreateDTO(
                name='p3-verify-' + uuid4().hex[:6],
                services=[ServiceCreateDTO(name='checkout-svc', description='checkout api', enabled=True)],
            ),
        )
        pid = proj.id
        try:
            cfg = await svc.runtime_config(pid, include_disabled=True)
            check('service created', len(cfg.services) == 1, f'{len(cfg.services)} service(s)')
            svc_id = cfg.services[0].id
            check('service id assigned', svc_id is not None, str(svc_id))

            # bind a process target to the service
            cfg.process_targets.append(
                ProcessTargetDTO(
                    name='checkout-worker',
                    executable='worker',
                    cmdline_filters=[],
                    cwd='/',
                    port=None,
                    enabled=True,
                    service_id=svc_id,
                )
            )
            await svc.update(pid, admin.id, cfg)
            cfg2 = await svc.runtime_config(pid, include_disabled=True)
            pt = [t for t in cfg2.process_targets if t.name == 'checkout-worker']
            check('process target persisted', len(pt) == 1, f'{len(pt)} target(s)')
            if pt:
                check('target bound to service', pt[0].service_id == svc_id, f'service_id={pt[0].service_id}')

            # remove the service -> target.service_id must become NULL (FK SET NULL)
            cfg3 = await svc.runtime_config(pid, include_disabled=True)
            cfg3.services = []
            await svc.update(pid, admin.id, cfg3)
            cfg4 = await svc.runtime_config(pid, include_disabled=True)
            check('service removed', len(cfg4.services) == 0, f'{len(cfg4.services)} left')
            pt4 = [t for t in cfg4.process_targets if t.name == 'checkout-worker']
            check('target survived, service_id NULL', bool(pt4) and pt4[0].service_id is None,
                  f'kept={bool(pt4)} service_id={pt4[0].service_id if pt4 else "n/a"}')

            # re-bind to a fresh service to confirm binding works bidirectionally
            cfg5 = await svc.runtime_config(pid, include_disabled=True)
            cfg5.services.append(ServiceCreateDTO(name='pay-svc', enabled=True))
            await svc.update(pid, admin.id, cfg5)
            cfg6 = await svc.runtime_config(pid, include_disabled=True)
            new_svc = [s for s in cfg6.services if s.name == 'pay-svc']
            if new_svc:
                for t in cfg6.process_targets:
                    if t.name == 'checkout-worker':
                        t.service_id = new_svc[0].id
                await svc.update(pid, admin.id, cfg6)
                cfg7 = await svc.runtime_config(pid, include_disabled=True)
                rebind = [t for t in cfg7.process_targets if t.name == 'checkout-worker']
                check('re-bound to new service', bool(rebind) and rebind[0].service_id == new_svc[0].id,
                      f'new service_id={rebind[0].service_id if rebind else "n/a"}')
        finally:
            await svc.delete(pid, admin.id)

    print('\n=== SUMMARY ===')
    print(f'{len(PASS)}/{len(PASS) + len(FAIL)} checks passed')
    if FAIL:
        print('FAILED:', FAIL)
        raise SystemExit(1)
    print('ALL P3 CHECKS PASSED')


if __name__ == '__main__':
    asyncio.run(main())
