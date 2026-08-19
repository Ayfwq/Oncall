from __future__ import annotations

import asyncio
from oncall.application.dtos import DockerTargetDTO
from oncall.domain.schemas import ToolResult
from .base import CollectResult


class DockerIntegration:
    name='docker'
    def __init__(self,targets:list[DockerTargetDTO]): self.targets=targets

    async def collect(self)->CollectResult: return await asyncio.to_thread(self._collect_sync)

    def _client(self):
        import docker
        return docker.from_env()

    @staticmethod
    def _cpu_percent(stats:dict)->float:
        try:
            cpu_delta=stats['cpu_stats']['cpu_usage']['total_usage']-stats['precpu_stats']['cpu_usage']['total_usage']
            sys_delta=stats['cpu_stats']['system_cpu_usage']-stats['precpu_stats']['system_cpu_usage']
            cpus=len(stats['cpu_stats']['cpu_usage'].get('percpu_usage') or []) or 1
            return (cpu_delta/sys_delta)*cpus*100 if sys_delta>0 else 0.0
        except Exception:return 0.0

    def _inspect_rows(self):
        client=self._client(); rows=[]
        for t in self.targets:
            try:
                c=client.containers.get(t.container_ref); c.reload(); stats=c.stats(stream=False)
                mem_usage=float(stats.get('memory_stats',{}).get('usage',0)); mem_limit=float(stats.get('memory_stats',{}).get('limit',0) or 1)
                rows.append({'container':t.container_ref,'status':c.status,'running':c.status=='running','health':c.attrs.get('State',{}).get('Health',{}).get('Status','unknown'),'cpu_percent':self._cpu_percent(stats),'memory_percent':mem_usage/mem_limit*100,'restart_count':float(c.attrs.get('RestartCount',0))})
            except Exception as e: rows.append({'container':t.container_ref,'status':'unavailable','running':False,'health':'unknown','error':str(e),'cpu_percent':0.0,'memory_percent':0.0,'restart_count':0.0})
        return rows

    def _collect_sync(self)->CollectResult:
        try: rows=self._inspect_rows()
        except Exception as e:return CollectResult(name=self.name,ok=False,error=str(e))
        first=rows[0] if rows else {'running':True,'health':'unknown','cpu_percent':0,'memory_percent':0,'restart_count':0}
        health={'healthy':1.0,'unhealthy':0.0,'unknown':-1.0}.get(first.get('health'),-1.0)
        return CollectResult(name=self.name,ok=True,signals={'container.running':1.0 if first.get('running') else 0.0,'container.health':health,'container.cpu_percent':float(first.get('cpu_percent',0)),'container.memory_percent':float(first.get('memory_percent',0)),'container.restart_count':float(first.get('restart_count',0))},resources={'containers':rows})

    async def query(self)->ToolResult:
        try: rows=await asyncio.to_thread(self._inspect_rows);return ToolResult(ok=True,summary=f'检查 {len(rows)} 个容器',data=rows)
        except Exception as e:return ToolResult(ok=False,summary='Docker 查询失败',error_code='DOCKER_UNAVAILABLE',data={'error':str(e)})
