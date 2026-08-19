"""Target-machine end-to-end smoke test.

Requires: API + monitor/agent/rag workers, PostgreSQL/Milvus, Python dependencies.
Runs safely in ONCALL_ENV=development and uses only read-only Agent tools.
"""
from __future__ import annotations

import argparse
import os
import time
import uuid
import httpx


def expect(resp:httpx.Response,code:int=200):
    if resp.status_code!=code:
        raise RuntimeError(f'{resp.request.method} {resp.request.url} -> {resp.status_code}: {resp.text[:1000]}')
    return resp


def stream_final(client:httpx.Client,path:str,content:str,timeout:float)->str:
    event=None;final=''
    with client.stream('POST',path,json={'content':content,'channel':'web'},timeout=timeout) as r:
        expect(r)
        for line in r.iter_lines():
            if not line:continue
            if line.startswith('event:'):event=line.split(':',1)[1].strip()
            elif line.startswith('data:'):
                data=__import__('json').loads(line.split(':',1)[1].strip())
                if event=='error':raise RuntimeError(data.get('message','stream error'))
                if event=='final':final=data.get('content','')
    if not final:raise RuntimeError('chat stream returned no final event')
    return final


def main()->int:
    ap=argparse.ArgumentParser();ap.add_argument('--base-url',default='http://127.0.0.1:9900');ap.add_argument('--timeout',type=float,default=120)
    args=ap.parse_args();base=args.base_url.rstrip('/')
    username=os.getenv('ONCALL_ADMIN_USERNAME','admin');password=os.getenv('ONCALL_ADMIN_PASSWORD','change-me-now')
    client=httpx.Client(base_url=base,timeout=httpx.Timeout(min(args.timeout, 30)),follow_redirects=True)
    project_id=None
    try:
        print('[1/10] health')
        h=expect(client.get('/api/health')).json();assert h.get('database'),h
        print('[2/10] login')
        expect(client.post('/api/auth/login',json={'username':username,'password':password}))
        ready=expect(client.get('/api/settings/readiness')).json();print(' readiness:',ready)
        assert ready['environment']=='development','dev incident trigger is disabled outside development'

        print('[3/10] create project')
        suffix=uuid.uuid4().hex[:8]
        project={
            'name':f'Oncall Smoke {suffix}','description':'automated local E2E smoke','poll_interval':300,
            'process_targets':[],'log_sources':[],'docker_targets':[],'database_profiles':[],'service_endpoints':[],
            'rules':[{'metric_key':'host.cpu.percent','resource_key':'default','operator':'>','trigger_threshold':99,'trigger_for':2,'recovery_threshold':90,'recovery_for':2,'severity':'warning','enabled':True}],
        }
        project_id=expect(client.post('/api/projects',json=project)).json()['id']
        snap=expect(client.post(f'/api/projects/{project_id}/test')).json();assert 'host.cpu.percent' in snap.get('signals',{}),snap

        print('[4/10] create/search/rename conversation')
        conv=expect(client.post('/api/conversations',json={'title':f'Smoke Chat {suffix}','project_id':project_id})).json();cid=conv['id']
        final=stream_final(client,f'/api/conversations/{cid}/messages:stream','请检查当前 CPU，并说明你使用的是实时工具数据还是知识库。',args.timeout)
        assert final.strip()
        expect(client.patch(f'/api/conversations/{cid}',json={'title':f'Smoke Renamed {suffix}'}))
        rows=expect(client.get('/api/conversations',params={'q':suffix})).json();assert any(x['id']==cid for x in rows)
        msgs=expect(client.get(f'/api/conversations/{cid}/messages')).json();assert len(msgs)>=2

        print('[5/10] synthetic incident -> durable job')
        trig=expect(client.post('/api/dev/incidents/trigger',json={'project_id':project_id,'metric_key':'host.cpu.percent','value':100})).json();iid=trig['incident_id'];icid=trig['conversation_id'];assert icid

        print('[6/10] wait agent-worker diagnosis')
        deadline=time.time()+args.timeout;detail=None
        while time.time()<deadline:
            detail=expect(client.get(f'/api/incidents/{iid}')).json()
            if detail.get('diagnosis'):break
            time.sleep(1)
        if not detail or not detail.get('diagnosis'):
            raise RuntimeError('Incident was created but no diagnosis appeared. Verify oncall-agent-worker is running.')

        print('[7/10] trace')
        trace=expect(client.get(f'/api/incidents/{iid}/trace')).json();assert trace.get('agent_runs'),trace
        assert any(r.get('tools') for r in trace['agent_runs']),'AgentRun exists but no ToolRun was persisted'

        print('[8/10] incident follow-up')
        follow=stream_final(client,f'/api/conversations/{icid}/messages:stream','这是刚才的告警。现在 CPU 情况如何？请基于实时工具复查。',args.timeout)
        assert follow.strip()

        print('[9/10] persistence-visible API state')
        old=expect(client.get(f'/api/conversations/{icid}/messages')).json();assert len(old)>=3
        expect(client.post(f'/api/dev/incidents/{iid}/recover'))
        resolved=expect(client.get(f'/api/incidents/{iid}')).json();assert resolved['status']=='resolved'

        print('[10/10] cleanup')
        expect(client.delete(f'/api/projects/{project_id}'));project_id=None
        print('E2E SMOKE: PASS')
        return 0
    finally:
        if project_id:
            try:client.delete(f'/api/projects/{project_id}')
            except Exception:pass
        client.close()


if __name__=='__main__':raise SystemExit(main())
