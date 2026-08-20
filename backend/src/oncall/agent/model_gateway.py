from __future__ import annotations

import json
import re
from typing import Any
import httpx
from pydantic import ValidationError
from oncall.agent.prompts import DECISION_SCHEMA, STREAM_ANSWER_PROMPT, SYSTEM_PROMPT
from oncall.bootstrap.config import get_settings
from oncall.domain.schemas import AgentDecision, DiagnosisReport


class ModelProvider:
    async def decide(self,context:dict[str,Any])->AgentDecision:raise NotImplementedError
    async def summarize(self,text:str)->str:return text[:6000]

    async def stream_answer(self,context:dict[str,Any],on_token=None)->str:
        """Generate the final prose answer. Streaming providers override this; the
        deterministic fallback reuses decide() so Mock keeps working without tokens."""
        d=await self.decide(context)
        ans=d.answer or ''
        if on_token and ans:on_token(ans)
        return ans


class MockProvider(ModelProvider):
    """Deterministic development provider. It proves graph/tool/persistence wiring without external API keys."""
    async def decide(self,context:dict[str,Any])->AgentDecision:
        mode=context.get('mode','chat');called_keys=set(context.get('called_tools',[]));called={x.split(':',1)[0] for x in called_keys};msg=(context.get('user_message') or '').lower();incident=context.get('incident_context') or {}
        if mode=='chat':
            if context.get('intent') == 'casual_chat':
                return AgentDecision(action='final',answer='你好，我是 Oncall AI SRE，可以协助回答运维问题、检索知识库，并在绑定项目后查询实时运行状态。')
            realtime=any(k in msg for k in ['cpu','内存','memory','磁盘','进程','日志','docker','数据库','postgres','健康','health','现在','当前'])
            if realtime and context.get('project_id') and 'query_host_metrics' not in called:
                if any(k in msg for k in ['日志','error','异常日志']):name='query_logs';args={'keyword':'','level':'error','limit':50}
                elif any(k in msg for k in ['进程','chromium','python']):name='query_processes';args={'limit':30}
                elif any(k in msg for k in ['数据库','postgres']):name='query_database';args={}
                elif any(k in msg for k in ['docker','容器']):name='query_containers';args={}
                elif any(k in msg for k in ['health','健康','接口']):name='query_service_health';args={}
                else:name='query_host_metrics';args={}
                return AgentDecision(action='tool',rationale='需要真实运行状态',tool_name=name,tool_args=args)
            if 'search_knowledge' not in called:
                return AgentDecision(action='tool',rationale='先检索运维知识库',tool_name='search_knowledge',tool_args={'query':context.get('user_message','')})
            ev=context.get('evidence',[]);summary='；'.join(x.get('summary','') for x in ev[-3:]) or '知识库暂未提供可用内容'
            if context.get('project_id'):
                answer=f'基于当前可用信息：{summary}\n\n如果你希望我继续检查该项目的实时状态，我可以调用监控工具复查。'
            else:
                answer=f'基于当前可用信息：{summary}\n\n当前为通用运维问答模式。'
            return AgentDecision(action='final',answer=answer)
        if mode=='follow_up':
            realtime=any(k in msg for k in ['现在','当前','cpu','内存','memory','进程','日志','docker','数据库','health','健康','恢复'])
            if realtime and context.get('project_id'):
                if any(k in msg for k in ['日志','error']): name='query_logs'; args={'level':'error','limit':60}
                elif any(k in msg for k in ['进程','chromium','python']): name='query_processes'; args={'limit':30}
                elif any(k in msg for k in ['数据库','postgres']): name='query_database'; args={}
                elif any(k in msg for k in ['docker','容器']): name='query_containers'; args={}
                elif any(k in msg for k in ['health','健康','接口','恢复']): name='query_service_health'; args={}
                else: name='query_host_metrics'; args={}
                if name not in called:return AgentDecision(action='tool',rationale='追问涉及实时状态，需要复查',tool_name=name,tool_args=args)
            prev=context.get('incident_context') or {};ev=context.get('evidence',[])
            summary='；'.join(x.get('summary','') for x in ev[-4:])
            return AgentDecision(action='final',answer=f"这是同一个 Incident 的后续追问。当前事故：{prev.get('summary','')}。已掌握证据：{summary or '暂无新增实时证据'}。如需确认当前状态，我可以继续调用只读工具复查。")
        # investigation/deep
        sequence=['query_host_metrics','query_processes','query_logs','query_database','query_containers','query_service_health','search_knowledge']
        anomaly=str(incident.get('anomaly_type',''))
        if 'db.' in anomaly:sequence=['query_database','query_logs','query_host_metrics','search_knowledge']
        elif 'service.' in anomaly:sequence=['query_service_health','query_logs','query_processes','search_knowledge']
        elif 'container.' in anomaly:sequence=['query_containers','query_logs','query_host_metrics','search_knowledge']
        elif 'log.' in anomaly:sequence=['query_logs','query_processes','query_host_metrics','search_knowledge']
        for name in sequence:
            if name not in called:
                args={'query':f"{anomaly} 运维处理方案"} if name=='search_knowledge' else {'level':'error','limit':80} if name=='query_logs' else {'limit':30} if name=='query_processes' else {}
                return AgentDecision(action='tool',rationale=f'收集 {name} 证据',tool_name=name,tool_args=args)
        evidence=context.get('evidence',[]);summaries=[x.get('summary','') for x in evidence if x.get('summary')]
        report=DiagnosisReport(summary=f"检测到 {anomaly or '运行异常'}",severity=incident.get('severity','warning'),affected_service=incident.get('project_name'),symptoms=[incident.get('summary','异常触发')],evidence=summaries[-8:],root_cause='当前证据显示存在运行异常；Mock 模型不会虚构更具体根因，配置真实 LLM 后将基于 Evidence 进行因果判断。',confidence=0.55 if summaries else 0.2,remediation=['按照报告中的 Evidence 逐项确认异常资源','参考知识库命中的 SOP 进行人工处置','V1 不自动执行有副作用操作'],verification=['重新检查触发指标已越过 recovery threshold','确认服务健康检查恢复并持续两个监测周期正常'],risks=['执行任何重启/终止进程前先确认业务任务状态'],knowledge_refs=context.get('knowledge_refs',[]),unknowns=[] if summaries else ['缺少有效工具证据'])
        return AgentDecision(action='final',diagnosis=report)


