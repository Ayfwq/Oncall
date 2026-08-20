from __future__ import annotations

from contextlib import asynccontextmanager
import json
import uvicorn
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from oncall.api.deps import current_user
from oncall.application.agent_service import AgentService
from oncall.application.auth_service import AuthService
from oncall.application.conversation_service import ConversationService
from oncall.application.dtos import ChatMessageDTO, ConversationCreateDTO, ConversationPatchDTO, FeishuSettingsDTO, PasswordChangeDTO, ProjectCreateDTO
from oncall.application.knowledge_service import KnowledgeService
from oncall.application.project_service import ProjectService
from oncall.bootstrap.config import get_settings, update_env_values
from oncall.bootstrap.logging import configure_logging
from oncall.infrastructure.db.models import (AgentRun, BackgroundJob, Conversation, Diagnosis, Incident, IncidentEvidence, KnowledgeDocument, KnowledgeDocumentVersion, MetricSample, MonitoringRule, MonitoringRun, Notification, Project, RetrievalTrace, ToolRun)
from oncall.infrastructure.db.session import SessionFactory, get_session

s=get_settings();configure_logging(s.log_level)

def _uuid(value, what='id'):
    """Parse a path/query UUID; malformed ids are 404, matching the routes' semantics."""
    from uuid import UUID
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(404, f'{what} not found')

@asynccontextmanager
async def lifespan(app:FastAPI):
    import os
    if s.langgraph_strict_msgpack:os.environ.setdefault('LANGGRAPH_STRICT_MSGPACK','true')
    app.state.checkpointer=None
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        cm=AsyncPostgresSaver.from_conn_string(s.langgraph_database_url); cp=await cm.__aenter__();await cp.setup();app.state.checkpointer=cp;app.state.checkpointer_cm=cm
    except Exception as e:
        app.state.checkpointer_error=str(e)
    async with SessionFactory() as db: await AuthService(db).ensure_admin()
    app.state.feishu_ws_thread=None
    if s.feishu_enabled:
        try:
            import asyncio
            from oncall.channels.feishu import start_ws_listener
            from oncall.channels.feishu_gateway import FeishuGateway, build_lark_callback
            loop=asyncio.get_running_loop()
            gateway=FeishuGateway(app.state.checkpointer)
            app.state.feishu_ws_thread=start_ws_listener(build_lark_callback(loop,gateway))
        except Exception as e:
            app.state.feishu_ws_error=str(e)
    yield
    if getattr(app.state,'checkpointer_cm',None):await app.state.checkpointer_cm.__aexit__(None,None,None)

app=FastAPI(title='Oncall AI SRE',version='1.0.0',lifespan=lifespan)
app.add_middleware(CORSMiddleware,allow_origins=[s.web_origin],allow_credentials=True,allow_methods=['*'],allow_headers=['*'])

@app.middleware('http')
async def request_id_middleware(request:Request,call_next):
    from uuid import uuid4
    request_id=request.headers.get('x-request-id') or str(uuid4())
    request.state.request_id=request_id
    response=await call_next(request)
    response.headers['x-request-id']=request_id
    return response

@app.get('/api/health')
async def health(request:Request):
    db_ok=False;db_error=None
    try:
        async with SessionFactory() as db:
            await db.execute(text('select 1'));db_ok=True
    except Exception as exc:db_error=str(exc)
    return {'ok':db_ok,'database':db_ok,'database_error':db_error,'checkpointer':bool(request.app.state.checkpointer),'checkpointer_error':getattr(request.app.state,'checkpointer_error',None),'feishu_ws_error':getattr(request.app.state,'feishu_ws_error',None)}

@app.post('/api/auth/login')
async def login(payload:dict,response:Response,db:AsyncSession=Depends(get_session)):
    result=await AuthService(db).login(payload.get('username',''),payload.get('password',''))
    if not result:raise HTTPException(401,'invalid credentials')
    user,token=result;response.set_cookie('oncall_session',token,httponly=True,samesite='strict',secure=s.env.lower()=='production',max_age=s.session_days*86400);return {'id':str(user.id),'username':user.username}

