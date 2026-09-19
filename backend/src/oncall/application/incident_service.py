from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from oncall.infrastructure.db.models import (
    BackgroundJob,
    Conversation,
    Incident,
    IncidentEvidence,
    IncidentSignal,
    MonitoringRule,
    MonitoringRuleState,
    Notification,
    Project,
)
from oncall.jobs.queue import JobQueue

_SEVERITY_RANK={'info':0,'warning':1,'critical':2}
_SEVERITY_LABEL={'info':'提示','warning':'警告','critical':'严重'}
_METRIC_LABEL={
    'host.exporter.up':'服务器采集器断开','host.cpu.percent':'服务器 CPU 使用率过高',
    'host.memory.percent':'服务器内存使用率过高','host.disk.usage_percent':'服务器磁盘空间不足',
    'host.gpu.exporter.up':'GPU 采集器断开','host.gpu.memory_percent':'GPU 显存使用率过高',
    'host.gpu.temperature_celsius':'GPU 温度过高','service.reachable':'健康检查失败',
    'app.up':'应用指标采集失败','app.http.error_rate':'API 错误率过高',
    'app.http.p95_ms':'API P95 延迟异常','app.http.p99_ms':'API P99 延迟异常',
    'app.http.availability':'API 可用性下降',
    'log.collector.up':'日志采集器失联','log.exception_count':'异常日志集中出现',
    'db.up':'数据库连接失败','db.connections.utilization_percent':'数据库连接压力过高',
    'db.long_transactions':'数据库存在长事务','db.lock_waits':'数据库存在锁等待',
    'db.replication_lag_seconds':'数据库复制延迟过高',
}


def _format_value(value:float)->str:
    """Keep alert text readable: 0.1234 -> 0.123, 1234567.0 -> 1234567."""
    if value!=value or value in (float('inf'),float('-inf')):return str(value)
    if float(value).is_integer():return str(int(value))
    return f'{value:.3f}'.rstrip('0').rstrip('.')


def _format_metric_value(metric_key:str,value:float)->str:
    if metric_key in {'host.cpu.percent','host.memory.percent','host.disk.usage_percent','host.gpu.memory_percent','app.http.availability'}:
        return f'{value:.1f}%'
    if metric_key=='app.http.error_rate':
        return f'{value * 100:.1f}%'
    if metric_key in {'app.http.p95_ms','app.http.p99_ms'}:
        return f'{value:.0f} ms'
    if metric_key=='host.gpu.temperature_celsius':
        return f'{value:.1f} °C'
    if metric_key=='db.connections.utilization_percent':
        return f'{value:.1f}%'
    if metric_key=='db.replication_lag_seconds':
        return f'{value:.1f} 秒'
    return _format_value(value)


def _alert_text(project_name:str,anomaly_type:str,resource_key:str,severity:str,value:float,now)->str:
    """First-stage alert body. Intentionally short and factual: what broke, where,
    how bad. Root-cause analysis arrives later as a separate diagnosis message."""
    label=_SEVERITY_LABEL.get(severity,severity)
    lines=[
        f'**{_METRIC_LABEL.get(anomaly_type,anomaly_type)}**',
        '',
        f'**级别**：{label}',
        f'**项目**：{project_name or "-"}',
        f'**资源**：{resource_key or "default"}',
        f'**指标**：`{anomaly_type}`',
        f'**当前值**：{_format_metric_value(anomaly_type,value)}',
        f'**时间**：{now.strftime("%Y-%m-%d %H:%M:%S")}',
    ]
    return '\n'.join(lines)


def incident_fingerprint(project_id:UUID,rule_id:UUID,resource_key:str,anomaly_type:str)->str:
    raw=f'{project_id}|{rule_id}|{resource_key}|{anomaly_type}'.encode()
    return hashlib.sha256(raw).hexdigest()


