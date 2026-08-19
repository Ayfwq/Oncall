from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from oncall.application.project_service import ProjectService
from oncall.domain.schemas import ToolResult
from oncall.infrastructure.db.models import MetricSample, RetrievalTrace, ToolRun
from oncall.integrations.database import DatabaseIntegration
from oncall.integrations.docker_integration import DockerIntegration
from oncall.integrations.host import HostIntegration
from oncall.integrations.logs import LogIntegration
from oncall.integrations.process import ProcessIntegration
from oncall.integrations.service import ServiceIntegration
from oncall.rag.retrieval import KnowledgeRetriever
from oncall.security.redact import redact_text
from oncall.agent.tool_contracts import ALLOWED_TOOLS, validate_tool_args


@dataclass(frozen=True)
class ToolExecutionContext:
    project_id:UUID|None
    incident_id:UUID|None
    agent_run_id:UUID


class ToolRegistry:
    def __init__(self,session:AsyncSession):self.session=session

    async def execute(self,name:str,args:dict,ctx:ToolExecutionContext,timeout:float=15)->ToolResult:
        if name not in ALLOWED_TOOLS:return ToolResult(ok=False,summary='工具未授权',error_code='TOOL_NOT_ALLOWED')
        if name!='search_knowledge' and not ctx.project_id:return ToolResult(ok=False,summary='该工具需要绑定 Project',error_code='PROJECT_REQUIRED')
        start=time.perf_counter();params_hash=hashlib.sha256(json.dumps(args,sort_keys=True,default=str).encode()).hexdigest()
        args_ok,args_error=validate_tool_args(name,args)
        if not args_ok:
            result=ToolResult(ok=False,summary=f'工具参数无效: {args_error}',error_code='INVALID_TOOL_ARGS');status='error'
        else:
            try:
                result=await asyncio.wait_for(self._dispatch(name,args,ctx),timeout=timeout);status='ok' if result.ok else 'error'
            except TimeoutError:result=ToolResult(ok=False,summary='工具执行超时',error_code='TIMEOUT');status='timeout'
            except Exception as e:result=ToolResult(ok=False,summary='工具执行失败',error_code='TOOL_ERROR',data={'error':str(e)});status='error'
        result.summary=redact_text(result.summary);latency=(time.perf_counter()-start)*1000
        if isinstance(result.data,list):
            result_size=len(result.data)
        elif isinstance(result.data,dict):
            result_size=len(result.data)
        elif result.data is None:
            result_size=0
        else:
            result_size=1
        self.session.add(ToolRun(
            agent_run_id=ctx.agent_run_id,tool_name=name,params_hash=params_hash,status=status,
            summary=result.summary,latency_ms=latency,result_size=result_size,
            truncated=result.truncated,error_code=result.error_code,
        ))
        if name=='search_knowledge':
            refs=[]
            if isinstance(result.data,list):
                refs=[{
                    'chunk_id':str(x.get('id','')),
                    'document_id':str(x.get('document_id','')),
                    'version_id':str(x.get('version_id','')),
                    'title':x.get('title'),
                    'page_range':x.get('page_range'),
                    'score':x.get('rerank_score',x.get('rrf_score')),
                } for x in result.data[:20]]
            self.session.add(RetrievalTrace(
                agent_run_id=ctx.agent_run_id,project_id=ctx.project_id,query=str(args.get('query','')),
                hit_count=len(refs),refs=refs,latency_ms=latency,status='ok' if result.ok else 'error',
                error_code=result.error_code,
            ))
        await self.session.commit()
        return result

    async def _dispatch(self,name:str,args:dict,ctx:ToolExecutionContext)->ToolResult:
        if name=='search_knowledge':return await KnowledgeRetriever().search(str(args.get('query','')),ctx.project_id,top_k=int(args.get('top_k',5)))
        cfg=await ProjectService(self.session).runtime_config(ctx.project_id)
        if name=='query_host_metrics':return await HostIntegration().query()
        if name=='query_processes':return await ProcessIntegration(cfg.process_targets).query(int(args.get('limit',30)))
        if name=='query_logs':return await LogIntegration(cfg.log_sources).query(str(args.get('keyword','')),str(args.get('level','')),int(args.get('limit',100)))
        if name=='query_containers':return await DockerIntegration(cfg.docker_targets).query()
        if name=='query_database':return await DatabaseIntegration(cfg.database_profiles).query()
        if name=='query_service_health':return await ServiceIntegration(cfg.service_endpoints).query()
        if name=='query_metric_history':
            key=str(args.get('metric','host.cpu.percent'));hours=max(1,min(int(args.get('hours',1)),168));since=datetime.now().astimezone()-timedelta(hours=hours)
            rows=list((await self.session.scalars(select(MetricSample).where(MetricSample.project_id==ctx.project_id,MetricSample.metric_key==key,MetricSample.ts>=since).order_by(MetricSample.ts.asc()).limit(1000))).all())
            return ToolResult(ok=True,summary=f'{key} 过去 {hours}h 共 {len(rows)} 个采样',data=[{'ts':r.ts.isoformat(),'value':r.value} for r in rows],truncated=len(rows)>=1000)
        raise KeyError(name)