@app.post('/api/auth/logout')
async def logout(response:Response,request:Request,db:AsyncSession=Depends(get_session)):
    await AuthService(db).logout(request.cookies.get('oncall_session'));response.delete_cookie('oncall_session');return {'ok':True}

@app.get('/api/auth/me')
async def me(user=Depends(current_user)):return {'id':str(user.id),'username':user.username}

@app.post('/api/auth/password')
async def change_password(dto:PasswordChangeDTO,request:Request,user=Depends(current_user),db:AsyncSession=Depends(get_session)):
    token=request.cookies.get('oncall_session')
    ok=await AuthService(db).change_password(user,dto.current_password,dto.new_password,token)
    if not ok:raise HTTPException(400,'current password is incorrect')
    return {'ok':True,'message':'password changed; other sessions were signed out'}

@app.get('/api/conversations')
async def conversations(q:str|None=Query(default=None,max_length=200),include_archived:bool=False,user=Depends(current_user),db:AsyncSession=Depends(get_session)):
    rows=await ConversationService(db).list(user.id,include_archived=include_archived,query=q);return [{'id':str(x.id),'title':x.title,'type':x.type,'project_id':str(x.project_id) if x.project_id else None,'incident_id':str(x.incident_id) if x.incident_id else None,'updated_at':x.updated_at} for x in rows]

@app.post('/api/conversations')
async def create_conversation(dto:ConversationCreateDTO,user=Depends(current_user),db:AsyncSession=Depends(get_session)):
    c=await ConversationService(db).create(user.id,dto.title,dto.project_id);return {'id':str(c.id),'title':c.title}

@app.patch('/api/conversations/{cid}')
async def patch_conversation(cid:str,dto:ConversationPatchDTO,user=Depends(current_user),db:AsyncSession=Depends(get_session)):
    c=await ConversationService(db).patch(_uuid(cid),user.id,dto.title,dto.archived)
    if not c:raise HTTPException(404,'not found')
    return {'id':str(c.id),'title':c.title,'archived':c.archived}

@app.delete('/api/conversations/{cid}')
async def delete_conversation(cid:str,user=Depends(current_user),db:AsyncSession=Depends(get_session)):
    if not await ConversationService(db).delete(_uuid(cid),user.id):raise HTTPException(404,'not found')
    return {'ok':True}

@app.get('/api/conversations/{cid}/messages')
async def list_messages(cid:str,user=Depends(current_user),db:AsyncSession=Depends(get_session)):
    cid_u=_uuid(cid)
    if not await ConversationService(db).get(cid_u,user.id):raise HTTPException(404,'not found')
    rows=await ConversationService(db).messages(cid_u);return [{'id':str(x.id),'role':x.role,'content':x.content,'channel':x.channel,'created_at':x.created_at,'metadata':x.metadata_json} for x in rows]

@app.post('/api/conversations/{cid}/messages:stream')
async def chat(cid:str,dto:ChatMessageDTO,request:Request,user=Depends(current_user),db:AsyncSession=Depends(get_session)):
    cid_u=_uuid(cid)
    conv=await ConversationService(db).get(cid_u,user.id)
    if not conv:raise HTTPException(404,'not found')
    checkpointer=getattr(request.app.state,'checkpointer',None)
    async def gen():
        import asyncio
        queue:asyncio.Queue=asyncio.Queue()
        def emit(event_type:str,data:dict)->None:
            queue.put_nowait((event_type,data))
        async def work():
            try:
                # Streaming responses can outlive request-scoped dependencies. Give the
                # Agent its own DB session so persistence remains valid until the stream ends.
                async with SessionFactory() as agent_db:
                    state=await AgentService(agent_db,checkpointer).run(cid_u,dto.content,dto.channel,emit=emit)
                text=(state or {}).get('final_response','')
                queue.put_nowait(('final',{'content':text}))
            except Exception as e:
                queue.put_nowait(('error',{'message':str(e)}))
        task=asyncio.create_task(work())
        yield 'event: status\ndata: '+json.dumps({'stage':'reasoning'},ensure_ascii=False)+'\n\n'
        try:
            while True:
                event_type,data=await queue.get()
                yield f'event: {event_type}\ndata: '+json.dumps(data,ensure_ascii=False)+'\n\n'
                if event_type in ('final','error'):
                    break
        finally:
            if not task.done():task.cancel()
    return StreamingResponse(gen(),media_type='text/event-stream',headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})

