from __future__ import annotations

import hashlib
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from oncall.application.conversation_service import ConversationService
from oncall.infrastructure.db.models import (
    Conversation,
    Incident,
    IncidentEvidence,
    MonitoringRule,
    MonitoringRuleState,
    Notification,
    Project,
)
from oncall.jobs.queue import JobQueue

_SEVERITY_RANK={'info':0,'warning':1,'critical':2}
_SEVERITY_LABEL={'info':'提示','warning':'警告','critical':'严重'}


def _format_value(value:float)->str:
    """Keep alert text readable: 0.1234 -> 0.123, 1234567.0 -> 1234567."""
    if value!=value or value in (float('inf'),float('-inf')):return str(value)
    if float(value).is_integer():return str(int(value))
    return f'{value:.3f}'.rstrip('0').rstrip('.')


def _alert_text(project_name:str,anomaly_type:str,resource_key:str,severity:str,value:float,now)->str:
    """First-stage alert body. Intentionally short and factual: what broke, where,
    how bad. Root-cause analysis arrives later as a separate diagnosis message."""
    label=_SEVERITY_LABEL.get(severity,severity)
    lines=[
        f'**{anomaly_type}**',
        '',
        f'**级别**：{label}',
        f'**项目**：{project_name or "-"}',
        f'**资源**：{resource_key or "default"}',
        f'**当前值**：{_format_value(value)}',
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

    async def on_firing(self,project_id:UUID,rule_id:UUID,resource_key:str,anomaly_type:str,severity:str,value:float)->Incident:
        fp=incident_fingerprint(project_id,rule_id,resource_key,anomaly_type)
        stmt=select(Incident).where(Incident.project_id==project_id,Incident.fingerprint==fp,Incident.status.in_(['open','investigating','diagnosed'])).order_by(Incident.first_seen.desc()).limit(1)
        inc=await self.session.scalar(stmt);now=datetime.now().astimezone();is_new=inc is None
        if not inc:
            inc=Incident(project_id=project_id,fingerprint=fp,status='open',severity=severity,anomaly_type=anomaly_type,resource_key=resource_key,summary=f'{anomaly_type} triggered: {value}',first_seen=now,last_seen=now)
            self.session.add(inc);await self.session.flush()
        else:
            inc.last_seen=now
            upgraded=_SEVERITY_RANK.get(severity,0)>_SEVERITY_RANK.get(inc.severity,0)
            if upgraded:inc.severity=severity
        await self.session.commit();await self.session.refresh(inc)
        if is_new:
            # Incident conversation is durable and becomes the Web/Feishu follow-up anchor.
            project=await self.session.get(Project,project_id)
            project_name=getattr(project,'name','') or ''
            user_id=project.user_id if project else None
            if user_id is not None:
                conv=await ConversationService(self.session).create(user_id,title=f'🚨 {anomaly_type}',project_id=project_id,incident_id=inc.id,type_='incident')
                conversation_id=str(conv.id)
            else:
                conversation_id=None
            self._queue_alert(inc,anomaly_type,resource_key,severity,value,now,project_name,kind='triggered',dedupe_suffix='triggered')
            await self.session.commit()
            if conversation_id:
                await JobQueue(self.session).enqueue('incident_investigate',{'incident_id':str(inc.id),'conversation_id':conversation_id},idempotency_key=f'incident_investigate:{inc.id}:initial',priority=20)
        elif upgraded:
            project=await self.session.get(Project,project_id)
            self._queue_alert(inc,anomaly_type,resource_key,severity,value,now,getattr(project,'name','') or '',kind='escalated',dedupe_suffix=f'escalated:{severity}',prefix='🔺 **告警升级**\n\n')
            await self.session.commit()
            await self._enqueue_reinvestigation(inc,f'upgrade:{severity}')
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

    async def touch_firing(self,incident:Incident,severity:str,value:float)->Incident:
        """Refresh an already firing Incident without re-running the Agent every poll."""
        now=datetime.now().astimezone()
        incident.last_seen=now
        incident.summary=f'{incident.anomaly_type} still firing: {value}'
        upgraded=_SEVERITY_RANK.get(severity,0)>_SEVERITY_RANK.get(incident.severity,0)
        if upgraded:incident.severity=severity
        await self.session.commit()
        if upgraded:
            await self._enqueue_reinvestigation(incident,f'upgrade:{severity}')
        return incident

    async def resolve(self,incident_id:UUID,reason:str='rule_recovered')->Incident|None:
        inc=await self.session.get(Incident,incident_id)
        if not inc:return None
        if inc.status=='resolved':return inc
        inc.status='resolved';inc.resolved_at=datetime.now().astimezone();inc.last_seen=inc.resolved_at
        # Manual resolution is an operator override, not proof that the metric became healthy.
        # Reset the matching detector state so an unchanged abnormal metric can fire again
        # after trigger_for samples instead of entering a permanent blind spot.
        if reason=='manual_resolve':
            rule=await self.session.scalar(select(MonitoringRule).where(
                MonitoringRule.project_id==inc.project_id,
                MonitoringRule.metric_key==inc.anomaly_type,
                MonitoringRule.resource_key==inc.resource_key,
            ).order_by(MonitoringRule.id.asc()).limit(1))
            if rule:
                rs=await self.session.get(MonitoringRuleState,rule.id)
                if rs:
                    rs.state='normal';rs.abnormal_hits=0;rs.recovery_hits=0
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
            payload={'kind':'resolved','incident_id':str(inc.id),'summary':reason,'text':recovery_text},
            dedupe_key=f'incident:{inc.id}:resolved'
        ))
        await self.session.commit();return inc

    async def add_evidence(self,incident_id:UUID,type_:str,source:str,summary:str,data:dict|None=None,raw_ref:str|None=None)->IncidentEvidence:
        e=IncidentEvidence(incident_id=incident_id,type=type_,source=source,summary=summary,data=data or {},raw_ref=raw_ref)
        self.session.add(e);await self.session.commit();await self.session.refresh(e);return e
