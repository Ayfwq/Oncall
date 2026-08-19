from __future__ import annotations

from uuid import UUID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from oncall.application.conversation_service import ConversationService
from oncall.infrastructure.db.models import Diagnosis, Incident, IncidentEvidence, Project


class ContextBuilder:
    def __init__(self,session:AsyncSession):self.session=session
    async def build(self,conversation_id:UUID,project_id:UUID|None,incident_id:UUID|None)->dict:
        cs=ConversationService(self.session);msgs=await cs.recent_messages(conversation_id,30);summary=await cs.latest_summary(conversation_id)
        project=await self.session.get(Project,project_id) if project_id else None
        incident=None;evidence=[];latest_diag=None
        if incident_id:
            inc=await self.session.get(Incident,incident_id)
            if inc:
                incident={'id':str(inc.id),'status':inc.status,'severity':inc.severity,'anomaly_type':inc.anomaly_type,'resource_key':inc.resource_key,'summary':inc.summary,'first_seen':inc.first_seen.isoformat(),'last_seen':inc.last_seen.isoformat(),'project_name':project.name if project else None}
                ev=list((await self.session.scalars(select(IncidentEvidence).where(IncidentEvidence.incident_id==incident_id).order_by(IncidentEvidence.observed_at.asc()).limit(100))).all())
                evidence=[{'type':x.type,'source_tool':x.source,'observed_at':x.observed_at.isoformat(),'summary':x.summary,'data':x.data,'source_ref':x.raw_ref} for x in ev]
                latest_diag=await self.session.scalar(select(Diagnosis).where(Diagnosis.incident_id==incident_id).order_by(Diagnosis.created_at.desc()).limit(1))
        return {'working_messages':[{'role':m.role,'content':m.content} for m in msgs],'conversation_summary':summary.summary if summary else None,'project_context':{'id':str(project.id),'name':project.name,'description':project.description} if project else None,'incident_context':incident,'evidence':evidence,'previous_diagnosis':latest_diag.structured_json if latest_diag else None}
