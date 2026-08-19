from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from oncall.infrastructure.db.models import BackgroundJob


class JobQueue:
    def __init__(self, session: AsyncSession): self.session=session

    async def enqueue(self,type_:str,payload:dict,idempotency_key:str|None=None,priority:int=100)->BackgroundJob:
        if idempotency_key:
            existing=await self.session.scalar(select(BackgroundJob).where(BackgroundJob.idempotency_key==idempotency_key))
            if existing:return existing
        job=BackgroundJob(type=type_,payload=payload,idempotency_key=idempotency_key,priority=priority)
        self.session.add(job);await self.session.commit();await self.session.refresh(job);return job

    async def claim(self,types:list[str],lease_seconds:int=120)->BackgroundJob|None:
        now=datetime.now().astimezone()
        stmt=(select(BackgroundJob).where(
            BackgroundJob.type.in_(types),
            BackgroundJob.status.in_(['pending','running']),
            BackgroundJob.available_at<=now,
            or_(BackgroundJob.lease_until.is_(None),BackgroundJob.lease_until<now),
        ).order_by(BackgroundJob.priority.asc(),BackgroundJob.created_at.asc()).with_for_update(skip_locked=True).limit(1))
        job=await self.session.scalar(stmt)
        if not job:return None
        job.status='running';job.lease_until=now+timedelta(seconds=lease_seconds);job.attempts+=1
        await self.session.commit();await self.session.refresh(job);return job

    async def complete(self,job_id:UUID)->None:
        # Core UPDATE is idempotent: if the job was deleted concurrently (e.g. a test
        # fixture or a future reaper removed it mid-run), it affects 0 rows silently.
        await self.session.execute(update(BackgroundJob).where(BackgroundJob.id==job_id).values(status='done',lease_until=None))
        try:await self.session.commit()
        except Exception:await self.session.rollback()

    async def fail(self,job_id:UUID,error:str,retry_delay_seconds:int=10)->None:
        # A durable handler may leave the shared session in a rolled-back state after
        # a mid-flight failure. Reset, re-read by id, and mutate via Core UPDATE so a
        # concurrent delete of the job row can never take down the worker loop.
        await self.session.rollback()
        row=(await self.session.execute(select(BackgroundJob.attempts,BackgroundJob.max_attempts).where(BackgroundJob.id==job_id))).first()
        if row is None:return
        attempts,max_attempts=row[0],row[1]
        values={'lease_until':None,'last_error':error[:8000]}
        if attempts>=max_attempts:values['status']='dead'
        else:values['status']='pending';values['available_at']=datetime.now().astimezone()+timedelta(seconds=retry_delay_seconds*max(1,attempts))
        await self.session.execute(update(BackgroundJob).where(BackgroundJob.id==job_id).values(**values))
        try:await self.session.commit()
        except Exception:await self.session.rollback()