@app.get('/api/projects')
async def list_projects(user=Depends(current_user),db:AsyncSession=Depends(get_session)):
    rows=await ProjectService(db).list(user.id);return [{'id':str(x.id),'name':x.name,'description':x.description,'enabled':x.enabled,'poll_interval':x.poll_interval,'updated_at':x.updated_at} for x in rows]

@app.post('/api/projects')
async def create_project(dto:ProjectCreateDTO,user=Depends(current_user),db:AsyncSession=Depends(get_session)):
    p=await ProjectService(db).create(user.id,dto);return {'id':str(p.id),'name':p.name}

@app.put('/api/projects/{pid}')
async def update_project(pid:str,dto:ProjectCreateDTO,user=Depends(current_user),db:AsyncSession=Depends(get_session)):
    p=await ProjectService(db).update(_uuid(pid),user.id,dto)
    if not p:raise HTTPException(404,'not found')
    return {'id':str(p.id),'name':p.name}



def _project_runtime_payload(cfg):
    data=cfg.model_dump(mode='json')
    for dbp in data.get('database_profiles',[]):
        if dbp.get('password'): dbp['password']=None
    return data

@app.get('/api/projects/{pid}')
async def project_detail(pid:str,user=Depends(current_user),db:AsyncSession=Depends(get_session)):
    project=await ProjectService(db).get(_uuid(pid),user.id)
    if not project:raise HTTPException(404,'not found')
    cfg=await ProjectService(db).runtime_config(project.id)
    return _project_runtime_payload(cfg)

@app.delete('/api/projects/{pid}')
async def delete_project(pid:str,user=Depends(current_user),db:AsyncSession=Depends(get_session)):
    if not await ProjectService(db).delete(_uuid(pid),user.id):raise HTTPException(404,'not found')
    return {'ok':True}

@app.post('/api/projects/{pid}/test')
async def test_project(pid:str,user=Depends(current_user),db:AsyncSession=Depends(get_session)):
    from oncall.monitoring.engine import MonitoringEngine
    project=await ProjectService(db).get(_uuid(pid),user.id)
    if not project:raise HTTPException(404,'not found')
    snap=await MonitoringEngine(db).collect(project.id,persist_state=False)
    return snap.model_dump(mode='json')

@app.get('/api/projects/{pid}/snapshot')
async def latest_project_snapshot(pid:str,user=Depends(current_user),db:AsyncSession=Depends(get_session)):
    project=await ProjectService(db).get(_uuid(pid),user.id)
    if not project:raise HTTPException(404,'not found')
    run=await db.scalar(select(MonitoringRun).where(MonitoringRun.project_id==project.id,MonitoringRun.status=='completed').order_by(MonitoringRun.started_at.desc()).limit(1))
    return {'snapshot':run.snapshot if run else None,'collector_status':run.collector_status if run else None,'observed_at':run.finished_at if run else None}

@app.get('/api/incidents')
async def list_incidents(user=Depends(current_user),db:AsyncSession=Depends(get_session)):
    rows=list((await db.scalars(select(Incident).join(Project,Project.id==Incident.project_id).where(Project.user_id==user.id).order_by(Incident.last_seen.desc()).limit(200))).all());return [{'id':str(x.id),'project_id':str(x.project_id),'status':x.status,'severity':x.severity,'summary':x.summary,'anomaly_type':x.anomaly_type,'resource_key':x.resource_key,'first_seen':x.first_seen,'last_seen':x.last_seen,'resolved_at':x.resolved_at} for x in rows]

