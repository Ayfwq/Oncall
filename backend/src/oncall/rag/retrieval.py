from __future__ import annotations

from uuid import UUID
from oncall.domain.schemas import CitationRef, ToolResult
from oncall.rag.embedding import get_embedding_provider
from oncall.rag.milvus_store import MilvusKnowledgeIndex
from oncall.rag.rerank import Reranker


def rrf(lists:list[list[dict]],k:int=60)->list[dict]:
    merged={}
    for hits in lists:
        for rank,item in enumerate(hits,1):
            key=item['id'];m=merged.setdefault(key,dict(item,rrf_score=0.0));m['rrf_score']+=1.0/(k+rank)
    return sorted(merged.values(),key=lambda x:x['rrf_score'],reverse=True)


class KnowledgeRetriever:
    def __init__(self):self.embedder=get_embedding_provider();self.index=MilvusKnowledgeIndex();self.reranker=Reranker()

    async def search(self,query:str,project_id:UUID|None=None,top_k:int=5)->ToolResult:
        try:
            query=' '.join(str(query).strip().split())[:1000]
            if not query:
                return ToolResult(ok=False,summary='知识库检索参数为空',error_code='INVALID_QUERY')
            top_k=max(1,min(int(top_k),10))
            vector=(await self.embedder.embed([query]))[0]
            scope=str(project_id) if project_id else None
            dense,bm25=await __import__('asyncio').gather(self.index.dense_search(vector,scope,20),self.index.bm25_search(query,scope,20))
            candidates=rrf([dense,bm25])[:30];items=await self.reranker.rerank(query,candidates,top_k=top_k)
            return ToolResult(ok=True,summary=f'知识库命中 {len(items)} 条',data=items)
        except Exception as e:
            return ToolResult(ok=False,summary='知识库检索不可用',error_code='RAG_UNAVAILABLE',data={'error':str(e)})
