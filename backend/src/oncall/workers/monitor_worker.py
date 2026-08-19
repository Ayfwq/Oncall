from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from sqlalchemy import delete, select, text

from oncall.bootstrap.config import get_settings
from oncall.infrastructure.db.models import MetricSample, MonitoringRun, Project
from oncall.infrastructure.db.session import SessionFactory, engine
from oncall.monitoring.engine import MonitoringEngine

MONITOR_ADVISORY_LOCK_KEY = 0x4F4E43414C4C  # "ONCALL" within signed bigint range


async def _project_is_due(db, project: Project, now: datetime) -> bool:
    last = await db.scalar(select(MonitoringRun).where(MonitoringRun.project_id==project.id).order_by(MonitoringRun.started_at.desc()).limit(1))
    if not last:return True
    anchor=last.finished_at or last.started_at
    return (now-anchor).total_seconds()>=max(10,project.poll_interval)


async def _cleanup_metric_retention(db,days:int)->None:
    cutoff=datetime.now().astimezone()-timedelta(days=days)
    await db.execute(delete(MetricSample).where(MetricSample.ts<cutoff));await db.commit()


async def _acquire_leader_lock():
    """Keep a PostgreSQL session-level advisory lock for the whole worker lifetime.

    V1 is single-host. This prevents accidentally starting two monitor workers and
    duplicating collection/Incident transitions. Non-PostgreSQL test databases skip it.
    """
    if not get_settings().database_url.startswith('postgresql'):
        return None
    conn=await engine.connect()
    acquired=await conn.scalar(text('SELECT pg_try_advisory_lock(:key)'),{'key':MONITOR_ADVISORY_LOCK_KEY})
    if not acquired:
        await conn.close();return False
    return conn


async def loop()->None:
    settings=get_settings();lock_conn=await _acquire_leader_lock()
    if lock_conn is False:
        print('monitor-worker: another leader already holds the advisory lock; exiting')
        return
    cleanup_counter=0
    try:
        while True:
            now=datetime.now().astimezone()
            async with SessionFactory() as db:
                projects=list((await db.scalars(select(Project).where(Project.enabled.is_(True)))).all())
                for project in projects:
                    try:
                        if await _project_is_due(db,project,now):await MonitoringEngine(db).run_project(project.id)
                    except Exception as exc:print('monitor error',project.id,exc)
                cleanup_counter+=1
                if cleanup_counter>=720:
                    try:await _cleanup_metric_retention(db,settings.metric_retention_days)
                    finally:cleanup_counter=0
            await asyncio.sleep(5)
    finally:
        if lock_conn not in (None,False):
            try:await lock_conn.execute(text('SELECT pg_advisory_unlock(:key)'),{'key':MONITOR_ADVISORY_LOCK_KEY})
            finally:await lock_conn.close()


def run()->None:asyncio.run(loop())
