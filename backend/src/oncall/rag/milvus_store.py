from __future__ import annotations

import asyncio
from typing import Any
from oncall.bootstrap.config import get_settings


class MilvusKnowledgeIndex:
    def __init__(self):
        self.settings=get_settings();self.collection=f'oncall_knowledge_{self.settings.knowledge_index_version}_{self.settings.embedding_dimension}'

    def _client(self):
        from pymilvus import MilvusClient
        return MilvusClient(uri=self.settings.milvus_uri,token=self.settings.milvus_token)

    async def ensure(self)->None: await asyncio.to_thread(self._ensure_sync)
    def _ensure_sync(self)->None:
        from pymilvus import DataType, Function, FunctionType
        c=self._client()
        if c.has_collection(self.collection):return
        schema=c.create_schema(auto_id=False,enable_dynamic_field=False)
        schema.add_field('id',DataType.VARCHAR,is_primary=True,max_length=64)
        schema.add_field('document_id',DataType.VARCHAR,max_length=64)
        schema.add_field('version_id',DataType.VARCHAR,max_length=64)
        schema.add_field('project_scope',DataType.VARCHAR,max_length=64)
        schema.add_field('title',DataType.VARCHAR,max_length=1000)
        schema.add_field('page_range',DataType.VARCHAR,max_length=80)
        schema.add_field('content',DataType.VARCHAR,max_length=65535,enable_analyzer=True)
        schema.add_field('dense',DataType.FLOAT_VECTOR,dim=self.settings.embedding_dimension)
        schema.add_field('sparse',DataType.SPARSE_FLOAT_VECTOR)
        schema.add_function(Function(name='bm25_fn',input_field_names=['content'],output_field_names=['sparse'],function_type=FunctionType.BM25))
        index=c.prepare_index_params();index.add_index('dense',index_type='AUTOINDEX',metric_type='COSINE');index.add_index('sparse',index_type='SPARSE_INVERTED_INDEX',metric_type='BM25')
        c.create_collection(collection_name=self.collection,schema=schema,index_params=index)

    async def upsert(self,rows:list[dict])->None:
        if not rows:return
        await self.ensure();await asyncio.to_thread(self._client().upsert,collection_name=self.collection,data=rows)

    async def delete_version(self,version_id:str)->None:
        await self.ensure();await asyncio.to_thread(self._client().delete,collection_name=self.collection,filter=f'version_id == "{version_id}"')

    async def dense_search(self,vector:list[float],project_scope:str|None,limit:int=20)->list[dict]:
        await self.ensure();flt='' if not project_scope else f'project_scope == "" or project_scope == "{project_scope}"'
        res=await asyncio.to_thread(self._client().search,collection_name=self.collection,data=[vector],anns_field='dense',limit=limit,filter=flt or '',output_fields=['document_id','version_id','project_scope','title','page_range','content'])
        return [dict(hit.get('entity',{}),id=str(hit.get('id')),score=float(hit.get('distance',0))) for hit in (res[0] if res else [])]

    async def bm25_search(self,query:str,project_scope:str|None,limit:int=20)->list[dict]:
        await self.ensure();flt='' if not project_scope else f'project_scope == "" or project_scope == "{project_scope}"'
        res=await asyncio.to_thread(self._client().search,collection_name=self.collection,data=[query],anns_field='sparse',limit=limit,filter=flt or '',output_fields=['document_id','version_id','project_scope','title','page_range','content'])
        return [dict(hit.get('entity',{}),id=str(hit.get('id')),score=float(hit.get('distance',0))) for hit in (res[0] if res else [])]
