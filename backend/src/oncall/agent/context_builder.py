from __future__ import annotations

import asyncio
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from oncall.application.conversation_service import ConversationService
from oncall.infrastructure.db.models import Diagnosis, Incident, IncidentEvidence, Project
from oncall.infrastructure.db.session import SessionFactory


class ContextBuilder:
    def __init__(self,session:AsyncSession,session_factory:async_sessionmaker|None=None):
        self.session=session
        self.session_factory=session_factory or SessionFactory

    async def build(self,conversation_id:UUID,project_id:UUID|None,incident_id:UUID|None)->dict:
        msgs_pack,project,incident_pack=await asyncio.gather(
            self._load_messages(conversation_id),
            self._load_project(project_id),
            self._load_incident(incident_id),
        )
        msgs,summary=msgs_pack
        incident,evidence,latest_diag=incident_pack
        if incident and project:incident['project_name']=project.get('name')
        return {'working_messages':[{'role':m.role,'content':m.content} for m in msgs],'conversation_summary':summary,'project_context':project,'incident_context':incident,'evidence':evidence,'previous_diagnosis':latest_diag}

    async def _load_messages(self,conversation_id:UUID):
        async with self.session_factory() as s:
            cs=ConversationService(s)
            msgs=await cs.recent_messages(conversation_id,30)
            summary=await cs.latest_summary(conversation_id)
            return msgs,summary.summary if summary else None

    async def _load_project(self,project_id:UUID|None):
        if not project_id:return None
        async with self.session_factory() as s:
            p=await s.get(Project,project_id)
            return {'id':str(p.id),'name':p.name,'description':p.description} if p else None

    async def _load_incident(self,incident_id:UUID|None):
        if not incident_id:return None,[],None
        async with self.session_factory() as s:
            inc=await s.get(Incident,incident_id)
            if not inc:return None,[],None
            incident={'id':str(inc.id),'status':inc.status,'severity':inc.severity,'anomaly_type':inc.anomaly_type,'resource_key':inc.resource_key,'summary':inc.summary,'first_seen':inc.first_seen.isoformat() if inc.first_seen else None,'last_seen':inc.last_seen.isoformat() if inc.last_seen else None,'project_name':None}
            ev=list((await s.scalars(select(IncidentEvidence).where(IncidentEvidence.incident_id==incident_id).order_by(IncidentEvidence.observed_at.asc()).limit(100))).all())
            evidence=[{'type':x.type,'source_tool':x.source,'observed_at':x.observed_at.isoformat() if x.observed_at else None,'summary':x.summary,'data':x.data,'source_ref':x.raw_ref} for x in ev]
            latest_diag=await s.scalar(select(Diagnosis).where(Diagnosis.incident_id==incident_id).order_by(Diagnosis.created_at.desc()).limit(1))
            return incident,evidence,latest_diag.structured_json if latest_diag else None
