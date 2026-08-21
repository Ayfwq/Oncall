from __future__ import annotations

from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from oncall.infrastructure.db.models import KnowledgeDocument
from oncall.jobs.queue import JobQueue
from oncall.rag.ingestion import KnowledgeIngestor
from oncall.rag.retrieval import KnowledgeRetriever


class KnowledgeService:
    def __init__(self,session:AsyncSession):self.session=session
    async def list_documents(self,user_id:UUID):return list((await self.session.scalars(select(KnowledgeDocument).where(KnowledgeDocument.user_id==user_id).order_by(KnowledgeDocument.updated_at.desc()))).all())
    async def upload(self,user_id:UUID,path:Path,title:str|None=None,project_scope:UUID|None=None):
        ver=await KnowledgeIngestor(self.session).register_upload(user_id,path,title,project_scope);job=await JobQueue(self.session).enqueue('rag_ingest',{'version_id':str(ver.id)},idempotency_key=f'rag_ingest:{ver.id}',priority=50);return ver,job
    async def search(self,query:str,project_id:UUID|None=None):return await KnowledgeRetriever().search(query,project_id)