@app.get('/api/incidents/{iid}')
async def incident_detail(iid:str,user=Depends(current_user),db:AsyncSession=Depends(get_session)):
    x=await db.scalar(select(Incident).join(Project,Project.id==Incident.project_id).where(Incident.id==_uuid(iid),Project.user_id==user.id))
    if not x:raise HTTPException(404,'not found')
    d=await db.scalar(select(Diagnosis).where(Diagnosis.incident_id==x.id).order_by(Diagnosis.created_at.desc()).limit(1))
    evidence=list((await db.scalars(select(IncidentEvidence).where(IncidentEvidence.incident_id==x.id).order_by(IncidentEvidence.observed_at.asc()).limit(200))).all())
    conv=await db.scalar(select(Conversation).where(Conversation.incident_id==x.id).order_by(Conversation.created_at.asc()).limit(1))
    return {'id':str(x.id),'project_id':str(x.project_id),'status':x.status,'severity':x.severity,'summary':x.summary,'anomaly_type':x.anomaly_type,'resource_key':x.resource_key,'first_seen':x.first_seen,'last_seen':x.last_seen,'resolved_at':x.resolved_at,'conversation_id':str(conv.id) if conv else None,'diagnosis':d.structured_json if d else None,'evidence':[{'id':str(e.id),'type':e.type,'source':e.source,'observed_at':e.observed_at,'summary':e.summary,'data':e.data,'raw_ref':e.raw_ref} for e in evidence]}



@app.post('/api/incidents/{iid}/investigate')
async def reinvestigate(iid:str,user=Depends(current_user),db:AsyncSession=Depends(get_session)):
    from uuid import uuid4
    inc=await db.scalar(select(Incident).join(Project,Project.id==Incident.project_id).where(Incident.id==_uuid(iid),Project.user_id==user.id))
    if not inc:raise HTTPException(404,'not found')
    conv=await db.scalar(select(Conversation).where(Conversation.incident_id==inc.id).order_by(Conversation.created_at.asc()).limit(1))
    if not conv:
        conv=await ConversationService(db).create(user.id,title=f'🚨 {inc.anomaly_type}',project_id=inc.project_id,incident_id=inc.id,type_='incident')
    from oncall.jobs.queue import JobQueue
    job=await JobQueue(db).enqueue('incident_investigate',{'incident_id':str(inc.id),'conversation_id':str(conv.id)},idempotency_key=f'incident_investigate:{inc.id}:manual:{uuid4()}',priority=10)
    return {'job_id':str(job.id),'conversation_id':str(conv.id)}

@app.post('/api/incidents/{iid}/resolve')
async def manual_resolve(iid:str,user=Depends(current_user),db:AsyncSession=Depends(get_session)):
    from oncall.application.incident_service import IncidentService
    inc=await db.scalar(select(Incident).join(Project,Project.id==Incident.project_id).where(Incident.id==_uuid(iid),Project.user_id==user.id))
    if not inc:raise HTTPException(404,'not found')
    await IncidentService(db).resolve(inc.id,'manual_resolve')
    return {'ok':True,'status':'resolved'}

@app.post('/api/incidents/{iid}/conversation')
async def incident_conversation(iid:str,user=Depends(current_user),db:AsyncSession=Depends(get_session)):
    inc=await db.scalar(select(Incident).join(Project,Project.id==Incident.project_id).where(Incident.id==_uuid(iid),Project.user_id==user.id))
    if not inc:raise HTTPException(404,'not found')
    conv=await db.scalar(select(Conversation).where(Conversation.incident_id==inc.id).order_by(Conversation.created_at.asc()).limit(1))
    if not conv:conv=await ConversationService(db).create(user.id,title=f'🚨 {inc.anomaly_type}',project_id=inc.project_id,incident_id=inc.id,type_='incident')
    return {'conversation_id':str(conv.id)}

@app.get('/api/monitoring/metrics')
async def metrics(project_id:str,metric_key:str,user=Depends(current_user),db:AsyncSession=Depends(get_session)):
    pid=_uuid(project_id,'project_id')
    if not await ProjectService(db).get(pid,user.id):raise HTTPException(404,'project not found')
    rows=list((await db.scalars(select(MetricSample).where(MetricSample.project_id==pid,MetricSample.metric_key==metric_key).order_by(MetricSample.ts.desc()).limit(500))).all());rows.reverse();return [{'ts':x.ts,'value':x.value} for x in rows]