class OpenAICompatibleProvider(ModelProvider):
    def __init__(self):self.s=get_settings()

    async def _chat(self,messages:list[dict[str,str]],temperature:float=0.1)->str:
        last=None
        for attempt in range(2):
            try:
                async with httpx.AsyncClient(timeout=90) as client:
                    r=await client.post(
                        f"{self.s.model_base_url.rstrip('/')}/chat/completions",
                        headers={'Authorization':f'Bearer {self.s.model_api_key}'},
                        json={'model':self.s.model_name,'temperature':temperature,'messages':messages},
                    )
                    if r.status_code==429 or r.status_code>=500:
                        last=RuntimeError(f'LLM HTTP {r.status_code}: {r.text[:500]}')
                        if attempt==0:
                            await __import__('asyncio').sleep(1.0);continue
                    r.raise_for_status();body=r.json()
                    return str(body['choices'][0]['message']['content'])
            except (httpx.TimeoutException,httpx.NetworkError) as exc:
                last=exc
                if attempt==0:
                    await __import__('asyncio').sleep(1.0);continue
                raise
        raise RuntimeError(f'LLM request failed: {last}')

    @staticmethod
    def _json_candidate(content:str)->str:
        text=re.sub(r'^```(?:json)?\s*|\s*```$','',content.strip(),flags=re.I|re.S).strip()
        if text.startswith('{') and text.endswith('}'):return text
        start=text.find('{');end=text.rfind('}')
        return text[start:end+1] if start>=0 and end>start else text

    async def decide(self,context:dict[str,Any])->AgentDecision:
        payload_context=json.dumps(context,ensure_ascii=False,default=str)
        messages=[{'role':'system','content':SYSTEM_PROMPT+'\n'+DECISION_SCHEMA},{'role':'user','content':payload_context}]
        content=await self._chat(messages,0.1);candidate=self._json_candidate(content)
        try:return AgentDecision.model_validate_json(candidate)
        except ValidationError as first_error:
            repair=[
                {'role':'system','content':'把下面内容修复为严格符合指定 AgentDecision schema 的单个 JSON 对象。不得增加事实，不要 Markdown。\n'+DECISION_SCHEMA},
                {'role':'user','content':f'Validation error: {first_error}\nOriginal output:\n{content[:12000]}'},
            ]
            fixed=self._json_candidate(await self._chat(repair,0.0))
            try:return AgentDecision.model_validate_json(fixed)
            except ValidationError as second_error:
                raise RuntimeError(f'LLM returned invalid AgentDecision after one repair: {second_error}; content={fixed[:1000]}') from second_error

    async def summarize(self,text:str)->str:
        messages=[
            {'role':'system','content':'你是 Oncall 的会话记忆压缩器。只保留可验证事实、用户目标、已执行检查、结论和未决事项；不要编造。'},
            {'role':'user','content':text[:50000]},
        ]
        return (await self._chat(messages,0.0))[:12000]

    async def stream_answer(self,context:dict[str,Any],on_token=None)->str:
        """Stream the final prose answer token-by-token via SSE (stream=True)."""
        payload_context=json.dumps(context,ensure_ascii=False,default=str)
        messages=[{'role':'system','content':STREAM_ANSWER_PROMPT},{'role':'user','content':payload_context}]
        parts:list[str]=[]
        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream('POST',f"{self.s.model_base_url.rstrip('/')}/chat/completions",
                headers={'Authorization':f'Bearer {self.s.model_api_key}'},
                json={'model':self.s.model_name,'temperature':0.2,'stream':True,'messages':messages}) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line or not line.startswith('data:'):
                        continue
                    payload=line[5:].strip()
                    if payload=='[DONE]':
                        break
                    try:
                        obj=json.loads(payload)
                        delta=obj.get('choices',[{}])[0].get('delta',{}).get('content')
                    except Exception:
                        continue
                    if delta:
                        parts.append(delta)
                        if on_token:on_token(delta)
        return ''.join(parts).strip()


def get_model_provider()->ModelProvider:
    s=get_settings()
    return MockProvider() if s.model_provider=='mock' or not s.model_api_key else OpenAICompatibleProvider()
