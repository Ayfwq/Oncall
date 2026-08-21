from __future__ import annotations

import asyncio
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from oncall.application.dtos import SnapshotDTO
from oncall.application.incident_service import IncidentService, incident_fingerprint
from oncall.application.project_service import ProjectService
from oncall.infrastructure.db.models import (
    AlertEvent,
    Conversation,
    Incident,
    LogCursor,
    MetricSample,
    MonitoringRule,
    MonitoringRuleState,
    MonitoringRun,
)
from oncall.integrations.database import DatabaseIntegration
from oncall.integrations.docker_integration import DockerIntegration
from oncall.integrations.host import HostIntegration
from oncall.integrations.logs import LogIntegration
from oncall.integrations.process import ProcessIntegration
from oncall.integrations.service import ServiceIntegration
from oncall.monitoring.detector import Detector, RuleConfig, RuleRuntimeState


class MonitoringEngine:
    def __init__(self,session:AsyncSession):
        from oncall.bootstrap.config import get_settings
        self.session=session;self.detector=Detector();self._host=HostIntegration();self.settings=get_settings()

    async def collect(self,project_id:UUID, *, persist_state:bool=True)->SnapshotDTO:
        cfg=await ProjectService(self.session).runtime_config(project_id)
        integrations=[self._host,ProcessIntegration(cfg.process_targets),DockerIntegration(cfg.docker_targets),DatabaseIntegration(cfg.database_profiles),ServiceIntegration(cfg.service_endpoints)]
        async def one(i):
            try:return await asyncio.wait_for(i.collect(),timeout=8)
            except Exception as e:
                from oncall.integrations.base import CollectResult
                return CollectResult(name=i.name,ok=False,error=str(e))
        results=list(await asyncio.gather(*(one(i) for i in integrations)))
        try:
            results.append(await asyncio.wait_for(self._collect_logs_incremental(cfg.log_sources,cfg.poll_interval,persist_state=persist_state),timeout=8))
        except Exception as e:
            from oncall.integrations.base import CollectResult
            results.append(CollectResult(name='logs',ok=False,error=str(e)))
        signals={};resources={};status={}
        for r in results:
            signals.update(r.signals);resources[r.name]=r.resources;status[r.name]={'ok':r.ok,'error':r.error}
        # PostgreSQL exposes a cumulative deadlock counter. Convert it to the V1
        # contract's db.deadlock.delta by comparing against the previous completed
        # snapshot, rather than mislabelling the cumulative total as a delta.
        db_rows=(resources.get('database') or {}).get('databases') or []
        current_deadlocks=float(db_rows[0].get('deadlocks',0)) if db_rows else 0.0
        previous=await self.session.scalar(select(MonitoringRun).where(MonitoringRun.project_id==project_id,MonitoringRun.status=='completed').order_by(MonitoringRun.finished_at.desc()).limit(1))
        previous_snapshot=(previous.snapshot or {}) if previous else {}
        previous_rows=(previous_snapshot.get('resources',{}).get('database',{}).get('databases') or [])
        previous_deadlocks=float(previous_rows[0].get('deadlocks',current_deadlocks)) if previous_rows else current_deadlocks
        if 'db.deadlock.delta' in signals:signals['db.deadlock.delta']=max(0.0,current_deadlocks-previous_deadlocks)

        # Convert host cumulative OS counters into rates even when a worker process
        # constructs a fresh MonitoringEngine between polls.
        cur_c=(resources.get('host') or {}).get('counters') or {}
        prev_c=(previous_snapshot.get('resources',{}).get('host',{}).get('counters') or {})
        dt=float(cur_c.get('ts',0) or 0)-float(prev_c.get('ts',0) or 0)
        if dt>0:
            pairs=(('host.disk.read_bytes_per_sec','disk_read_bytes'),('host.disk.write_bytes_per_sec','disk_write_bytes'),('host.net.rx_bytes_per_sec','net_rx_bytes'),('host.net.tx_bytes_per_sec','net_tx_bytes'))
            for metric,counter in pairs:
                cur=cur_c.get(counter);prev=prev_c.get(counter)
                if cur is not None and prev is not None:signals[metric]=max(0.0,(float(cur)-float(prev))/dt)

        # Consecutive HTTP failures are also durable across worker restarts.
        if 'service.consecutive_failures' in signals:
            current_eps=(resources.get('service') or {}).get('endpoints') or []
            ok=bool(current_eps[0].get('ok')) if current_eps else True
            previous_failures=float(previous_snapshot.get('signals',{}).get('service.consecutive_failures',0) or 0)
            signals['service.consecutive_failures']=0.0 if ok else previous_failures+1.0
        return SnapshotDTO(project_id=project_id,observed_at=datetime.now().astimezone(),signals=signals,resources=resources,collector_status=status)

    async def _collect_logs_incremental(self,sources,window_seconds:int, *, persist_state:bool=True):
        """Read only bytes appended since the last successful poll.

        The cursor is PostgreSQL-backed, therefore service restarts do not cause the
        monitor to recount the whole file. Rotation/truncation resets the offset.
        First observation is bounded to the most recent 2 MiB to avoid a huge import.
        """
        from pathlib import Path
        max_first_read=2*1024*1024
        lines_by_source={}
        status={}
        for src in sources:
            p=Path(src.path)
            try:
                stat=await asyncio.to_thread(p.stat)
                identity=f"{getattr(stat,'st_dev',0)}:{getattr(stat,'st_ino',0)}:{p.resolve()}"
                cursor=await self.session.get(LogCursor,src.id) if src.id else None
                if not cursor and src.id and persist_state:
                    cursor=LogCursor(source_id=src.id,file_identity=identity,offset=max(0,stat.st_size-max_first_read),size=stat.st_size,mtime=stat.st_mtime)
                    self.session.add(cursor);await self.session.flush()
                offset=(cursor.offset if cursor else max(0,stat.st_size-max_first_read))
                if cursor and (cursor.file_identity!=identity or stat.st_size<cursor.offset):offset=0
                def read_new():
                    with p.open('rb') as f:
                        f.seek(offset);data=f.read(max_first_read)
                    return data
                data=await asyncio.to_thread(read_new)
                text=data.decode(src.encoding,errors='replace')
                lines_by_source[src.path]=text.splitlines()
                if cursor and persist_state:
                    cursor.file_identity=identity;cursor.offset=min(stat.st_size,offset+len(data));cursor.size=stat.st_size;cursor.mtime=stat.st_mtime
                status[src.path]={'ok':True,'bytes':len(data),'offset':offset}
            except Exception as exc:
                status[src.path]={'ok':False,'error':str(exc)}
        if persist_state:
            await self.session.commit()
        result=LogIntegration.summarize(lines_by_source,window_seconds)
        result.resources['sources']=status
        result.ok=any(x.get('ok') for x in status.values()) if status else True
        return result

    async def run_project(self,project_id:UUID)->SnapshotDTO:
        run=MonitoringRun(project_id=project_id);self.session.add(run);await self.session.flush()
        snapshot=await self.collect(project_id);run.snapshot=snapshot.model_dump(mode='json');run.collector_status=snapshot.collector_status;run.status='completed';run.finished_at=datetime.now().astimezone()
        for key,value in snapshot.signals.items():
            if isinstance(value,(int,float,bool)):
                self.session.add(MetricSample(project_id=project_id,metric_key=key,resource_key='default',ts=snapshot.observed_at,value=float(value)))
        await self.session.commit();await self.evaluate_rules(project_id,snapshot)
        return snapshot

    async def evaluate_rules(self,project_id:UUID,snapshot:SnapshotDTO)->None:
        rules=list((await self.session.scalars(select(MonitoringRule).where(MonitoringRule.project_id==project_id,MonitoringRule.enabled.is_(True)))).all())
        incident_service=IncidentService(self.session)
        for rule in rules:
            raw=snapshot.signals.get(rule.metric_key)
            if not isinstance(raw,(int,float,bool)):continue
            value=float(raw)
            rs=await self.session.get(MonitoringRuleState,rule.id)
            if not rs:
                rs=MonitoringRuleState(rule_id=rule.id);self.session.add(rs);await self.session.flush()
            runtime=RuleRuntimeState(state=__import__('oncall.domain.enums',fromlist=['RuleState']).RuleState(rs.state),abnormal_hits=rs.abnormal_hits,recovery_hits=rs.recovery_hits,last_value=rs.last_value)
            tr=self.detector.evaluate(RuleConfig(rule.operator,rule.trigger_threshold,rule.trigger_for,rule.recovery_threshold,rule.recovery_for),runtime,value)
            rs.state=runtime.state.value;rs.abnormal_hits=runtime.abnormal_hits;rs.recovery_hits=runtime.recovery_hits;rs.last_value=value
            if tr.changed:
                self.session.add(AlertEvent(rule_id=rule.id,project_id=project_id,resource_key=rule.resource_key,state_from=tr.before.value,state_to=tr.after.value,payload={'metric_key':rule.metric_key,'value':value,'threshold':rule.trigger_threshold}))
            await self.session.commit()
            if tr.became_firing:
                await incident_service.on_firing(project_id,rule.id,rule.resource_key,rule.metric_key,rule.severity,value)
            elif tr.became_recovered:
                fp=incident_fingerprint(project_id,rule.id,rule.resource_key,rule.metric_key)
                inc=await self.session.scalar(select(Incident).where(Incident.project_id==project_id,Incident.fingerprint==fp,Incident.status!='resolved').order_by(Incident.first_seen.desc()).limit(1))
                if inc:await incident_service.resolve(inc.id)
            elif runtime.state.value=='firing':
                # Sustained FIRING updates last_seen every poll but does not call the LLM
                # every time. If an operator manually resolved the Incident while the
                # metric stayed abnormal, recreate it instead of leaving a blind spot.
                from datetime import timedelta

                from oncall.jobs.queue import JobQueue
                fp=incident_fingerprint(project_id,rule.id,rule.resource_key,rule.metric_key)
                inc=await self.session.scalar(select(Incident).where(Incident.project_id==project_id,Incident.fingerprint==fp,Incident.status.in_(['open','investigating','diagnosed'])).order_by(Incident.first_seen.desc()).limit(1))
                if not inc:
                    inc=await incident_service.on_firing(project_id,rule.id,rule.resource_key,rule.metric_key,rule.severity,value)
                else:
                    await incident_service.touch_firing(inc,rule.severity,value)
                now=datetime.now().astimezone()
                if inc and inc.last_investigated_at and now-inc.last_investigated_at>=timedelta(seconds=self.settings.incident_stale_reinvestigate_seconds):
                    conv=await self.session.scalar(select(Conversation).where(Conversation.incident_id==inc.id).order_by(Conversation.created_at.asc()).limit(1))
                    if conv:
                        bucket=int(now.timestamp())//self.settings.incident_stale_reinvestigate_seconds
                        await JobQueue(self.session).enqueue('incident_investigate',{'incident_id':str(inc.id),'conversation_id':str(conv.id)},idempotency_key=f'incident_investigate:{inc.id}:stale:{bucket}',priority=30)
