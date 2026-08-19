from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
from pathlib import Path
from uuid import UUID
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from oncall.bootstrap.config import get_settings
from oncall.infrastructure.db.models import KnowledgeChunk, KnowledgeDocument, KnowledgeDocumentVersion
from oncall.rag.embedding import get_embedding_provider
from oncall.rag.milvus_store import MilvusKnowledgeIndex

ALLOWED_SUFFIXES={'.pdf','.docx','.pptx','.html','.htm','.md','.txt','.xlsx'}


def checksum(path:Path)->str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


class KnowledgeIngestor:
    def __init__(self,session:AsyncSession):self.session=session;self.settings=get_settings();self.embedder=get_embedding_provider();self.index=MilvusKnowledgeIndex()

    async def register_upload(self,user_id:UUID,source:Path,title:str|None=None,project_scope:UUID|None=None)->KnowledgeDocumentVersion:
        if source.suffix.lower() not in ALLOWED_SUFFIXES:raise ValueError(f'unsupported file type: {source.suffix}')
        cs=await asyncio.to_thread(checksum,source);resolved_title=title or source.stem
        # Same title + scope is treated as a new version of the same logical document.
        # Uploading identical bytes is idempotent and returns the existing version.
        doc=await self.session.scalar(select(KnowledgeDocument).where(KnowledgeDocument.user_id==user_id,KnowledgeDocument.project_scope==project_scope,KnowledgeDocument.title==resolved_title).order_by(KnowledgeDocument.created_at.asc()).limit(1))
        if not doc:
            doc=KnowledgeDocument(user_id=user_id,project_scope=project_scope,title=resolved_title,status='uploaded');self.session.add(doc);await self.session.flush()
        existing=await self.session.scalar(select(KnowledgeDocumentVersion).where(KnowledgeDocumentVersion.document_id==doc.id,KnowledgeDocumentVersion.checksum==cs).limit(1))
        if existing:return existing
        target_dir=self.settings.knowledge_dir/str(doc.id)/cs;target_dir.mkdir(parents=True,exist_ok=True);raw=target_dir/source.name
        await asyncio.to_thread(shutil.copy2,source,raw)
        ver=KnowledgeDocumentVersion(document_id=doc.id,checksum=cs,original_filename=source.name,raw_path=str(raw),status='uploaded');self.session.add(ver);doc.status='uploaded';await self.session.commit();await self.session.refresh(ver);return ver

    async def ingest_version(self,version_id:UUID)->None:
        ver=await self.session.get(KnowledgeDocumentVersion,version_id)
        if not ver:
            # Document was deleted before this durable job was claimed; nothing to do.
            return
        doc=await self.session.get(KnowledgeDocument,ver.document_id)
        if not doc:
            return
        ver.status='processing';doc.status='processing';await self.session.commit()
        try:
            converted=await asyncio.to_thread(self._convert,Path(ver.raw_path))
            outdir=Path(ver.raw_path).parent;json_path=outdir/'document.json';md_path=outdir/'document.md'
            await asyncio.to_thread(json_path.write_text,json.dumps(converted['json'],ensure_ascii=False,indent=2),encoding='utf-8')
            await asyncio.to_thread(md_path.write_text,converted['markdown'],encoding='utf-8')
            ver.canonical_json_path=str(json_path);ver.canonical_md_path=str(md_path)
            await self.session.execute(delete(KnowledgeChunk).where(KnowledgeChunk.version_id==ver.id))
            chunks=[]
            for i,ch in enumerate(converted['chunks']):
                row=KnowledgeChunk(version_id=ver.id,chunk_index=i,heading_path=ch['headings'],page_range=ch['page_range'],content=ch['content'],metadata_json=ch['metadata']);self.session.add(row);chunks.append(row)
            await self.session.flush()
            embeddings=await self.embedder.embed([x.content for x in chunks]) if chunks else []
            rows=[]
            for c,vec in zip(chunks,embeddings,strict=False):
                rows.append({'id':str(c.id),'document_id':str(doc.id),'version_id':str(ver.id),'project_scope':str(doc.project_scope or ''),'title':doc.title[:1000],'page_range':(c.page_range or '')[:80],'content':c.content[:65535],'dense':vec})
            await self.index.delete_version(str(ver.id));await self.index.upsert(rows)
            ver.status='ready';doc.status='ready';doc.active_version_id=ver.id;await self.session.commit()
        except Exception as e:
            # A document may be deleted while a durable RAG job is still running. Never
            # let that race crash the worker or wedge the session in a broken transaction.
            await self.session.rollback()
            ver2=await self.session.get(KnowledgeDocumentVersion,version_id)
            if ver2 is None:
                return  # document/version removed concurrently; nothing left to persist
            doc2=await self.session.get(KnowledgeDocument,ver2.document_id)
            ver2.status='failed';ver2.error=str(e)[:8000]
            if doc2 is not None:doc2.status='failed'
            await self.session.commit();raise

    def _convert(self,path:Path)->dict:
        from docling.document_converter import DocumentConverter
        from docling.chunking import HybridChunker
        result=DocumentConverter().convert(path);dl=result.document
        chunker=HybridChunker(merge_peers=True)
        chunks=[]
        def page_info(meta):
            pages=set()
            if not meta:
                return None, []
            for raw in list(getattr(meta,'doc_items',[]) or []):
                item=raw[0] if isinstance(raw,(tuple,list)) and raw else raw
                for prov in list(getattr(item,'prov',[]) or []):
                    page=getattr(prov,'page_no',None)
                    if isinstance(page,int):pages.add(page)
            ordered=sorted(pages)
            if not ordered:return None, []
            text=str(ordered[0]) if len(ordered)==1 else f'{ordered[0]}-{ordered[-1]}'
            return text,ordered

        for ch in chunker.chunk(dl_doc=dl):
            text=chunker.contextualize(ch).strip()
            if not text:continue
            meta=getattr(ch,'meta',None);headings=list(getattr(meta,'headings',[]) or []) if meta else []
            page_range,pages=page_info(meta)
            chunks.append({'content':text,'headings':headings,'page_range':page_range,'metadata':{'headings':headings,'pages':pages}})
        return {'json':dl.export_to_dict(),'markdown':dl.export_to_markdown(),'chunks':chunks}
