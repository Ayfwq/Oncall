from __future__ import annotations

import time

import httpx

from oncall.application.dtos import ServiceEndpointDTO
from oncall.domain.schemas import ToolResult

from .base import CollectResult


class ServiceIntegration:
    name='service'
    def __init__(self,endpoints:list[ServiceEndpointDTO]):self.endpoints=endpoints

    async def _probe(self,e:ServiceEndpointDTO)->dict:
        start=time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=e.timeout_ms/1000.0) as client:
                r=await client.request(e.method,e.url)
            latency=(time.perf_counter()-start)*1000
            return {'name':e.name,'url':e.url,'reachable':True,'status_code':r.status_code,'expected_status':e.expected_status,'ok':r.status_code==e.expected_status,'latency_ms':latency}
        except Exception as ex:
            return {'name':e.name,'url':e.url,'reachable':False,'ok':False,'status_code':0,'expected_status':e.expected_status,'latency_ms':(time.perf_counter()-start)*1000,'error':str(ex)}

    async def collect(self)->CollectResult:
        rows=[await self._probe(x) for x in self.endpoints]
        f=rows[0] if rows else {'reachable':True,'status_code':200,'latency_ms':0,'ok':True}
        return CollectResult(name=self.name,ok=bool(f.get('ok')),signals={'service.reachable':1.0 if f.get('reachable') else 0.0,'service.status_code':float(f.get('status_code',0)),'service.latency_ms':float(f.get('latency_ms',0)),'service.consecutive_failures':0.0 if f.get('ok') else 1.0},resources={'endpoints':rows})

    async def query(self)->ToolResult:
        rows=[await self._probe(x) for x in self.endpoints]
        return ToolResult(ok=all(x.get('ok') for x in rows) if rows else True,summary=f'探测 {len(rows)} 个服务端点',data=rows)