@app.get('/api/knowledge/documents')
async def documents(user=Depends(current_user),db:AsyncSession=Depends(get_session)):
    rows=await KnowledgeService(db).list_documents(user.id);return [{'id':str(x.id),'title':x.title,'status':x.status,'project_scope':str(x.project_scope) if x.project_scope else None,'updated_at':x.updated_at} for x in rows]

@app.post('/api/knowledge/documents')
async def upload_document(file:UploadFile=File(...),project_scope:str|None=Form(default=None),user=Depends(current_user),db:AsyncSession=Depends(get_session)):
    from pathlib import Path
    from uuid import UUID
    import tempfile
    try:
        scope=UUID(project_scope) if project_scope else None
    except (ValueError, TypeError):
        raise HTTPException(400,'invalid project_scope')
    if scope and not await ProjectService(db).get(scope,user.id):raise HTTPException(404,'project scope not found')
    filename=Path(file.filename or 'upload.bin').name
    limit=s.knowledge_max_upload_mb*1024*1024
    with tempfile.TemporaryDirectory(prefix='oncall-upload-') as td:
        path=Path(td)/filename;size=0
        with path.open('wb') as out:
            while True:
                chunk=await file.read(1024*1024)
                if not chunk:break
                size+=len(chunk)
                if size>limit:raise HTTPException(413,f'file exceeds {s.knowledge_max_upload_mb} MiB limit')
                out.write(chunk)
        try:
            ver,job=await KnowledgeService(db).upload(user.id,path,filename,scope)
        except ValueError as e:
            raise HTTPException(400,str(e))
    return {'version_id':str(ver.id),'job_id':str(job.id),'status':ver.status}



@app.get('/api/knowledge/jobs/{jid}')
async def knowledge_job(jid:str,user=Depends(current_user),db:AsyncSession=Depends(get_session)):
    from uuid import UUID
    job=await db.get(BackgroundJob,_uuid(jid))
    if not job or job.type not in ('rag_ingest','knowledge_reindex'):raise HTTPException(404,'not found')
    try:version_id=UUID(str(job.payload.get('version_id')))
    except (TypeError,ValueError):raise HTTPException(404,'not found')
    ver=await db.scalar(select(KnowledgeDocumentVersion).join(KnowledgeDocument,KnowledgeDocument.id==KnowledgeDocumentVersion.document_id).where(KnowledgeDocumentVersion.id==version_id,KnowledgeDocument.user_id==user.id))
    if not ver:raise HTTPException(404,'not found')
    return {'id':str(job.id),'type':job.type,'status':job.status,'attempts':job.attempts,'last_error':job.last_error,'updated_at':job.updated_at}

@app.post('/api/knowledge/documents/{did}/reindex')
async def reindex_document(did:str,user=Depends(current_user),db:AsyncSession=Depends(get_session)):
    from uuid import uuid4
    from oncall.jobs.queue import JobQueue
    doc=await db.scalar(select(KnowledgeDocument).where(KnowledgeDocument.id==_uuid(did),KnowledgeDocument.user_id==user.id))
    if not doc or not doc.active_version_id:raise HTTPException(404,'document/version not found')
    job=await JobQueue(db).enqueue('knowledge_reindex',{'version_id':str(doc.active_version_id)},idempotency_key=f'reindex:{doc.active_version_id}:{uuid4()}',priority=40)
    return {'job_id':str(job.id)}

