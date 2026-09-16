from __future__ import annotations

import time

import httpx

from oncall.application.dtos import ServiceEndpointDTO
from oncall.domain.schemas import ToolResult
from oncall.security.redact import redact_text

from .base import CollectResult


class ServiceIntegration:
    name='service'
    def __init__(self,endpoints:list[ServiceEndpointDTO]):self.endpoints=endpoints

    async def _probe(self,e:ServiceEndpointDTO,include_body:bool=False)->dict:
        start=time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=e.timeout_ms/1000.0, trust_env=False) as client:
                r=await client.request(e.method,e.url)
            latency=(time.perf_counter()-start)*1000
            row={'name':e.name,'url':e.url,'reachable':True,'status_code':r.status_code,'expected_status':e.expected_status,'ok':r.status_code==e.expected_status,'latency_ms':latency}
            if include_body:
                row['content_type']=r.headers.get('content-type','')[:100]
                row['body']=redact_text(r.text[:2000])
                row['body_truncated']=len(r.text)>2000
            return row
        except Exception as ex:
            return {'name':e.name,'url':e.url,'reachable':False,'ok':False,'status_code':0,'expected_status':e.expected_status,'latency_ms':(time.perf_counter()-start)*1000,'error':str(ex)}

    async def collect(self)->CollectResult:
        rows=[await self._probe(x) for x in self.endpoints]
        f=rows[0] if rows else {'reachable':True,'status_code':200,'latency_ms':0,'ok':True}
        resource_signals={}
        for endpoint, row in zip(self.endpoints, rows):
            key=str(endpoint.id or endpoint.name)
            resource_signals[key]={'service.reachable':1.0 if row.get('reachable') else 0.0,'service.status_code':float(row.get('status_code',0)),'service.latency_ms':float(row.get('latency_ms',0)),'service.consecutive_failures':0.0 if row.get('ok') else 1.0}
        return CollectResult(name=self.name,ok=bool(f.get('ok')),signals={'service.reachable':1.0 if f.get('reachable') else 0.0,'service.status_code':float(f.get('status_code',0)),'service.latency_ms':float(f.get('latency_ms',0)),'service.consecutive_failures':0.0 if f.get('ok') else 1.0},resource_signals=resource_signals,resources={'endpoints':rows})

    async def query(self,endpoint:str|None=None,include_body:bool=False)->ToolResult:
        if not self.endpoints:
            return ToolResult(ok=False,summary='项目没有配置服务健康检查端点',error_code='SERVICE_ENDPOINT_REQUIRED')
        selected=self.endpoints
        if endpoint:
            selected=[x for x in self.endpoints if str(x.id)==endpoint or x.name==endpoint]
            if not selected:
                return ToolResult(ok=False,summary='未找到指定服务端点',error_code='ENDPOINT_NOT_FOUND')
        rows=[await self._probe(x,include_body) for x in selected]
        return ToolResult(ok=all(x.get('ok') for x in rows) if rows else True,summary=f'探测 {len(rows)} 个服务端点',data=rows)