class IncidentService:
    def __init__(self,session:AsyncSession):self.session=session

    async def _enqueue_reinvestigation(self,inc:Incident,reason:str)->None:
        """Re-run the Agent on a live Incident (severity upgrade / new evidence)."""
        conv=await self.session.scalar(select(Conversation).where(Conversation.incident_id==inc.id).order_by(Conversation.created_at.asc()).limit(1))
        if not conv:return
        await JobQueue(self.session).enqueue('incident_investigate',{'incident_id':str(inc.id),'conversation_id':str(conv.id)},idempotency_key=f'incident_investigate:{inc.id}:{reason}',priority=20)

    async def active_incident_for_signal(self,project_id:UUID,rule_id:UUID,resource_key:str)->Incident|None:
        """Find the open incident that owns one detector signal."""
        return await self.session.scalar(
            select(Incident)
            .join(IncidentSignal,IncidentSignal.incident_id==Incident.id)
            .where(
                Incident.project_id==project_id,
                Incident.status.in_(['open','investigating','diagnosed']),
                IncidentSignal.rule_id==rule_id,
                IncidentSignal.resource_key==resource_key,
                IncidentSignal.state=='firing',
            )
            .order_by(Incident.last_seen.desc())
            .limit(1)
        )

    async def _recent_correlated_incident(self,project_id:UUID,now:datetime)->Incident|None:
        from oncall.bootstrap.config import get_settings

        window=get_settings().incident_correlation_window_seconds
        if window<=0:return None
        return await self.session.scalar(
            select(Incident).where(
                Incident.project_id==project_id,
                Incident.status.in_(['open','investigating','diagnosed']),
                Incident.last_seen>=now-timedelta(seconds=window),
            ).order_by(Incident.last_seen.desc()).limit(1)
        )

    async def on_firing(self,project_id:UUID,rule_id:UUID,resource_key:str,anomaly_type:str,severity:str,value:float)->Incident:
        fp=incident_fingerprint(project_id,rule_id,resource_key,anomaly_type)
        stmt=select(Incident).where(Incident.project_id==project_id,Incident.fingerprint==fp,Incident.status.in_(['open','investigating','diagnosed'])).order_by(Incident.first_seen.desc()).limit(1)
        now=datetime.now().astimezone()
        inc=await self.active_incident_for_signal(project_id,rule_id,resource_key)
        if inc is None:inc=await self.session.scalar(stmt)
        if inc is None:inc=await self._recent_correlated_incident(project_id,now)
        is_new=inc is None
        added_signal=False
        upgraded=False
        if not inc:
            inc=Incident(project_id=project_id,fingerprint=fp,status='open',severity=severity,anomaly_type=anomaly_type,resource_key=resource_key,summary=f'{anomaly_type} triggered: {value}',first_seen=now,last_seen=now)
            self.session.add(inc);await self.session.flush()
        else:
            inc.last_seen=now
            upgraded=_SEVERITY_RANK.get(severity,0)>_SEVERITY_RANK.get(inc.severity,0)
            if upgraded:inc.severity=severity
        signal=await self.session.scalar(select(IncidentSignal).where(
            IncidentSignal.incident_id==inc.id,
            IncidentSignal.rule_id==rule_id,
            IncidentSignal.resource_key==resource_key,
        ).limit(1))
        if signal is None:
            signal=IncidentSignal(incident_id=inc.id,rule_id=rule_id,resource_key=resource_key,anomaly_type=anomaly_type,severity=severity,state='firing',last_value=value,first_seen=now,last_seen=now)
            self.session.add(signal)
            self.session.add(IncidentEvidence(
                incident_id=inc.id,type='monitoring_signal',source=f'rule:{rule_id}',
                summary=f'{anomaly_type} triggered: {value}',
                data={'rule_id':str(rule_id),'metric_key':anomaly_type,'resource_key':resource_key,'severity':severity,'value':value,'state':'firing'},
            ))
            added_signal=True
        else:
            signal.state='firing';signal.severity=severity;signal.last_value=value;signal.last_seen=now;signal.recovered_at=None
        # Keep the initial incident pipeline atomic.  A process restart between
        # separate commits used to leave a durable Incident without its first
        # Feishu notification or investigation job.  The rows below are now
        # flushed into one transaction and committed together; the existing
        # idempotency keys still make retries safe.
        project=await self.session.get(Project,project_id)
        project_name=getattr(project,'name','') or ''
        conv=await self.session.scalar(select(Conversation).where(Conversation.incident_id==inc.id).order_by(Conversation.created_at.asc()).limit(1))
        if conv is None and project is not None:
            conv=Conversation(user_id=project.user_id,title=f'🚨 {anomaly_type}',project_id=project_id,incident_id=inc.id,type='incident')
            self.session.add(conv)
            await self.session.flush()
        initial_key=f'incident:{inc.id}:triggered'
        initial_note=await self.session.scalar(select(Notification.id).where(Notification.dedupe_key==initial_key))
        if initial_note is None:
            self._queue_alert(inc,anomaly_type,resource_key,severity,value,now,project_name,kind='triggered',dedupe_suffix='triggered')
        if conv:
            await JobQueue(self.session).enqueue(
                'incident_investigate',
                {'incident_id':str(inc.id),'conversation_id':str(conv.id)},
                idempotency_key=f'incident_investigate:{inc.id}:initial',
                priority=20,
                commit=False,
            )
        await self.session.commit()
        await self.session.refresh(inc)
        if not is_new and upgraded:
            project=await self.session.get(Project,project_id)
            self._queue_alert(inc,anomaly_type,resource_key,severity,value,now,getattr(project,'name','') or '',kind='escalated',dedupe_suffix=f'escalated:{severity}',prefix='🔺 **告警升级**\n\n')
            await self.session.commit()
            await self._enqueue_reinvestigation(inc,f'upgrade:{severity}')
        elif not is_new and added_signal:
            # The first notification stays concise. New correlated evidence is
            # consumed by a fresh Agent pass and lands in the same message thread.
            await self._enqueue_reinvestigation(inc,f'signal:{rule_id}')
        return inc

    def _queue_alert(self,inc:Incident,anomaly_type:str,resource_key:str,severity:str,value:float,now,
                     project_name:str,kind:str,dedupe_suffix:str,prefix:str='')->None:
        """Stage-one notification. Written to the outbox table (not sent inline) so a
        Feishu outage can never lose or delay the alert, and so delivery is decoupled
        from the Agent investigation which may take minutes.

        Safe to call repeatedly: dedupe_key is unique, so a duplicate insert for the
        same incident/stage is rejected by the database.
        """
        self.session.add(Notification(
            incident_id=inc.id,
            channel='feishu',
            target='default',
            payload={
                'kind':kind,
                'incident_id':str(inc.id),
                'severity':severity,
                'anomaly_type':anomaly_type,
                'resource_key':resource_key,
                'value':value,
                'text':prefix+_alert_text(project_name,anomaly_type,resource_key,severity,value,now),
            },
            dedupe_key=f'incident:{inc.id}:{dedupe_suffix}',
        ))

    async def touch_firing(self,incident:Incident,severity:str,value:float,rule_id:UUID|None=None,resource_key:str='default')->Incident:
        """Refresh an already firing Incident without re-running the Agent every poll."""
        now=datetime.now().astimezone()
        incident.last_seen=now
        incident.summary=f'{incident.anomaly_type} still firing: {value}'
        upgraded=_SEVERITY_RANK.get(severity,0)>_SEVERITY_RANK.get(incident.severity,0)
        if upgraded:incident.severity=severity
        if rule_id is not None:
            signal=await self.session.scalar(select(IncidentSignal).where(
                IncidentSignal.incident_id==incident.id,
                IncidentSignal.rule_id==rule_id,
                IncidentSignal.resource_key==resource_key,
            ).limit(1))
            if signal:
                signal.last_seen=now;signal.last_value=value;signal.severity=severity;signal.state='firing';signal.recovered_at=None
        await self.session.commit()
        if upgraded:
            await self._enqueue_reinvestigation(incident,f'upgrade:{severity}')
        return incident

    async def on_recovered(self,project_id:UUID,rule_id:UUID,resource_key:str,anomaly_type:str)->Incident|None:
        """Recover one detector and close its incident only when all signals recover."""
        row=await self.session.execute(
            select(IncidentSignal,Incident)
            .join(Incident,Incident.id==IncidentSignal.incident_id)
            .where(
                Incident.project_id==project_id,
                Incident.status.in_(['open','investigating','diagnosed']),
                IncidentSignal.rule_id==rule_id,
                IncidentSignal.resource_key==resource_key,
                IncidentSignal.state=='firing',
            ).order_by(Incident.last_seen.desc()).limit(1)
        )
        pair=row.first()
        if pair is None:return None
        signal,inc=pair
        now=datetime.now().astimezone()
        signal.state='recovered';signal.recovered_at=now;signal.last_seen=now
        self.session.add(IncidentEvidence(
            incident_id=inc.id,type='monitoring_signal_recovered',source=f'rule:{rule_id}',
            summary=f'{anomaly_type} recovered',
            data={'rule_id':str(rule_id),'metric_key':anomaly_type,'resource_key':resource_key,'state':'recovered'},
        ))
        await self.session.flush()
        active=await self.session.scalar(select(func.count()).select_from(IncidentSignal).where(
            IncidentSignal.incident_id==inc.id,IncidentSignal.state=='firing'
        ))
        await self.session.commit()
        if int(active or 0)==0:return await self.resolve(inc.id)
        return inc

    async def resolve(self,incident_id:UUID,reason:str='rule_recovered')->Incident|None:
        inc=await self.session.get(Incident,incident_id)
        if not inc:return None
        if inc.status=='resolved':return inc
        inc.status='resolved';inc.resolved_at=datetime.now().astimezone();inc.last_seen=inc.resolved_at
        # Manual resolution is an operator override, not proof that the metric became healthy.
        # Reset the matching detector state so an unchanged abnormal metric can fire again
        # after trigger_for samples instead of entering a permanent blind spot.
        if reason=='manual_resolve':
            signal_rule_ids=list((await self.session.scalars(select(IncidentSignal.rule_id).where(IncidentSignal.incident_id==inc.id))).all())
            if not signal_rule_ids:
                matching_rule=await self.session.scalar(select(MonitoringRule).where(
                    MonitoringRule.project_id==inc.project_id,
                    MonitoringRule.metric_key==inc.anomaly_type,
                    MonitoringRule.resource_key==inc.resource_key,
                ).order_by(MonitoringRule.id.asc()).limit(1))
                signal_rule_ids=[matching_rule.id] if matching_rule else []
            for signal_rule_id in signal_rule_ids:
                rs=await self.session.get(MonitoringRuleState,signal_rule_id)
                if rs:
                    rs.state='normal';rs.abnormal_hits=0;rs.recovery_hits=0
            signals=list((await self.session.scalars(select(IncidentSignal).where(IncidentSignal.incident_id==inc.id))).all())
            for signal in signals:
                signal.state='recovered';signal.recovered_at=inc.resolved_at
        await self.session.commit()
        target='default'
        duration=''
        if inc.first_seen and inc.resolved_at:
            seconds=max(0,int((inc.resolved_at-inc.first_seen).total_seconds()))
            duration=f'{seconds // 60} 分钟' if seconds >= 60 else f'{seconds} 秒'
        recovery_text=(
            f'✅ [恢复] {inc.anomaly_type}\n'
            f'资源: {inc.resource_key}\n'
            f'状态: 已恢复\n'
            f'持续: {duration or "未知"}\n'
            f'恢复原因: {reason}'
        )
        self.session.add(Notification(
            incident_id=inc.id,channel='feishu',target=target,
            payload={'kind':'resolved','incident_id':str(inc.id),'severity':'info','summary':reason,'text':recovery_text},
            dedupe_key=f'incident:{inc.id}:resolved'
        ))
        await self.session.commit();return inc

    async def delete(self,incident_id:UUID)->bool:
        """Permanently remove one incident and its private investigation trail.

        Detector state is reset first so a genuinely persistent problem can
        create a new incident after satisfying the full trigger window again.
        Project rules and raw monitoring samples are intentionally preserved.
        """
        inc=await self.session.get(Incident,incident_id)
        if not inc:return False
        rule_ids=list((await self.session.scalars(
            select(IncidentSignal.rule_id).where(IncidentSignal.incident_id==incident_id)
        )).all())
        for rule_id in set(rule_ids):
            state=await self.session.get(MonitoringRuleState,rule_id)
            if state:
                state.state='normal';state.abnormal_hits=0;state.recovery_hits=0;state.last_value=None
        await self.session.execute(delete(BackgroundJob).where(
            BackgroundJob.payload['incident_id'].astext==str(incident_id)
        ))
        # Incident conversations are generated solely for this alert.  Removing
        # them also cascades their messages and Agent/tool runs.
        await self.session.execute(delete(Conversation).where(Conversation.incident_id==incident_id))
        await self.session.delete(inc)
        await self.session.commit()
        return True

    async def add_evidence(self,incident_id:UUID,type_:str,source:str,summary:str,data:dict|None=None,raw_ref:str|None=None)->IncidentEvidence:
        e=IncidentEvidence(incident_id=incident_id,type=type_,source=source,summary=summary,data=data or {},raw_ref=raw_ref)
        self.session.add(e);await self.session.commit();await self.session.refresh(e);return e