@app.delete('/api/knowledge/documents/{did}')
async def delete_document(did:str,user=Depends(current_user),db:AsyncSession=Depends(get_session)):
    import shutil
    from oncall.rag.milvus_store import MilvusKnowledgeIndex
    doc=await db.scalar(select(KnowledgeDocument).where(KnowledgeDocument.id==_uuid(did),KnowledgeDocument.user_id==user.id))
    if not doc:raise HTTPException(404,'not found')
    versions=list((await db.scalars(select(KnowledgeDocumentVersion).where(KnowledgeDocumentVersion.document_id==doc.id))).all())
    index=MilvusKnowledgeIndex()
    for ver in versions:
        try:await index.delete_version(str(ver.id))
        except Exception:pass
    roots={__import__('pathlib').Path(v.raw_path).parent.parent for v in versions if v.raw_path}
    await db.delete(doc);await db.commit()
    for root in roots:shutil.rmtree(root,ignore_errors=True)
    return {'ok':True}


@app.get('/api/incidents/{iid}/trace')
async def incident_trace(iid:str,user=Depends(current_user),db:AsyncSession=Depends(get_session)):
    inc=await db.scalar(select(Incident).join(Project,Project.id==Incident.project_id).where(Incident.id==_uuid(iid),Project.user_id==user.id))
    if not inc:raise HTTPException(404,'not found')
    runs=list((await db.scalars(select(AgentRun).where(AgentRun.incident_id==inc.id).order_by(AgentRun.started_at.desc()).limit(50))).all())
    out=[]
    for run in runs:
        tools=list((await db.scalars(select(ToolRun).where(ToolRun.agent_run_id==run.id).order_by(ToolRun.created_at.asc()))).all())
        retrievals=list((await db.scalars(select(RetrievalTrace).where(RetrievalTrace.agent_run_id==run.id).order_by(RetrievalTrace.created_at.asc()))).all())
        out.append({
            'id':str(run.id),'mode':run.mode,'status':run.status,'started_at':run.started_at,'finished_at':run.finished_at,
            'tools':[{'tool_name':x.tool_name,'status':x.status,'summary':x.summary,'latency_ms':x.latency_ms,'result_size':x.result_size,'truncated':x.truncated,'error_code':x.error_code,'created_at':x.created_at} for x in tools],
            'retrievals':[{'query':x.query,'hit_count':x.hit_count,'refs':x.refs,'latency_ms':x.latency_ms,'status':x.status,'error_code':x.error_code,'created_at':x.created_at} for x in retrievals],
        })
    notes=list((await db.scalars(select(Notification).where(Notification.incident_id==inc.id).order_by(Notification.created_at.asc()).limit(100))).all())
    return {'agent_runs':out,'notifications':[{'id':str(n.id),'status':n.status,'attempts':n.attempts,'last_error':n.last_error,'payload':n.payload,'created_at':n.created_at,'sent_at':n.sent_at} for n in notes]}


@app.post('/api/dev/incidents/trigger')
async def dev_trigger_incident(payload:dict,user=Depends(current_user),db:AsyncSession=Depends(get_session)):
    if s.env.lower()!='development':raise HTTPException(404,'not found')
    from uuid import UUID
    from oncall.application.incident_service import IncidentService
    try:
        pid=UUID(str(payload.get('project_id')))
    except (ValueError, TypeError):
        raise HTTPException(400,'invalid project_id')
    project=await ProjectService(db).get(pid,user.id)
    if not project:raise HTTPException(404,'project not found')
    metric_key=str(payload.get('metric_key') or 'host.cpu.percent')
    rule=await db.scalar(select(MonitoringRule).where(MonitoringRule.project_id==pid,MonitoringRule.metric_key==metric_key,MonitoringRule.enabled.is_(True)).order_by(MonitoringRule.id.asc()).limit(1))
    if not rule:raise HTTPException(400,f'no enabled rule for {metric_key}')
    value=float(payload.get('value',rule.trigger_threshold+1))
    inc=await IncidentService(db).on_firing(pid,rule.id,rule.resource_key,rule.metric_key,rule.severity,value)
    conv=await db.scalar(select(Conversation).where(Conversation.incident_id==inc.id).order_by(Conversation.created_at.asc()).limit(1))
    return {'incident_id':str(inc.id),'conversation_id':str(conv.id) if conv else None,'status':inc.status}

