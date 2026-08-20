from __future__ import annotations

from datetime import datetime
from uuid import UUID
from langgraph.graph import END, START, StateGraph
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from oncall.agent.context_builder import ContextBuilder
from oncall.agent.model_gateway import ModelProvider, get_model_provider
from oncall.agent.router import classify_intent
from oncall.agent.state import OncallState
from oncall.agent.tool_contracts import ALLOWED_TOOLS, public_tool_specs
from oncall.agent.tool_registry import ToolExecutionContext, ToolRegistry
from oncall.application.conversation_service import ConversationService
from oncall.domain.enums import AgentMode
from oncall.domain.schemas import DiagnosisReport, EvidenceItem
from oncall.infrastructure.db.models import AgentRun, Diagnosis, Incident, IncidentEvidence, Notification

BUDGETS={
    'chat':(5,4),
    'follow_up':(6,5),
    'investigate':(10,8),
    'deep':(14,12),
}


class OncallGraphRuntime:
    def __init__(self,session:AsyncSession,model:ModelProvider|None=None,emit=None):
        self.session=session;self.model=model or get_model_provider();self.tools=ToolRegistry(session);self.context=ContextBuilder(session)
        self.emit=emit  # optional sync callable emit(event_type:str, data:dict)

    def _emit(self,event_type:str,data:dict|None=None)->None:
        if self.emit:
            try:self.emit(event_type,data or {})
            except Exception:pass

    def _context(self,state:OncallState)->dict:
        context={k:state.get(k) for k in ('mode','user_message','project_id','project_context','incident_context','previous_diagnosis','conversation_summary','working_messages','evidence','called_tools','tool_calls_used','tool_budget','knowledge_refs','knowledge_hits','knowledge_status','intent','route_reason','requires_realtime','requires_project')}
        allowed=set(state.get('allowed_tools') or [])
        context['available_tools']=[x for x in public_tool_specs() if x['name'] in allowed]
        return context

    def build(self,checkpointer=None):
        g=StateGraph(OncallState)
        g.add_node('load_context',self.load_context)
        g.add_node('route_intent',self.route_intent)
        g.add_node('retrieve_knowledge',self.retrieve_knowledge)
        g.add_node('reason',self.reason)
        g.add_node('guard_tools',self.guard_tools)
        g.add_node('execute_tools',self.execute_tools)
        g.add_node('record_observations',self.record_observations)
        g.add_node('finalize',self.finalize)
        g.add_node('persist_result',self.persist_result)
        g.add_edge(START,'load_context');g.add_edge('load_context','route_intent')
        g.add_conditional_edges('route_intent',self.route_after_route,{'knowledge':'retrieve_knowledge','reason':'reason','clarify':'finalize'})
        g.add_edge('retrieve_knowledge','reason')
        g.add_conditional_edges('reason',self.route_after_reason,{'tool':'guard_tools','final':'finalize'})
        g.add_conditional_edges('guard_tools',self.route_after_guard,{'tool':'execute_tools','final':'finalize'})
        g.add_edge('execute_tools','record_observations');g.add_edge('record_observations','reason')
        g.add_edge('finalize','persist_result');g.add_edge('persist_result',END)
        return g.compile(checkpointer=checkpointer)

    async def load_context(self,state:OncallState)->dict:
        built=await self.context.build(UUID(state['conversation_id']),UUID(state['project_id']) if state.get('project_id') else None,UUID(state['incident_id']) if state.get('incident_id') else None)
        tool_budget,loop_budget=BUDGETS.get(state.get('mode','chat'),BUDGETS['chat'])
        # A LangGraph thread is durable across turns, but tool/loop budgets are
        # per AgentRun. Never inherit transient counters from the previous checkpoint.
        return {**built,'tool_calls_used':0,'tool_budget':tool_budget,'reason_loops':0,'reason_loop_budget':loop_budget,'called_tools':[],'knowledge_refs':[],'knowledge_hits':[],'knowledge_status':'skipped','allowed_tools':[],'tool_plan':[],'answer_sources':[],'pending_tool':None,'current_tool_result':None,'decision':{},'diagnosis':None,'final_response':None,'exhausted':False}

    async def route_intent(self,state:OncallState)->dict:
        route=classify_intent(state.get('user_message',''),project_id=state.get('project_id'),incident_id=state.get('incident_id'),mode=state.get('mode','chat'))
        if route.get('requires_realtime'):
            route['allowed_tools']=[x['name'] for x in public_tool_specs()]
        elif route.get('requires_knowledge'):
            route['allowed_tools']=['search_knowledge']
        else:
            route['allowed_tools']=[]
        self._emit('intent_routed',{'intent':route.get('intent'),'confidence':route.get('route_confidence'),'reason':route.get('route_reason')})
        if route.get('intent') == 'clarification':
            route['decision']={'action':'final','rationale':route.get('route_reason',''),'answer':route.get('clarification_question')}
        return route

    def route_after_route(self,state:OncallState)->str:
        if state.get('intent') == 'clarification': return 'clarify'
        return 'knowledge' if state.get('requires_knowledge') else 'reason'

    async def retrieve_knowledge(self,state:OncallState)->dict:
        query=str(state.get('user_message','')).strip()
        if not query: return {'knowledge_status':'skipped'}
        ctx=ToolExecutionContext(project_id=UUID(state['project_id']) if state.get('project_id') else None,incident_id=UUID(state['incident_id']) if state.get('incident_id') else None,agent_run_id=UUID(state['run_id']))
        self._emit('knowledge_started',{'query':query[:200]})
        try:
            result=await self.tools.execute('search_knowledge',{'query':query,'top_k':5},ctx)
            dumped=result.model_dump(mode='json');hits=dumped.get('data') if isinstance(dumped.get('data'),list) else []
            refs=[{'document_id':x.get('document_id',''),'version_id':x.get('version_id'),'chunk_id':x.get('id'),'title':x.get('title'),'page_range':x.get('page_range'),'score':x.get('rerank_score',x.get('rrf_score'))} for x in hits[:5]]
            self._emit('knowledge_finished',{'ok':bool(dumped.get('ok')),'count':len(hits),'summary':dumped.get('summary','')})
            key='search_knowledge:'+__import__('json').dumps({'query':query,'top_k':5},sort_keys=True,ensure_ascii=False)
            return {'knowledge_query':query,'knowledge_status':'hit' if hits else ('unavailable' if not dumped.get('ok') else 'empty'),'knowledge_hits':hits,'knowledge_refs':refs,'called_tools':[key],'answer_sources':[{'type':'knowledge','count':len(hits)}]}
        except Exception as exc:
            self._emit('knowledge_finished',{'ok':False,'count':0,'error':str(exc)[:300]})
            return {'knowledge_query':query,'knowledge_status':'unavailable','knowledge_hits':[],'knowledge_refs':[],'answer_sources':[{'type':'knowledge','count':0,'error':str(exc)[:300]}]}

    async def reason(self,state:OncallState)->dict:
        loops=state.get('reason_loops',0)+1
        if loops>state.get('reason_loop_budget',4) or state.get('tool_calls_used',0)>=state.get('tool_budget',5):
            return {'reason_loops':loops,'exhausted':True,'decision':{'action':'final','rationale':'budget exhausted','answer':None}}
        context=self._context(state)
        decision=await self.model.decide(context)
        return {'reason_loops':loops,'decision':decision.model_dump(mode='json')}

    def route_after_reason(self,state:OncallState)->str:
        return 'tool' if state.get('decision',{}).get('action')=='tool' else 'final'

    async def guard_tools(self,state:OncallState)->dict:
        d=state.get('decision',{});name=d.get('tool_name');args=d.get('tool_args') or {}
        if name not in ALLOWED_TOOLS:
            return {'pending_tool':None,'decision':{'action':'final','answer':f'工具 {name} 未授权。'},'exhausted':True}
        call_key=f"{name}:{__import__('json').dumps(args,sort_keys=True,ensure_ascii=False)}"
        if call_key in state.get('called_tools',[]):
            return {'pending_tool':None,'decision':{'action':'final','answer':'没有获得新的证据；停止重复工具调用。'},'exhausted':True}
        if state.get('tool_calls_used',0)>=state.get('tool_budget',5):
            return {'pending_tool':None,'decision':{'action':'final','answer':None},'exhausted':True}
        return {'pending_tool':{'name':name,'args':args,'call_key':call_key}}

    def route_after_guard(self,state:OncallState)->str:return 'tool' if state.get('pending_tool') else 'final'

    async def execute_tools(self,state:OncallState)->dict:
        p=state['pending_tool'];ctx=ToolExecutionContext(project_id=UUID(state['project_id']) if state.get('project_id') else None,incident_id=UUID(state['incident_id']) if state.get('incident_id') else None,agent_run_id=UUID(state['run_id']))
        self._emit('tool_started',{'tool_name':p['name'],'tool_args':p.get('args') or {}})
        try:
            result=await self.tools.execute(p['name'],p['args'],ctx)
        except Exception as exc:
            self._emit('tool_finished',{'tool_name':p['name'],'ok':False,'error':str(exc)[:500]})
            raise
        dumped=result.model_dump(mode='json')
        self._emit('tool_finished',{'tool_name':p['name'],'ok':bool(dumped.get('ok')),'summary':dumped.get('summary','')})
        return {'current_tool_result':dumped,'tool_calls_used':state.get('tool_calls_used',0)+1,'called_tools':[*state.get('called_tools',[]),p['call_key']]}

    async def record_observations(self,state:OncallState)->dict:
        p=state.get('pending_tool') or {};r=state.get('current_tool_result') or {};evidence=list(state.get('evidence',[]));refs=list(state.get('knowledge_refs',[]))
        if p.get('name')=='search_knowledge':
            if r.get('ok') and isinstance(r.get('data'),list):
                refs.extend([{'document_id':x.get('document_id',''),'version_id':x.get('version_id'),'chunk_id':x.get('id'),'title':x.get('title'),'page_range':x.get('page_range'),'score':x.get('rerank_score',x.get('rrf_score'))} for x in r['data'][:5]])
                if r['data']:
                    self._emit('rag_retrieved',{'count':len(r['data']),'top':[{'title':x.get('title',''),'score':x.get('rerank_score',x.get('rrf_score'))} for x in r['data'][:5]]})
        elif r:
            item=EvidenceItem(type='tool_observation',source_tool=p.get('name','unknown'),observed_at=datetime.fromisoformat(r['observed_at']) if isinstance(r.get('observed_at'),str) else datetime.now().astimezone(),summary=r.get('summary',''),data=r.get('data'),source_ref=r.get('source_ref')).model_dump(mode='json')
            evidence.append(item)
            if state.get('incident_id') and r.get('ok') and await self._incident_alive(state['incident_id']):
                try:
                    self.session.add(IncidentEvidence(incident_id=UUID(state['incident_id']),type='tool_observation',source=p.get('name','unknown'),summary=r.get('summary',''),data={'result':r.get('data') or {}},raw_ref=r.get('source_ref')));await self.session.commit()
                except Exception:
                    await self.session.rollback()
        return {'evidence':evidence,'knowledge_refs':refs,'pending_tool':None,'current_tool_result':None}

    async def _incident_alive(self,incident_id:str|None)->bool:
        if not incident_id:return False
        try:
            return await self.session.scalar(select(Incident.id).where(Incident.id==UUID(incident_id))) is not None
        except Exception:
            return False

    async def finalize(self,state:OncallState)->dict:
        d=state.get('decision',{});mode=state.get('mode','chat')
        if mode in ('investigate','deep'):
            diagnosis=d.get('diagnosis')
            if not diagnosis:
                summaries=[x.get('summary','') for x in state.get('evidence',[]) if x.get('summary')]
                diagnosis=DiagnosisReport(summary='调查在当前预算内收敛',severity=(state.get('incident_context') or {}).get('severity','warning'),affected_service=(state.get('incident_context') or {}).get('project_name'),symptoms=[(state.get('incident_context') or {}).get('summary','')],evidence=summaries[-8:],root_cause='当前证据不足以确认唯一根因。',confidence=0.2,remediation=['根据已收集 Evidence 继续人工排查','必要时在 Incident 页面触发深度调查'],verification=['确认触发指标越过恢复阈值并连续满足恢复次数'],risks=['不要在根因未确认时执行破坏性操作'],knowledge_refs=state.get('knowledge_refs',[]),unknowns=['唯一根因未确认']).model_dump(mode='json')
            report=DiagnosisReport.model_validate(diagnosis)
            text=self.render_diagnosis(report)
            self._emit('diagnosis_ready',{'severity':report.severity.value,'confidence':report.confidence,'root_cause':report.root_cause})
            return {'diagnosis':report.model_dump(mode='json'),'final_response':text}
        answer=d.get('answer')
        if not answer:
            answer=await self.model.stream_answer(self._context(state),on_token=lambda t:self._emit('token',{'content':t}))
        answer=answer or self.fallback_chat(state)
        return {'final_response':answer}

    @staticmethod
    def fallback_chat(state:OncallState)->str:
        ev='\n'.join(f"- {x.get('summary','')}" for x in state.get('evidence',[])[-5:])
        return '当前可用信息：\n'+(ev or '- 没有获得足够的工具/知识库证据。')

    @staticmethod
    def render_diagnosis(r:DiagnosisReport)->str:
        def bullets(xs):return '\n'.join(f'- {x}' for x in xs) if xs else '- 无'
        refs=[f"{x.title or x.document_id} (score={x.score:.3f})" if x.score is not None else (x.title or x.document_id) for x in r.knowledge_refs]
        service=r.affected_service or '未绑定服务名'
        return f"""## Incident 监测报告\n\n**级别**：{r.severity.value}  \n**影响服务**：{service}\n\n### 故障概况\n{r.summary}\n\n### 症状\n{bullets(r.symptoms)}\n\n### 关键证据\n{bullets(r.evidence)}\n\n### 根因判断\n{r.root_cause}\n\n**置信度**：{r.confidence:.0%}\n\n### 建议处理步骤\n{bullets(r.remediation)}\n\n### 风险\n{bullets(r.risks)}\n\n### 处理后验证\n{bullets(r.verification)}\n\n### 知识库依据\n{bullets(refs)}\n\n### 尚不确定\n{bullets(r.unknowns)}"""

    async def persist_result(self,state:OncallState)->dict:
        run=await self.session.get(AgentRun,UUID(state['run_id']))
        if run and run.status=='completed':return {}
        cs=ConversationService(self.session)
        try:
            await cs.add_message(UUID(state['conversation_id']),'assistant',state.get('final_response') or '',channel='agent',metadata={'agent_run_id':state['run_id'],'mode':state.get('mode')})
        except Exception:
            # The conversation may have been deleted while a durable investigation ran.
            await self.session.rollback()
        if state.get('diagnosis') and state.get('incident_id') and await self._incident_alive(state['incident_id']):
            rep=DiagnosisReport.model_validate(state['diagnosis'])
            try:
                self.session.add(Diagnosis(incident_id=UUID(state['incident_id']),agent_run_id=UUID(state['run_id']),structured_json=rep.model_dump(mode='json'),confidence=rep.confidence))
                self.session.add(Notification(incident_id=UUID(state['incident_id']),channel='feishu',target='default',payload={'kind':'diagnosis','incident_id':state['incident_id'],'severity':rep.severity.value,'text':state.get('final_response') or ''},status='pending',dedupe_key=f"incident:{state['incident_id']}:diagnosis:{state['run_id']}"))
                inc=await self.session.get(Incident,UUID(state['incident_id']))
                if inc:inc.status='diagnosed';inc.last_investigated_at=datetime.now().astimezone()
                await self.session.commit()
            except Exception:
                await self.session.rollback()
        if run:
            try:
                run.usage={
                    **(run.usage or {}),
                    'intent':state.get('intent'),
                    'route_confidence':state.get('route_confidence'),
                    'route_reason':state.get('route_reason'),
                    'knowledge_status':state.get('knowledge_status'),
                    'knowledge_refs':state.get('knowledge_refs',[])[:5],
                    'answer_sources':state.get('answer_sources',[]),
                }
                run.status='completed';run.finished_at=datetime.now().astimezone();await self.session.commit()
            except Exception:
                await self.session.rollback()
        return {}
