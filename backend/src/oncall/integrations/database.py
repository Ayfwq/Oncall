from __future__ import annotations

import asyncpg
from oncall.application.dtos import DatabaseProfileDTO
from oncall.domain.schemas import ToolResult
from .base import CollectResult


class DatabaseIntegration:
    name='database'
    def __init__(self,profiles:list[DatabaseProfileDTO]): self.profiles=profiles

    async def _conn(self,p:DatabaseProfileDTO):
        return await asyncpg.connect(host=p.host,port=p.port,database=p.database,user=p.username,password=p.password or '',ssl=p.sslmode if p.sslmode not in ('disable','prefer') else None,timeout=3)

    async def _query_one(self,p:DatabaseProfileDTO)->dict:
        conn=await self._conn(p)
        try:
            max_conn=int(await conn.fetchval("SHOW max_connections")); active=int(await conn.fetchval("SELECT count(*) FROM pg_stat_activity"))
            long_q=int(await conn.fetchval("SELECT count(*) FROM pg_stat_activity WHERE state <> 'idle' AND query_start < now() - interval '30 seconds'"))
            locks=int(await conn.fetchval("SELECT count(*) FROM pg_stat_activity WHERE wait_event_type='Lock'"))
            dead=int(await conn.fetchval("SELECT coalesce(sum(deadlocks),0) FROM pg_stat_database"))
            return {'database':p.database,'reachable':True,'connections':active,'max_connections':max_conn,'connections_usage_percent':active/max_conn*100 if max_conn else 0,'long_query_count':long_q,'lock_wait_count':locks,'deadlocks':dead}
        finally: await conn.close()

    async def collect(self)->CollectResult:
        if not self.profiles:return CollectResult(name=self.name,ok=True)
        rows=[]
        for p in self.profiles:
            try:rows.append(await self._query_one(p))
            except Exception as e:rows.append({'database':p.database,'reachable':False,'error':str(e),'connections_usage_percent':0,'long_query_count':0,'lock_wait_count':0,'deadlocks':0})
        f=rows[0]
        return CollectResult(name=self.name,ok=bool(f.get('reachable')),signals={'db.reachable':1.0 if f.get('reachable') else 0.0,'db.connections.usage_percent':float(f.get('connections_usage_percent',0)),'db.long_query.count':float(f.get('long_query_count',0)),'db.lock_wait.count':float(f.get('lock_wait_count',0)),'db.deadlock.delta':float(f.get('deadlocks',0))},resources={'databases':rows})

    async def query(self)->ToolResult:
        rows=[]
        for p in self.profiles:
            try:rows.append(await self._query_one(p))
            except Exception as e:rows.append({'database':p.database,'reachable':False,'error':str(e)})
        return ToolResult(ok=all(x.get('reachable') for x in rows) if rows else True,summary=f'检查 {len(rows)} 个 PostgreSQL 配置',data=rows)