@app.post('/api/dev/incidents/{iid}/recover')
async def dev_recover_incident(iid:str,user=Depends(current_user),db:AsyncSession=Depends(get_session)):
    if s.env.lower()!='development':raise HTTPException(404,'not found')
    from oncall.application.incident_service import IncidentService
    inc=await db.scalar(select(Incident).join(Project,Project.id==Incident.project_id).where(Incident.id==_uuid(iid),Project.user_id==user.id))
    if not inc:raise HTTPException(404,'not found')
    await IncidentService(db).resolve(inc.id,'development_smoke_recovered')
    return {'ok':True,'status':'resolved'}

@app.get('/api/settings/readiness')
async def settings_readiness(user=Depends(current_user)):
    return {
        'environment':s.env,
        'llm':{'provider':s.model_provider,'model':s.model_name,'configured':s.model_provider=='mock' or bool(s.model_api_key),'development_fallback':s.model_provider=='mock'},
        'embedding':{'model':s.embedding_model,'configured':bool(s.embedding_api_key),'development_fallback':not bool(s.embedding_api_key)},
        'rerank':{'model':s.rerank_model or None,'configured':bool(s.rerank_base_url and s.rerank_api_key and s.rerank_model),'development_fallback':not bool(s.rerank_base_url and s.rerank_api_key and s.rerank_model)},
        # A default receive_id is optional: inbound messages auto-bind the
        # latest Feishu chat for proactive delivery. Requiring it here made a
        # working bot appear unconfigured in the Settings page.
        'feishu':{
            'enabled':s.feishu_enabled,
            'configured':bool(s.feishu_app_id and s.feishu_app_secret),
            'default_receive_id_configured':bool(s.feishu_default_receive_id),
            'auto_bind_supported':True,
        },
        'security':{'secret_master_key_configured':bool(s.secret_master_key),'default_admin_password_in_use':s.admin_password=='change-me-now'},
        'storage':{'database':'postgresql' if s.database_url.startswith('postgresql') else 'other','milvus_uri':s.milvus_uri,'data_dir':str(s.data_dir)},
    }

@app.get('/api/settings/feishu')
async def feishu_settings(user=Depends(current_user)):
    return {'enabled':s.feishu_enabled,'app_id':s.feishu_app_id,'app_secret_configured':bool(s.feishu_app_secret),'default_receive_id':s.feishu_default_receive_id,'default_receive_id_type':s.feishu_default_receive_id_type,'restart_required':False}

@app.put('/api/settings/feishu')
async def update_feishu_settings(dto:FeishuSettingsDTO,user=Depends(current_user)):
    app_id=dto.app_id.strip()
    app_secret=(dto.app_secret or '').strip() or s.feishu_app_secret
    if dto.enabled and (not app_id or not app_secret):
        raise HTTPException(400,'启用飞书时必须填写 App ID 和 App Secret')
    if dto.enabled:
        import httpx
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response=await client.post('https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal',json={'app_id':app_id,'app_secret':app_secret})
            body=response.json()
        except Exception as exc:
            raise HTTPException(400,f'飞书凭证验证失败：{exc}')
        if response.status_code >= 400 or body.get('code') != 0 or not body.get('tenant_access_token'):
            raise HTTPException(400,f"飞书凭证验证失败：{body.get('msg') or 'unknown error'}")
    update_env_values({'ONCALL_FEISHU_ENABLED':str(dto.enabled).lower(),'ONCALL_FEISHU_APP_ID':app_id,'ONCALL_FEISHU_APP_SECRET':app_secret,'ONCALL_FEISHU_DEFAULT_RECEIVE_ID':dto.default_receive_id.strip(),'ONCALL_FEISHU_DEFAULT_RECEIVE_ID_TYPE':dto.default_receive_id_type})
    return {'ok':True,'message':'飞书配置已保存，重启 API 和 Agent Worker 后生效','restart_required':True}

@app.get('/api/settings/tool-contracts')
async def settings_tool_contracts(user=Depends(current_user)):
    from oncall.agent.tool_contracts import public_tool_specs
    return {'tools':public_tool_specs()}


def run():uvicorn.run('oncall.api.main:app',host=s.host,port=s.port,reload=False)
