from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager

import httpx
import uvicorn
from fastapi import (
    Depends,
    FastAPI,
    File,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, StreamingResponse
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from oncall.api.deps import current_user
from oncall.application.agent_service import AgentService
from oncall.application.conversation_service import ConversationService
from oncall.application.dtos import (
    ChatMessageDTO,
    ConversationCreateDTO,
    ConversationPatchDTO,
    FeishuSettingsDTO,
    IncidentBatchDeleteDTO,
    LogSourceDTO,
    ModelSettingsDTO,
    ModelProbeDTO,
    ModelProfileDTO,
    MetricsApplyDTO,
    MetricsDiscoverDTO,
    MetricsSourceDTO,
    MonitoredServerCreateDTO,
    MonitoredServerDTO,
    ProjectCreateDTO,
    ProjectRuntimeConfig,
    PythonProjectOnboardDTO,
)
from oncall.application.knowledge_service import KnowledgeService
from oncall.application.project_service import ProjectService
from oncall.application.server_service import MonitoredServerService, to_dto
from oncall.application.workspace_service import ensure_local_user
from oncall.bootstrap.config import get_settings, update_env_values
from oncall.bootstrap.logging import configure_logging, set_request_id
from oncall.security.crypto import SecretBox
from oncall.infrastructure.db.models import (
    AgentRun,
    AlertmanagerAlert,
    BackgroundJob,
    Conversation,
    Diagnosis,
    Incident,
    IncidentEvidence,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeDocumentVersion,
    MonitoredServer,
    ModelProfile,
    Notification,
    Project,
    RetrievalTrace,
    ToolRun,
)
from oncall.infrastructure.db.session import SessionFactory, get_session

logger = logging.getLogger(__name__)
s = get_settings()
configure_logging(s.log_level, s.log_dir, s.log_retention_days, "api")


def _uuid(value, what="id"):
    """Parse a path/query UUID; malformed ids are 404, matching the routes' semantics."""
    from uuid import UUID

    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(404, f"{what} not found")


async def _collector_check(server: MonitoredServerDTO) -> dict:
    if not server.collector_url:
        return {"ok": False, "error": "未配置 PulseOps Collector 地址"}
    try:
        async with httpx.AsyncClient(timeout=8, trust_env=False) as client:
            response = await client.get(
                server.collector_url.rstrip("/") + "/health",
                headers={"X-Oncall-Token": server.collector_token or ""},
            )
            response.raise_for_status()
            return response.json()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@asynccontextmanager
async def lifespan(app: FastAPI):
    import os

    if s.langgraph_strict_msgpack:
        os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")
    app.state.checkpointer = None
    logger.info("starting oncall api (env=%s)", s.env)
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        cm = AsyncPostgresSaver.from_conn_string(s.langgraph_database_url)
        cp = await cm.__aenter__()
        await cp.setup()
        app.state.checkpointer = cp
        app.state.checkpointer_cm = cm
        logger.info("langgraph checkpointer ready")
    except Exception as e:
        app.state.checkpointer_error = str(e)
        logger.error("langgraph checkpointer unavailable: %s", e)
    async with SessionFactory() as db:
        await ensure_local_user(db)
        try:
            from oncall.integrations.prometheus_api import PrometheusProvisioner

            await PrometheusProvisioner(db).reconcile()
        except Exception:
            logger.exception("initial Prometheus configuration render failed")
    logger.info("local workspace ready")
    app.state.feishu_ws_thread = None
    if s.feishu_enabled:
        try:
            import asyncio

            from oncall.channels.feishu import start_ws_listener
            from oncall.channels.feishu_gateway import FeishuGateway, build_lark_callback

            loop = asyncio.get_running_loop()
            gateway = FeishuGateway(app.state.checkpointer)
            app.state.feishu_ws_thread = start_ws_listener(build_lark_callback(loop, gateway))
            logger.info("feishu websocket listener started")
        except Exception as e:
            app.state.feishu_ws_error = str(e)
            logger.error("feishu websocket listener failed: %s", e)
    yield
    logger.info("stopping oncall api")
    if getattr(app.state, "checkpointer_cm", None):
        await app.state.checkpointer_cm.__aexit__(None, None, None)


app = FastAPI(title="PulseOps · 巡脉智能运维平台", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[s.web_origin],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    from time import perf_counter
    from uuid import uuid4

    request_id = request.headers.get("x-request-id") or str(uuid4())
    request.state.request_id = request_id
    set_request_id(request_id)
    start = perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("request failed %s %s", request.method, request.url.path)
        raise
    duration_ms = (perf_counter() - start) * 1000
    logger.info(
        "%s %s -> %d (%.1f ms)", request.method, request.url.path, response.status_code, duration_ms
    )
    response.headers["x-request-id"] = request_id
    return response


@app.get("/api/health")
async def health(request: Request):
    db_ok = False
    db_error = None
    try:
        async with SessionFactory() as db:
            await db.execute(text("select 1"))
            db_ok = True
    except Exception as exc:
        db_error = str(exc)
        logger.error("health check database probe failed: %s", exc)
    return {
        "ok": db_ok,
        "database": db_ok,
        "database_error": db_error,
        "checkpointer": bool(request.app.state.checkpointer),
        "checkpointer_error": getattr(request.app.state, "checkpointer_error", None),
        "feishu_ws_error": getattr(request.app.state, "feishu_ws_error", None),
    }


@app.post("/api/webhooks/alertmanager")
async def alertmanager_webhook(
    payload: dict,
    request: Request,
    db: AsyncSession = Depends(get_session),
    x_alertmanager_token: str | None = Header(default=None),
):
    """Alertmanager is the only external alert decision point."""
    expected = s.alertmanager_webhook_token
    authorization = request.headers.get("authorization", "")
    bearer = authorization.removeprefix("Bearer ").strip()
    if expected and x_alertmanager_token != expected and bearer != expected:
        raise HTTPException(401, "invalid Alertmanager webhook token")
    from oncall.application.incident_service import IncidentService

    incidents = await IncidentService(db).on_alertmanager_webhook(payload)
    return {
        "ok": True,
        "received": len(payload.get("alerts") or []),
        "incidents": [str(x.id) for x in incidents],
    }


@app.get("/api/prometheus/projects/{pid}/database-metrics", response_class=PlainTextResponse)
async def prometheus_database_metrics(
    pid: str, token: str = Query(default=""), db: AsyncSession = Depends(get_session)
):
    """Expose Collector-derived PostgreSQL numbers for Prometheus scraping.

    The endpoint never returns SQL text, connection credentials or database
    structure. Detailed inspection remains an LLM-only diagnostic tool.
    """
    if s.prometheus_scrape_token and token != s.prometheus_scrape_token:
        raise HTTPException(401, "invalid Prometheus scrape token")
    project_id = _uuid(pid)
    project = await db.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "project not found")
    config = await ProjectService(db).runtime_config(project_id, include_disabled=False)
    from oncall.integrations.observability import RemoteObservabilityIntegration

    integration = RemoteObservabilityIntegration(
        config.server, config.log_sources, config.database_profiles
    )
    signals: dict[str, float] = {"db.up": 0.0}
    try:
        result = await integration.query_database(
            [
                "availability",
                "connections",
                "long_transactions",
                "lock_waits",
                "deadlocks",
                "replication",
                "cache_hit",
            ]
        )
        payload = result.data if isinstance(result.data, dict) else {}
        signals.update({key: float(value) for key, value in (payload.get("signals") or {}).items()})
        if not result.ok:
            signals["db.up"] = 0.0
    except Exception:
        logger.exception("database metrics scrape failed for project %s", project_id)
        signals["db.up"] = 0.0
    names = {
        "db.up": "oncall_postgres_up",
        "db.connections.active": "oncall_postgres_connections_active",
        "db.connections.max": "oncall_postgres_connections_max",
        "db.connections.utilization_percent": "oncall_postgres_connections_utilization_percent",
        "db.long_transactions": "oncall_postgres_long_transactions",
        "db.lock_waits": "oncall_postgres_lock_waits",
        "db.deadlocks_total": "oncall_postgres_deadlocks_total",
        "db.cache_hit_percent": "oncall_postgres_cache_hit_percent",
        "db.replication_lag_seconds": "oncall_postgres_replication_lag_seconds",
    }
    lines = []
    for source_name, metric_name in names.items():
        if source_name not in signals:
            continue
        metric_type = "counter" if source_name == "db.deadlocks_total" else "gauge"
        lines.extend(
            [f"# TYPE {metric_name} {metric_type}", f"{metric_name} {signals[source_name]:.12g}"]
        )
    return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")


@app.get(
    "/api/prometheus/projects/{pid}/application-metrics/{source_id}",
    response_class=PlainTextResponse,
)
async def prometheus_application_metrics(
    pid: str,
    source_id: str,
    token: str = Query(default=""),
    db: AsyncSession = Depends(get_session),
):
    """Relay one configured application endpoint to Prometheus.

    This keeps target credentials inside Oncall and gives Prometheus a stable
    network path. The response is passed through unchanged and no rule is
    evaluated by the application.
    """
    if s.prometheus_scrape_token and token != s.prometheus_scrape_token:
        raise HTTPException(401, "invalid Prometheus scrape token")
    project_id = _uuid(pid)
    wanted_source_id = _uuid(source_id, "metrics source")
    project = await db.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "project not found")
    config = await ProjectService(db).runtime_config(project_id, include_disabled=False)
    source = next((item for item in config.metrics_sources if item.id == wanted_source_id), None)
    if source is None:
        raise HTTPException(404, "metrics source not found")
    import base64

    headers: dict[str, str] = {}
    if source.auth_type == "bearer" and source.token:
        headers["Authorization"] = f"Bearer {source.token}"
    elif source.auth_type == "basic" and source.token:
        headers["Authorization"] = "Basic " + base64.b64encode(source.token.encode()).decode()
    try:
        async with httpx.AsyncClient(
            timeout=source.scrape_timeout_ms / 1000, trust_env=False
        ) as client:
            response = await client.get(source.url, headers=headers)
            response.raise_for_status()
    except Exception as exc:
        raise HTTPException(502, f"application metrics unavailable: {exc}")
    return PlainTextResponse(response.text, media_type="text/plain; version=0.0.4")


@app.get("/api/servers")
async def list_servers(user=Depends(current_user), db: AsyncSession = Depends(get_session)):
    rows = await MonitoredServerService(db).list(user.id)
    return [
        {**to_dto(server).model_dump(mode="json"), "project_count": project_count}
        for server, project_count in rows
    ]


@app.post("/api/servers/test")
async def test_server_draft(dto: MonitoredServerCreateDTO, user=Depends(current_user)):
    from uuid import uuid4

    from oncall.integrations.server_exporters import ServerExportersIntegration

    server_dto = MonitoredServerDTO(id=uuid4(), **dto.model_dump())
    result = await ServerExportersIntegration(server_dto).collect()
    collector = (
        await _collector_check(server_dto)
        if server_dto.collector_url
        else {"ok": True, "configured": False}
    )
    return {
        "ok": result.ok and bool(collector.get("ok")),
        "signals": result.signals,
        "resource_signals": result.resource_signals,
        "resources": {**result.resources, "collector": collector},
        "error": result.error or collector.get("error"),
    }


@app.post("/api/servers")
async def create_server(
    dto: MonitoredServerCreateDTO,
    user=Depends(current_user),
    db: AsyncSession = Depends(get_session),
):
    try:
        row = await MonitoredServerService(db).create(user.id, dto)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    return to_dto(row).model_dump(mode="json")


@app.put("/api/servers/{sid}")
async def update_server(
    sid: str,
    dto: MonitoredServerCreateDTO,
    user=Depends(current_user),
    db: AsyncSession = Depends(get_session),
):
    row = await MonitoredServerService(db).update(_uuid(sid), user.id, dto)
    if row is None:
        raise HTTPException(404, "not found")
    return to_dto(row).model_dump(mode="json")


@app.post("/api/servers/{sid}/test")
async def test_server(
    sid: str, user=Depends(current_user), db: AsyncSession = Depends(get_session)
):
    from oncall.integrations.server_exporters import ServerExportersIntegration

    row = await MonitoredServerService(db).get(_uuid(sid), user.id)
    if row is None:
        raise HTTPException(404, "not found")
    server_dto = to_dto(row)
    server_dto.collector_token = MonitoredServerService(db).collector_token(row)
    result = await ServerExportersIntegration(server_dto).collect()
    collector = (
        await _collector_check(server_dto)
        if server_dto.collector_url
        else {"ok": True, "configured": False}
    )
    return {
        "ok": result.ok and bool(collector.get("ok")),
        "signals": result.signals,
        "resource_signals": result.resource_signals,
        "resources": {**result.resources, "collector": collector},
        "error": result.error or collector.get("error"),
    }


@app.delete("/api/servers/{sid}")
async def delete_server(
    sid: str, user=Depends(current_user), db: AsyncSession = Depends(get_session)
):
    try:
        deleted = await MonitoredServerService(db).delete(_uuid(sid), user.id)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    if not deleted:
        raise HTTPException(404, "not found")
    return {"ok": True}


@app.get("/api/conversations")
async def conversations(
    q: str | None = Query(default=None, max_length=200),
    include_archived: bool = False,
    user=Depends(current_user),
    db: AsyncSession = Depends(get_session),
):
    rows = await ConversationService(db).list(user.id, include_archived=include_archived, query=q)
    return [
        {
            "id": str(x.id),
            "title": x.title,
            "type": x.type,
            "project_id": str(x.project_id) if x.project_id else None,
            "incident_id": str(x.incident_id) if x.incident_id else None,
            "archived": x.archived,
            "updated_at": x.updated_at,
        }
        for x in rows
    ]


@app.post("/api/conversations")
async def create_conversation(
    dto: ConversationCreateDTO, user=Depends(current_user), db: AsyncSession = Depends(get_session)
):
    c = await ConversationService(db).create(user.id, dto.title, dto.project_id)
    return {"id": str(c.id), "title": c.title}


@app.patch("/api/conversations/{cid}")
async def patch_conversation(
    cid: str,
    dto: ConversationPatchDTO,
    user=Depends(current_user),
    db: AsyncSession = Depends(get_session),
):
    c = await ConversationService(db).patch(_uuid(cid), user.id, dto.title, dto.archived)
    if not c:
        raise HTTPException(404, "not found")
    return {"id": str(c.id), "title": c.title, "archived": c.archived}


@app.delete("/api/conversations/{cid}")
async def delete_conversation(
    cid: str,
    request: Request,
    user=Depends(current_user),
    db: AsyncSession = Depends(get_session),
):
    if not await ConversationService(db).delete(
        _uuid(cid), user.id, getattr(request.app.state, "checkpointer", None)
    ):
        raise HTTPException(404, "not found")
    return {"ok": True}


@app.get("/api/conversations/{cid}/messages")
async def list_messages(
    cid: str, user=Depends(current_user), db: AsyncSession = Depends(get_session)
):
    cid_u = _uuid(cid)
    if not await ConversationService(db).get(cid_u, user.id):
        raise HTTPException(404, "not found")
    rows = await ConversationService(db).messages(cid_u)
    return [
        {
            "id": str(x.id),
            "role": x.role,
            "content": x.content,
            "channel": x.channel,
            "created_at": x.created_at,
            "metadata": x.metadata_json,
        }
        for x in rows
    ]


@app.post("/api/conversations/{cid}/messages:stream")
async def chat(
    cid: str,
    dto: ChatMessageDTO,
    request: Request,
    user=Depends(current_user),
    db: AsyncSession = Depends(get_session),
):
    cid_u = _uuid(cid)
    conv = await ConversationService(db).get(cid_u, user.id)
    if not conv:
        raise HTTPException(404, "not found")
    checkpointer = getattr(request.app.state, "checkpointer", None)

    async def gen():
        import asyncio

        queue: asyncio.Queue = asyncio.Queue()

        def emit(event_type: str, data: dict) -> None:
            queue.put_nowait((event_type, data))

        async def work():
            try:
                # Streaming responses can outlive request-scoped dependencies. Give the
                # Agent its own DB session so persistence remains valid until the stream ends.
                async with SessionFactory() as agent_db:
                    state = await AgentService(agent_db, checkpointer).run(
                        cid_u, dto.content, dto.channel, emit=emit
                    )
                text = (state or {}).get("final_response", "")
                queue.put_nowait(("final", {"content": text}))
            except Exception as e:
                queue.put_nowait(("error", {"message": str(e)}))

        task = asyncio.create_task(work())
        yield (
            "event: status\ndata: "
            + json.dumps({"stage": "reasoning"}, ensure_ascii=False)
            + "\n\n"
        )
        try:
            while True:
                event_type, data = await queue.get()
                yield f"event: {event_type}\ndata: " + json.dumps(data, ensure_ascii=False) + "\n\n"
                if event_type in ("final", "error"):
                    break
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/projects")
async def list_projects(user=Depends(current_user), db: AsyncSession = Depends(get_session)):
    rows = await ProjectService(db).list(user.id)
    server_ids = {x.server_id for x in rows if x.server_id}
    servers = (
        {
            x.id: x
            for x in (
                await db.scalars(select(MonitoredServer).where(MonitoredServer.id.in_(server_ids)))
            ).all()
        }
        if server_ids
        else {}
    )
    return [
        {
            "id": str(x.id),
            "server_id": str(x.server_id) if x.server_id else None,
            "server_name": servers[x.server_id].name if x.server_id in servers else None,
            "name": x.name,
            "description": x.description,
            "environment": x.environment,
            "enabled": x.enabled,
            "poll_interval": x.poll_interval,
            "updated_at": x.updated_at,
        }
        for x in rows
    ]


@app.post("/api/projects")
async def create_project(
    dto: ProjectCreateDTO, user=Depends(current_user), db: AsyncSession = Depends(get_session)
):
    try:
        p = await ProjectService(db).create(user.id, dto)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"id": str(p.id), "name": p.name}


@app.post("/api/projects/onboard/python")
async def onboard_python_project(
    dto: PythonProjectOnboardDTO,
    user=Depends(current_user),
    db: AsyncSession = Depends(get_session),
):
    """Create a remote Python project linked to a tested server."""
    try:
        p = await ProjectService(db).create_python(user.id, dto)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"id": str(p.id), "name": p.name, "environment": p.environment, "enabled": p.enabled}


@app.post("/api/projects/onboard/python/test")
async def test_python_project_draft(
    dto: PythonProjectOnboardDTO,
    user=Depends(current_user),
    db: AsyncSession = Depends(get_session),
):
    """Dry-run all three remote observation paths before a project is saved."""
    from uuid import uuid4

    from oncall.monitoring.probe import ProjectProbe

    server = await MonitoredServerService(db).get(dto.server_id, user.id)
    if server is None:
        raise HTTPException(404, "selected server was not found")
    project_id = uuid4()
    server_dto = to_dto(server)
    server_dto.collector_token = MonitoredServerService(db).collector_token(server)
    try:
        database = ProjectService.database_profile_from_url(dto.database_url)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    config = ProjectRuntimeConfig(
        id=project_id,
        user_id=user.id,
        server_id=server.id,
        server=server_dto,
        name=dto.name,
        description=dto.description,
        environment="production",
        enabled=False,
        poll_interval=dto.poll_interval,
        metrics_sources=[MetricsSourceDTO(name="app", url=dto.metrics_url, enabled=True)],
        log_sources=[
            LogSourceDTO(
                path="docker://auto",
                parser_config={
                    "target_urls": [dto.metrics_url],
                    "compose_project": dto.compose_project,
                    "services": dto.compose_services,
                },
                enabled=True,
            )
        ],
        database_profiles=[database],
    )
    snap = await ProjectProbe().run(project_id, config)
    required = ("server", "prometheus")
    checks = [
        {
            "key": key,
            "ok": bool(snap.collector_status.get(key, {}).get("ok")),
            "error": snap.collector_status.get(key, {}).get("error"),
        }
        for key in required
    ]
    sources = (snap.resources.get("prometheus") or {}).get("sources") or []
    has_http_metrics = any(int(x.get("routes", 0) or 0) > 0 for x in sources if x.get("ok"))
    has_process_metrics = any(bool(x.get("process_metrics")) for x in sources if x.get("ok"))
    warnings = []
    if checks[-1]["ok"] and not has_http_metrics:
        warnings.append(
            "指标地址可访问，但没有识别到带路由和状态码的 HTTP 指标；请求率、错误率和延迟规则暂时没有有效数据。"
        )
    if checks[-1]["ok"] and not has_process_metrics:
        warnings.append(
            "指标地址可访问，但没有识别到 Python process_* 指标；进程 CPU、内存、文件数和运行时长暂时没有有效数据。"
        )
    observability = snap.resources.get("observability") or {}
    log_result = observability.get("logs") or {}
    database_result = observability.get("database") or {}
    checks.extend(
        [
            {"key": "logs", "ok": bool(log_result.get("ok")), "error": log_result.get("error")},
            {
                "key": "database",
                "ok": bool(database_result.get("ok")),
                "error": database_result.get("error"),
            },
        ]
    )
    return {
        "ok": all(x["ok"] for x in checks),
        "checks": checks,
        "capabilities": {
            "host_metrics": checks[0]["ok"],
            "gpu_metrics": "host.gpu.available" in snap.signals,
            "http_metrics": has_http_metrics,
            "process_metrics": has_process_metrics,
            "docker_logs": bool(log_result.get("ok")),
            "database_health": bool(database_result.get("ok")),
            "slow_sql": bool((database_result.get("capabilities") or {}).get("slow_sql")),
        },
        "warnings": warnings,
        "signals": snap.signals,
        "collector_status": snap.collector_status,
    }


@app.put("/api/projects/{pid}")
async def update_project(
    pid: str,
    dto: ProjectCreateDTO,
    user=Depends(current_user),
    db: AsyncSession = Depends(get_session),
):
    try:
        p = await ProjectService(db).update(_uuid(pid), user.id, dto)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if not p:
        raise HTTPException(404, "not found")
    return {"id": str(p.id), "name": p.name}


def _project_runtime_payload(cfg):
    data = cfg.model_dump(mode="json")
    for source in data.get("metrics_sources", []):
        # The worker receives decrypted credentials from runtime_config(), but
        # browser reads must never echo them back.
        source["token"] = None
    if data.get("server"):
        data["server"]["collector_token"] = None
    for profile in data.get("database_profiles", []):
        profile["password"] = None
    return data


@app.get("/api/projects/{pid}")
async def project_detail(
    pid: str, user=Depends(current_user), db: AsyncSession = Depends(get_session)
):
    project = await ProjectService(db).get(_uuid(pid), user.id)
    if not project:
        raise HTTPException(404, "not found")
    cfg = await ProjectService(db).runtime_config(project.id, include_disabled=True)
    return _project_runtime_payload(cfg)


@app.delete("/api/projects/{pid}")
async def delete_project(
    pid: str, user=Depends(current_user), db: AsyncSession = Depends(get_session)
):
    if not await ProjectService(db).delete(_uuid(pid), user.id):
        raise HTTPException(404, "not found")
    return {"ok": True}


@app.post("/api/projects/{pid}/test")
async def test_project(
    pid: str,
    payload: ProjectCreateDTO | None = None,
    user=Depends(current_user),
    db: AsyncSession = Depends(get_session),
):
    import asyncio

    from oncall.monitoring.probe import ProjectProbe

    project = await ProjectService(db).get(_uuid(pid), user.id)
    if not project:
        raise HTTPException(404, "not found")
    config = None
    if payload is not None:
        saved = await ProjectService(db).runtime_config(project.id, include_disabled=True)
        if payload.server_id != saved.server_id:
            raise HTTPException(400, "请先保存服务器绑定，再测试采集")
        draft = payload.model_copy(deep=True)
        saved_metrics_by_id = {x.id: x for x in saved.metrics_sources if x.id is not None}
        saved_metrics_by_name = {x.name: x for x in saved.metrics_sources}
        for source in draft.metrics_sources:
            if source.token is None:
                persisted = saved_metrics_by_id.get(source.id) or saved_metrics_by_name.get(
                    source.name
                )
                if persisted is not None:
                    source.token = persisted.token
        saved_databases_by_id = {x.id: x for x in saved.database_profiles if x.id is not None}
        saved_databases_by_key = {
            (x.host, x.port, x.database, x.username): x for x in saved.database_profiles
        }
        for profile in draft.database_profiles:
            if profile.password is None:
                persisted = saved_databases_by_id.get(profile.id) or saved_databases_by_key.get(
                    (profile.host, profile.port, profile.database, profile.username)
                )
                if persisted is not None:
                    profile.password = persisted.password
        config = ProjectRuntimeConfig(
            id=project.id, user_id=project.user_id, server=saved.server, **draft.model_dump()
        )
    try:
        snap = await asyncio.wait_for(
            ProjectProbe().run(
                project.id,
                config
                or await ProjectService(db).runtime_config(project.id, include_disabled=True),
            ),
            timeout=20,
        )
    except TimeoutError:
        raise HTTPException(504, "采集测试超过 20 秒，请检查 Collector、应用指标地址和数据库连接")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return snap.model_dump(mode="json")


@app.post("/api/projects/{pid}/metrics/discover")
async def discover_metrics(
    pid: str,
    dto: MetricsDiscoverDTO,
    user=Depends(current_user),
    db: AsyncSession = Depends(get_session),
):
    """Inspect a /metrics endpoint; Prometheus owns the alert policy."""
    project = await ProjectService(db).get(_uuid(pid), user.id)
    if not project:
        raise HTTPException(404, "not found")
    import base64

    from prometheus_client.parser import text_string_to_metric_families

    headers = {}
    if dto.auth_type == "bearer" and dto.token:
        headers["Authorization"] = f"Bearer {dto.token}"
    if dto.auth_type == "basic" and dto.token:
        headers["Authorization"] = "Basic " + base64.b64encode(dto.token.encode()).decode()
    try:
        async with httpx.AsyncClient(
            timeout=dto.scrape_timeout_ms / 1000, trust_env=False
        ) as client:
            response = await client.get(dto.url, headers=headers)
            response.raise_for_status()
        families = list(text_string_to_metric_families(response.text))
    except Exception as exc:
        raise HTTPException(502, f"Prometheus 指标地址读取失败：{exc}")
    names = [family.name for family in families]
    return {
        "source": {
            "name": "app",
            "url": dto.url,
            "auth_type": dto.auth_type,
            "route_label": dto.route_label,
            "enabled": True,
        },
        "metrics": {
            "families": names,
            "count": len(names),
            "has_process_metrics": any(x.startswith("process_") for x in names),
            "has_http_metrics": any(
                x.startswith(("http_", "starlette_", "django_", "flask_")) for x in names
            ),
        },
        "alert_policy": "prometheus-managed",
    }


@app.post("/api/projects/{pid}/metrics/apply")
async def apply_metrics(
    pid: str,
    dto: MetricsApplyDTO,
    user=Depends(current_user),
    db: AsyncSession = Depends(get_session),
):
    """Persist a discovered scrape source and let Prometheus reconcile rules."""
    project = await ProjectService(db).get(_uuid(pid), user.id)
    if not project:
        raise HTTPException(404, "not found")
    cfg = await ProjectService(db).runtime_config(project.id, include_disabled=True)
    added_sources = 0
    src_key = (dto.source.name.strip(), dto.source.url.strip())
    if src_key not in {(s.name, s.url) for s in cfg.metrics_sources}:
        cfg.metrics_sources.append(dto.source)
        added_sources += 1
    try:
        await ProjectService(db).update(project.id, user.id, cfg)
    except ValueError as e:
        raise HTTPException(400, f"配置校验失败：{e}")
    return {
        "ok": True,
        "added_metrics_sources": added_sources,
        "alert_policy": "prometheus-managed",
    }


@app.get("/api/projects/{pid}/snapshot")
async def latest_project_snapshot(
    pid: str, user=Depends(current_user), db: AsyncSession = Depends(get_session)
):
    project = await ProjectService(db).get(_uuid(pid), user.id)
    if not project:
        raise HTTPException(404, "not found")
    import asyncio
    import math

    from oncall.integrations.prometheus_api import PrometheusClient, project_metric_promql

    try:
        client = PrometheusClient()
        targets = await client.query(f'up{{project_id="{project.id}"}}')
        dashboard_metrics = [
            "host.exporter.up",
            "host.cpu.percent",
            "host.memory.percent",
            "host.disk.usage_percent",
            "app.up",
            "app.http.rps",
            "app.http.error_rate",
            "app.http.p95_ms",
            "db.up",
            "db.connections.active",
            "db.connections.max",
            "db.connections.utilization_percent",
            "db.long_transactions",
            "db.lock_waits",
            "db.deadlocks_total",
            "db.cache_hit_percent",
            "db.replication_lag_seconds",
        ]

        async def read_metric(key: str):
            expression = project_metric_promql(key, project.id)
            if not expression:
                return key, None
            rows = await client.query(expression)
            if not rows:
                return key, None
            try:
                value = float(rows[0].get("value", [0, None])[1])
                return key, value if math.isfinite(value) else None
            except (TypeError, ValueError, IndexError):
                return key, None

        metric_rows = await asyncio.gather(
            *(read_metric(key) for key in dashboard_metrics), return_exceptions=True
        )
        signals = {
            "prometheus.targets.up": float(sum(float(x.get("value", [0, 0])[1]) for x in targets))
        }
        for row in metric_rows:
            if not isinstance(row, BaseException) and row[1] is not None:
                signals[row[0]] = row[1]
        snapshot = {
            "project_id": str(project.id),
            "observed_at": __import__("datetime").datetime.now().astimezone(),
            "signals": signals,
            "resource_signals": {},
            "resources": {"prometheus": targets},
            "collector_status": {"prometheus": {"ok": True, "error": None}},
        }
    except Exception as exc:
        snapshot = {
            "project_id": str(project.id),
            "observed_at": __import__("datetime").datetime.now().astimezone(),
            "signals": {},
            "resource_signals": {},
            "resources": {"prometheus": {"error": str(exc)}},
            "collector_status": {"prometheus": {"ok": False, "error": str(exc)}},
        }
    active = list(
        (
            await db.scalars(
                select(AlertmanagerAlert)
                .where(
                    AlertmanagerAlert.project_id == project.id, AlertmanagerAlert.status == "firing"
                )
                .order_by(AlertmanagerAlert.received_at.desc())
                .limit(50)
            )
        ).all()
    )
    snapshot["resources"]["active_alerts"] = [
        {
            "fingerprint": x.fingerprint,
            "alertname": x.alertname,
            "severity": x.severity,
            "labels": x.labels,
            "annotations": x.annotations,
            "value": x.value,
        }
        for x in active
    ]
    return {
        "snapshot": snapshot,
        "collector_status": snapshot["collector_status"],
        "observed_at": snapshot["observed_at"],
    }


@app.get("/api/incidents")
async def list_incidents(user=Depends(current_user), db: AsyncSession = Depends(get_session)):
    rows = list(
        (
            await db.scalars(
                select(Incident)
                .join(Project, Project.id == Incident.project_id)
                .where(Project.user_id == user.id)
                .order_by(Incident.last_seen.desc())
                .limit(200)
            )
        ).all()
    )
    return [
        {
            "id": str(x.id),
            "project_id": str(x.project_id),
            "status": x.status,
            "severity": x.severity,
            "summary": x.summary,
            "anomaly_type": x.anomaly_type,
            "resource_key": x.resource_key,
            "first_seen": x.first_seen,
            "last_seen": x.last_seen,
            "resolved_at": x.resolved_at,
            "occurrence_count": x.occurrence_count,
        }
        for x in rows
    ]


@app.delete("/api/incidents")
async def delete_incidents(
    dto: IncidentBatchDeleteDTO,
    request: Request,
    user=Depends(current_user),
    db: AsyncSession = Depends(get_session),
):
    ids = list(dict.fromkeys(dto.ids))
    owned_ids = set(
        (
            await db.scalars(
                select(Incident.id)
                .join(Project, Project.id == Incident.project_id)
                .where(Incident.id.in_(ids), Project.user_id == user.id)
            )
        ).all()
    )
    if len(owned_ids) != len(ids):
        raise HTTPException(404, "one or more incidents not found")
    from oncall.application.incident_service import IncidentService

    deleted = await IncidentService(db).delete_many(
        ids, getattr(request.app.state, "checkpointer", None)
    )
    return {"ok": True, "deleted": deleted}


@app.get("/api/incidents/{iid}")
async def incident_detail(
    iid: str, user=Depends(current_user), db: AsyncSession = Depends(get_session)
):
    x = await db.scalar(
        select(Incident)
        .join(Project, Project.id == Incident.project_id)
        .where(Incident.id == _uuid(iid), Project.user_id == user.id)
    )
    if not x:
        raise HTTPException(404, "not found")
    d = await db.scalar(
        select(Diagnosis)
        .where(Diagnosis.incident_id == x.id)
        .order_by(Diagnosis.created_at.desc())
        .limit(1)
    )
    evidence = list(
        (
            await db.scalars(
                select(IncidentEvidence)
                .where(IncidentEvidence.incident_id == x.id)
                .order_by(IncidentEvidence.observed_at.asc())
                .limit(200)
            )
        ).all()
    )
    conv = await db.scalar(
        select(Conversation)
        .where(Conversation.incident_id == x.id)
        .order_by(Conversation.created_at.asc())
        .limit(1)
    )
    return {
        "id": str(x.id),
        "project_id": str(x.project_id),
        "status": x.status,
        "severity": x.severity,
        "summary": x.summary,
        "anomaly_type": x.anomaly_type,
        "resource_key": x.resource_key,
        "first_seen": x.first_seen,
        "last_seen": x.last_seen,
        "resolved_at": x.resolved_at,
        "occurrence_count": x.occurrence_count,
        "conversation_id": str(conv.id) if conv else None,
        "diagnosis": d.structured_json if d else None,
        "evidence": [
            {
                "id": str(e.id),
                "type": e.type,
                "source": e.source,
                "observed_at": e.observed_at,
                "summary": e.summary,
                "data": e.data,
                "raw_ref": e.raw_ref,
            }
            for e in evidence
        ],
    }


@app.delete("/api/incidents/{iid}")
async def delete_incident(
    iid: str,
    request: Request,
    user=Depends(current_user),
    db: AsyncSession = Depends(get_session),
):
    from oncall.application.incident_service import IncidentService

    inc = await db.scalar(
        select(Incident)
        .join(Project, Project.id == Incident.project_id)
        .where(Incident.id == _uuid(iid), Project.user_id == user.id)
    )
    if not inc:
        raise HTTPException(404, "not found")
    await IncidentService(db).delete(
        inc.id, getattr(request.app.state, "checkpointer", None)
    )
    return {"ok": True}


@app.post("/api/incidents/{iid}/investigate")
async def reinvestigate(
    iid: str, user=Depends(current_user), db: AsyncSession = Depends(get_session)
):
    from uuid import uuid4

    inc = await db.scalar(
        select(Incident)
        .join(Project, Project.id == Incident.project_id)
        .where(Incident.id == _uuid(iid), Project.user_id == user.id)
    )
    if not inc:
        raise HTTPException(404, "not found")
    conv = await db.scalar(
        select(Conversation)
        .where(Conversation.incident_id == inc.id)
        .order_by(Conversation.created_at.asc())
        .limit(1)
    )
    if not conv:
        conv = await ConversationService(db).create(
            user.id,
            title=f"🚨 {inc.anomaly_type}",
            project_id=inc.project_id,
            incident_id=inc.id,
            type_="incident",
        )
    from oncall.jobs.queue import JobQueue

    job = await JobQueue(db).enqueue(
        "incident_investigate",
        {"incident_id": str(inc.id), "conversation_id": str(conv.id)},
        idempotency_key=f"incident_investigate:{inc.id}:manual:{uuid4()}",
        priority=10,
    )
    return {"job_id": str(job.id), "conversation_id": str(conv.id)}


@app.post("/api/incidents/{iid}/resolve")
async def manual_resolve(
    iid: str, user=Depends(current_user), db: AsyncSession = Depends(get_session)
):
    from oncall.application.incident_service import IncidentService

    inc = await db.scalar(
        select(Incident)
        .join(Project, Project.id == Incident.project_id)
        .where(Incident.id == _uuid(iid), Project.user_id == user.id)
    )
    if not inc:
        raise HTTPException(404, "not found")
    await IncidentService(db).resolve(inc.id, "manual_resolve")
    return {"ok": True, "status": "resolved"}


@app.post("/api/incidents/{iid}/conversation")
async def incident_conversation(
    iid: str, user=Depends(current_user), db: AsyncSession = Depends(get_session)
):
    inc = await db.scalar(
        select(Incident)
        .join(Project, Project.id == Incident.project_id)
        .where(Incident.id == _uuid(iid), Project.user_id == user.id)
    )
    if not inc:
        raise HTTPException(404, "not found")
    conv = await db.scalar(
        select(Conversation)
        .where(Conversation.incident_id == inc.id)
        .order_by(Conversation.created_at.asc())
        .limit(1)
    )
    if not conv:
        conv = await ConversationService(db).create(
            user.id,
            title=f"🚨 {inc.anomaly_type}",
            project_id=inc.project_id,
            incident_id=inc.id,
            type_="incident",
        )
    return {"conversation_id": str(conv.id)}


@app.get("/api/monitoring/metrics")
async def metrics(
    project_id: str,
    metric_key: str,
    user=Depends(current_user),
    db: AsyncSession = Depends(get_session),
):
    pid = _uuid(project_id, "project_id")
    if not await ProjectService(db).get(pid, user.id):
        raise HTTPException(404, "project not found")
    from oncall.integrations.prometheus_api import PrometheusClient

    if not metric_key.replace("_", "").replace(":", "").isalnum():
        raise HTTPException(400, "invalid Prometheus metric name")
    try:
        rows = await PrometheusClient().query(f'{metric_key}{{project_id="{pid}"}}')
    except Exception as exc:
        raise HTTPException(502, f"Prometheus query failed: {exc}")
    return [
        {"metric": x.get("metric", {}), "value": x.get("value"), "values": x.get("values", [])}
        for x in rows
    ]


@app.get("/api/knowledge/documents")
async def documents(user=Depends(current_user), db: AsyncSession = Depends(get_session)):
    rows = await KnowledgeService(db).list_documents(user.id)
    return [
        {
            "id": str(x.id),
            "title": x.title,
            "status": x.status,
            "updated_at": x.updated_at,
        }
        for x in rows
    ]


@app.get("/api/knowledge/citations/{chunk_id}")
async def knowledge_citation(
    chunk_id: str, user=Depends(current_user), db: AsyncSession = Depends(get_session)
):
    """Return the authoritative source chunk behind a chat citation.

    The response deliberately exposes the normalized chunk and nearby context,
    never local filesystem paths. Knowledge is workspace-shared in scope, while
    the current data model still uses the owning user as the access boundary.
    """
    row = await db.execute(
        select(KnowledgeChunk, KnowledgeDocumentVersion, KnowledgeDocument)
        .join(KnowledgeDocumentVersion, KnowledgeDocumentVersion.id == KnowledgeChunk.version_id)
        .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeDocumentVersion.document_id)
        .where(KnowledgeChunk.id == _uuid(chunk_id, "citation"), KnowledgeDocument.user_id == user.id)
    )
    result = row.first()
    if not result:
        raise HTTPException(404, "knowledge citation not found")
    chunk, version, document = result
    neighbors = list(
        (
            await db.scalars(
                select(KnowledgeChunk)
                .where(
                    KnowledgeChunk.version_id == chunk.version_id,
                    KnowledgeChunk.chunk_index.in_([chunk.chunk_index - 1, chunk.chunk_index + 1]),
                )
                .order_by(KnowledgeChunk.chunk_index.asc())
            )
        ).all()
    )

    def chunk_view(item: KnowledgeChunk, limit: int = 24000) -> dict:
        content = item.content or ""
        return {
            "chunk_id": str(item.id),
            "chunk_index": item.chunk_index,
            "heading_path": item.heading_path or [],
            "page_range": item.page_range,
            "content": content[:limit],
            "truncated": len(content) > limit,
        }

    return {
        **chunk_view(chunk),
        "title": document.title,
        "document_id": str(document.id),
        "version_id": str(version.id),
        "original_filename": version.original_filename,
        "parser_version": version.parser_version,
        "metadata": chunk.metadata_json or {},
        "neighbors": [chunk_view(item, 2400) for item in neighbors],
    }


@app.post("/api/knowledge/documents")
async def upload_document(
    file: UploadFile = File(...),
    user=Depends(current_user),
    db: AsyncSession = Depends(get_session),
):
    import tempfile
    from pathlib import Path

    filename = Path(file.filename or "upload.bin").name
    limit = s.knowledge_max_upload_mb * 1024 * 1024
    with tempfile.TemporaryDirectory(prefix="oncall-upload-") as td:
        path = Path(td) / filename
        size = 0
        with path.open("wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, f"file exceeds {s.knowledge_max_upload_mb} MiB limit")
                out.write(chunk)
        try:
            ver, job = await KnowledgeService(db).upload(user.id, path, filename)
        except ValueError as e:
            raise HTTPException(400, str(e))
    return {"version_id": str(ver.id), "job_id": str(job.id), "status": ver.status}


@app.get("/api/knowledge/jobs/{jid}")
async def knowledge_job(
    jid: str, user=Depends(current_user), db: AsyncSession = Depends(get_session)
):
    from uuid import UUID

    job = await db.get(BackgroundJob, _uuid(jid))
    if not job or job.type not in ("rag_ingest", "knowledge_reindex"):
        raise HTTPException(404, "not found")
    try:
        version_id = UUID(str(job.payload.get("version_id")))
    except (TypeError, ValueError):
        raise HTTPException(404, "not found")
    ver = await db.scalar(
        select(KnowledgeDocumentVersion)
        .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeDocumentVersion.document_id)
        .where(KnowledgeDocumentVersion.id == version_id, KnowledgeDocument.user_id == user.id)
    )
    if not ver:
        raise HTTPException(404, "not found")
    return {
        "id": str(job.id),
        "type": job.type,
        "status": job.status,
        "attempts": job.attempts,
        "last_error": job.last_error,
        "updated_at": job.updated_at,
    }


@app.post("/api/knowledge/documents/{did}/reindex")
async def reindex_document(
    did: str, user=Depends(current_user), db: AsyncSession = Depends(get_session)
):
    from uuid import uuid4

    from oncall.jobs.queue import JobQueue

    doc = await db.scalar(
        select(KnowledgeDocument).where(
            KnowledgeDocument.id == _uuid(did), KnowledgeDocument.user_id == user.id
        )
    )
    if not doc or not doc.active_version_id:
        raise HTTPException(404, "document/version not found")
    job = await JobQueue(db).enqueue(
        "knowledge_reindex",
        {"version_id": str(doc.active_version_id)},
        idempotency_key=f"reindex:{doc.active_version_id}:{uuid4()}",
        priority=40,
    )
    return {"job_id": str(job.id)}


@app.delete("/api/knowledge/documents/{did}")
async def delete_document(
    did: str, user=Depends(current_user), db: AsyncSession = Depends(get_session)
):
    import shutil

    from oncall.rag.milvus_store import MilvusKnowledgeIndex

    doc = await db.scalar(
        select(KnowledgeDocument).where(
            KnowledgeDocument.id == _uuid(did), KnowledgeDocument.user_id == user.id
        )
    )
    if not doc:
        raise HTTPException(404, "not found")
    versions = list(
        (
            await db.scalars(
                select(KnowledgeDocumentVersion).where(
                    KnowledgeDocumentVersion.document_id == doc.id
                )
            )
        ).all()
    )
    index = MilvusKnowledgeIndex()
    try:
        # Delete by logical document in one operation.  Swallowing a Milvus
        # failure here leaves orphan vectors that can still be retrieved after
        # the PostgreSQL document has been deleted.
        await index.delete_document(str(doc.id))
    except Exception as exc:
        logger.exception("failed to remove knowledge vectors document=%s", doc.id)
        raise HTTPException(503, "知识库索引暂时不可用，请稍后重试") from exc
    roots = {__import__("pathlib").Path(v.raw_path).parent.parent for v in versions if v.raw_path}
    version_ids = [str(version.id) for version in versions]
    if version_ids:
        # Durable RAG jobs refer to versions through JSON, so they are invisible
        # to foreign-key cascades and would otherwise retry against deleted rows.
        await db.execute(
            delete(BackgroundJob).where(
                BackgroundJob.payload["version_id"].astext.in_(version_ids)
            )
        )
    await db.delete(doc)
    await db.commit()
    for root in roots:
        try:
            shutil.rmtree(root)
        except FileNotFoundError:
            pass
        except OSError:
            logger.exception("failed to remove knowledge source files root=%s", root)
    return {"ok": True}


@app.get("/api/incidents/{iid}/trace")
async def incident_trace(
    iid: str, user=Depends(current_user), db: AsyncSession = Depends(get_session)
):
    inc = await db.scalar(
        select(Incident)
        .join(Project, Project.id == Incident.project_id)
        .where(Incident.id == _uuid(iid), Project.user_id == user.id)
    )
    if not inc:
        raise HTTPException(404, "not found")
    runs = list(
        (
            await db.scalars(
                select(AgentRun)
                .where(AgentRun.incident_id == inc.id)
                .order_by(AgentRun.started_at.desc())
                .limit(50)
            )
        ).all()
    )
    out = []
    for run in runs:
        tools = list(
            (
                await db.scalars(
                    select(ToolRun)
                    .where(ToolRun.agent_run_id == run.id)
                    .order_by(ToolRun.created_at.asc())
                )
            ).all()
        )
        retrievals = list(
            (
                await db.scalars(
                    select(RetrievalTrace)
                    .where(RetrievalTrace.agent_run_id == run.id)
                    .order_by(RetrievalTrace.created_at.asc())
                )
            ).all()
        )
        out.append(
            {
                "id": str(run.id),
                "mode": run.mode,
                "status": run.status,
                "started_at": run.started_at,
                "finished_at": run.finished_at,
                "tools": [
                    {
                        "tool_name": x.tool_name,
                        "status": x.status,
                        "summary": x.summary,
                        "latency_ms": x.latency_ms,
                        "result_size": x.result_size,
                        "truncated": x.truncated,
                        "error_code": x.error_code,
                        "created_at": x.created_at,
                    }
                    for x in tools
                ],
                "retrievals": [
                    {
                        "query": x.query,
                        "hit_count": x.hit_count,
                        "refs": x.refs,
                        "latency_ms": x.latency_ms,
                        "status": x.status,
                        "error_code": x.error_code,
                        "created_at": x.created_at,
                    }
                    for x in retrievals
                ],
            }
        )
    notes = list(
        (
            await db.scalars(
                select(Notification)
                .where(Notification.incident_id == inc.id)
                .order_by(Notification.created_at.asc())
                .limit(100)
            )
        ).all()
    )
    return {
        "agent_runs": out,
        "notifications": [
            {
                "id": str(n.id),
                "status": n.status,
                "attempts": n.attempts,
                "last_error": n.last_error,
                "payload": n.payload,
                "created_at": n.created_at,
                "sent_at": n.sent_at,
            }
            for n in notes
        ],
    }


@app.post("/api/dev/incidents/trigger")
async def dev_trigger_incident(
    payload: dict, user=Depends(current_user), db: AsyncSession = Depends(get_session)
):
    if s.env.lower() != "development":
        raise HTTPException(404, "not found")
    from uuid import UUID, uuid4

    from oncall.application.incident_service import IncidentService

    try:
        pid = UUID(str(payload.get("project_id")))
    except (ValueError, TypeError):
        raise HTTPException(400, "invalid project_id")
    project = await ProjectService(db).get(pid, user.id)
    if not project:
        raise HTTPException(404, "project not found")
    metric_key = str(payload.get("metric_key") or "OncallDevelopmentAlert")
    value = float(payload.get("value", 1))
    severity = str(payload.get("severity") or "warning")
    fp = f"dev-{uuid4()}"
    created = await IncidentService(db).on_alertmanager_webhook(
        {
            "status": "firing",
            "alerts": [
                {
                    "status": "firing",
                    "labels": {
                        "project_id": str(pid),
                        "alertname": metric_key,
                        "severity": severity,
                        "category": "development",
                    },
                    "annotations": {
                        "summary": str(payload.get("summary") or metric_key),
                        "description": "开发环境模拟的 Alertmanager 告警",
                    },
                    "value": str(value),
                    "fingerprint": fp,
                }
            ],
        }
    )
    if not created:
        raise HTTPException(400, "unable to create development alert")
    inc = created[0]
    conv = await db.scalar(
        select(Conversation)
        .where(Conversation.incident_id == inc.id)
        .order_by(Conversation.created_at.asc())
        .limit(1)
    )
    return {
        "incident_id": str(inc.id),
        "conversation_id": str(conv.id) if conv else None,
        "status": inc.status,
    }


@app.post("/api/dev/incidents/{iid}/recover")
async def dev_recover_incident(
    iid: str, user=Depends(current_user), db: AsyncSession = Depends(get_session)
):
    if s.env.lower() != "development":
        raise HTTPException(404, "not found")
    from oncall.application.incident_service import IncidentService

    inc = await db.scalar(
        select(Incident)
        .join(Project, Project.id == Incident.project_id)
        .where(Incident.id == _uuid(iid), Project.user_id == user.id)
    )
    if not inc:
        raise HTTPException(404, "not found")
    await IncidentService(db).resolve(inc.id, "development_smoke_recovered")
    return {"ok": True, "status": "resolved"}


@app.get("/api/settings/readiness")
async def settings_readiness(user=Depends(current_user)):
    return {
        "environment": s.env,
        "llm": {
            "provider": s.model_provider,
            "model": s.model_name,
            "configured": s.model_provider == "mock" or bool(s.model_api_key),
        },
        "embedding": {
            "model": s.embedding_model,
            "configured": bool(s.embedding_base_url and s.embedding_api_key),
        },
        "rerank": {
            "model": s.rerank_model or None,
            "configured": bool(s.rerank_base_url and s.rerank_api_key and s.rerank_model),
        },
        # A default receive_id is optional: inbound messages auto-bind the
        # latest Feishu chat for proactive delivery. Requiring it here made a
        # working bot appear unconfigured in the Settings page.
        "feishu": {
            "enabled": s.feishu_enabled,
            "configured": bool(s.feishu_app_id and s.feishu_app_secret),
            "default_receive_id_configured": bool(s.feishu_default_receive_id),
            "auto_bind_supported": True,
        },
        "security": {"secret_master_key_configured": bool(s.secret_master_key)},
        "storage": {
            "database": "postgresql" if s.database_url.startswith("postgresql") else "other",
            "milvus_uri": s.milvus_uri,
            "data_dir": str(s.data_dir),
        },
    }


@app.get("/api/settings/models")
async def model_settings(user=Depends(current_user)):
    return {
        "model_provider": s.model_provider,
        "model_display_name": s.model_display_name,
        "model_base_url": s.model_base_url,
        "model_name": s.model_name,
        "model_api_key_configured": bool(s.model_api_key),
        "embedding_base_url": s.embedding_base_url,
        "embedding_model": s.embedding_model,
        "embedding_api_key_configured": bool(s.embedding_api_key),
        "rerank_base_url": s.rerank_base_url,
        "rerank_model": s.rerank_model,
        "rerank_api_key_configured": bool(s.rerank_api_key),
    }


@app.put("/api/settings/models")
async def update_model_settings(dto: ModelSettingsDTO, user=Depends(current_user)):
    values = {
        "ONCALL_MODEL_PROVIDER": dto.model_provider,
        "ONCALL_MODEL_DISPLAY_NAME": dto.model_display_name.strip(),
        "ONCALL_MODEL_BASE_URL": dto.model_base_url.strip().rstrip("/"),
        "ONCALL_MODEL_NAME": dto.model_name.strip(),
        "ONCALL_EMBEDDING_BASE_URL": dto.embedding_base_url.strip().rstrip("/"),
        "ONCALL_EMBEDDING_MODEL": dto.embedding_model.strip(),
        "ONCALL_RERANK_BASE_URL": dto.rerank_base_url.strip(),
        "ONCALL_RERANK_MODEL": dto.rerank_model.strip(),
    }
    secrets = {
        "model_api_key": "ONCALL_MODEL_API_KEY",
        "embedding_api_key": "ONCALL_EMBEDDING_API_KEY",
        "rerank_api_key": "ONCALL_RERANK_API_KEY",
    }
    for field, env_key in secrets.items():
        value = getattr(dto, field)
        if value and value.strip():
            values[env_key] = value.strip()

    update_env_values(values)
    # Keep this API process in sync immediately; local workers observe the
    # changed .env mtime when they next load model settings.
    for field, env_key in {
        "model_provider": "ONCALL_MODEL_PROVIDER",
        "model_display_name": "ONCALL_MODEL_DISPLAY_NAME",
        "model_base_url": "ONCALL_MODEL_BASE_URL",
        "model_name": "ONCALL_MODEL_NAME",
        "embedding_base_url": "ONCALL_EMBEDDING_BASE_URL",
        "embedding_model": "ONCALL_EMBEDDING_MODEL",
        "rerank_base_url": "ONCALL_RERANK_BASE_URL",
        "rerank_model": "ONCALL_RERANK_MODEL",
    }.items():
        setattr(s, field, values[env_key])
    for field, env_key in secrets.items():
        if env_key in values:
            setattr(s, field, values[env_key])
    return {
        "ok": True,
        "message": "模型配置已保存并立即生效。",
        "worker_restart_required": False,
    }


def _model_profile_box() -> SecretBox:
    return SecretBox(get_settings().secret_master_key)


def _model_profile_view(row: ModelProfile) -> dict:
    return {
        "id": str(row.id),
        "kind": row.kind,
        "name": row.name,
        "provider": row.provider,
        "base_url": row.base_url,
        "model": row.model,
        "api_key_configured": bool(row.encrypted_api_key),
        "embedding_dimension": (row.capabilities or {}).get("embedding_dimension"),
        "active": row.enabled,
        "created_at": row.created_at,
    }


def _current_model_config() -> dict:
    # This is the runtime config loaded from .env. It is intentionally not
    # presented as a saved profile: only profiles added by the user are history.
    current = get_settings()
    return {
        "llm": {
            "kind": "llm", "name": current.model_display_name, "provider": current.model_provider,
            "base_url": current.model_base_url, "model": current.model_name,
            "api_key_configured": bool(current.model_api_key),
        },
        "embedding": {
            "kind": "embedding", "name": "Embedding", "provider": "openai-compatible",
            "base_url": current.embedding_base_url, "model": current.embedding_model,
            "api_key_configured": bool(current.embedding_api_key),
            "embedding_dimension": current.embedding_dimension,
        },
        "rerank": {
            "kind": "rerank", "name": "Rerank", "provider": "openai-compatible",
            "base_url": current.rerank_base_url, "model": current.rerank_model,
            "api_key_configured": bool(current.rerank_api_key),
        },
    }


def _apply_model_profile(row: ModelProfile) -> None:
    key = _model_profile_box().decrypt(row.encrypted_api_key)
    if row.kind == "llm":
        values = {
            "ONCALL_MODEL_PROVIDER": row.provider,
            "ONCALL_MODEL_DISPLAY_NAME": row.name,
            "ONCALL_MODEL_BASE_URL": row.base_url,
            "ONCALL_MODEL_NAME": row.model,
            "ONCALL_MODEL_API_KEY": key,
        }
        for field, value in {
            "model_provider": row.provider, "model_display_name": row.name,
            "model_base_url": row.base_url, "model_name": row.model, "model_api_key": key,
        }.items():
            setattr(s, field, value)
    elif row.kind == "embedding":
        dimension = int((row.capabilities or {}).get("embedding_dimension") or s.embedding_dimension)
        values = {
            "ONCALL_EMBEDDING_BASE_URL": row.base_url,
            "ONCALL_EMBEDDING_MODEL": row.model,
            "ONCALL_EMBEDDING_API_KEY": key,
            "ONCALL_EMBEDDING_DIMENSION": str(dimension),
        }
        for field, value in {
            "embedding_base_url": row.base_url, "embedding_model": row.model,
            "embedding_api_key": key, "embedding_dimension": dimension,
        }.items():
            setattr(s, field, value)
    else:
        values = {
            "ONCALL_RERANK_BASE_URL": row.base_url,
            "ONCALL_RERANK_MODEL": row.model,
            "ONCALL_RERANK_API_KEY": key,
        }
        for field, value in {
            "rerank_base_url": row.base_url, "rerank_model": row.model, "rerank_api_key": key,
        }.items():
            setattr(s, field, value)
    update_env_values(values)


async def _activate_model_profile(db: AsyncSession, row: ModelProfile) -> int:
    reindex_queued = 0
    previous_embedding_dimension = get_settings().embedding_dimension
    if row.kind == "embedding" and not (row.capabilities or {}).get("embedding_dimension"):
        raise HTTPException(400, "请先测试 Embedding 连接；系统需要识别向量维度后才能启用。")
    db.add(row)
    await db.flush()
    active_rows = list((await db.scalars(
        select(ModelProfile).where(ModelProfile.kind == row.kind, ModelProfile.enabled.is_(True))
    )).all())
    for active in active_rows:
        if active.id != row.id:
            active.enabled = False
    await db.flush()
    row.enabled = True
    await db.commit()
    _apply_model_profile(row)
    if row.kind == "embedding":
        new_dimension = int((row.capabilities or {})["embedding_dimension"])
        if new_dimension != previous_embedding_dimension:
            from uuid import uuid4

            from oncall.jobs.queue import JobQueue
            from oncall.rag.milvus_store import MilvusKnowledgeIndex

            # Milvus vector dimensions are fixed at collection creation. The
            # index is derived data; PostgreSQL documents remain the source of
            # truth and are queued for a complete rebuild at the new dimension.
            await MilvusKnowledgeIndex().reset_collection()
            version_ids = list((await db.scalars(
                select(KnowledgeDocument.active_version_id).where(
                    KnowledgeDocument.active_version_id.is_not(None)
                )
            )).all())
            queue = JobQueue(db)
            for version_id in version_ids:
                await queue.enqueue(
                    "knowledge_reindex",
                    {"version_id": str(version_id)},
                    idempotency_key=f"embedding-reindex:{new_dimension}:{version_id}:{uuid4()}",
                    priority=20,
                    commit=False,
                )
            await db.commit()
            reindex_queued = len(version_ids)
    return reindex_queued


async def _profile_api_key(dto: ModelProbeDTO, db: AsyncSession) -> str:
    key = (dto.api_key or "").strip()
    if key:
        return key
    if dto.profile_id:
        profile = await db.get(ModelProfile, dto.profile_id)
        if profile and profile.kind == dto.service:
            return _model_profile_box().decrypt(profile.encrypted_api_key)
    current = get_settings()
    return {
        "llm": current.model_api_key,
        "embedding": current.embedding_api_key,
        "rerank": current.rerank_api_key,
    }[dto.service]


@app.get("/api/model-profiles")
async def list_model_profiles(user=Depends(current_user), db: AsyncSession = Depends(get_session)):
    rows = list((await db.scalars(select(ModelProfile).order_by(ModelProfile.created_at.desc()))).all())
    return {"profiles": [_model_profile_view(row) for row in rows], "current": _current_model_config()}


@app.post("/api/model-profiles")
async def create_model_profile(
    dto: ModelProfileDTO, user=Depends(current_user), db: AsyncSession = Depends(get_session)
):
    if dto.kind != "llm" and dto.provider == "mock":
        raise HTTPException(400, "Mock 类型只适用于大语言模型")
    row = ModelProfile(
        kind=dto.kind,
        name=dto.name.strip(),
        provider=dto.provider,
        base_url=dto.base_url.strip().rstrip("/"),
        model=dto.model.strip(),
        encrypted_api_key=_model_profile_box().encrypt(dto.api_key.strip()) if dto.api_key and dto.api_key.strip() else None,
        capabilities={"embedding_dimension": dto.embedding_dimension} if dto.kind == "embedding" and dto.embedding_dimension else {},
        enabled=False,
    )
    if dto.activate:
        reindex_queued = await _activate_model_profile(db, row)
    else:
        reindex_queued = 0
        db.add(row)
        await db.commit()
        await db.refresh(row)
    return _model_profile_view(row) | {"reindex_queued": reindex_queued}


@app.put("/api/model-profiles/{profile_id}")
async def update_model_profile(
    profile_id: str,
    dto: ModelProfileDTO,
    user=Depends(current_user),
    db: AsyncSession = Depends(get_session),
):
    row = await db.get(ModelProfile, _uuid(profile_id, "model profile"))
    if not row:
        raise HTTPException(404, "model profile not found")
    if dto.kind != "llm" and dto.provider == "mock":
        raise HTTPException(400, "Mock 类型只适用于大语言模型")
    if dto.kind != row.kind:
        raise HTTPException(400, "模型用途不能修改；请新建对应用途的模型连接。")
    row.kind = dto.kind
    row.name = dto.name.strip()
    row.provider = dto.provider
    row.base_url = dto.base_url.strip().rstrip("/")
    row.model = dto.model.strip()
    row.capabilities = (
        {"embedding_dimension": dto.embedding_dimension}
        if row.kind == "embedding" and dto.embedding_dimension
        else (row.capabilities or {})
    )
    if dto.clear_api_key:
        row.encrypted_api_key = None
    elif dto.api_key and dto.api_key.strip():
        row.encrypted_api_key = _model_profile_box().encrypt(dto.api_key.strip())
    activate = dto.activate or row.enabled
    if activate:
        reindex_queued = await _activate_model_profile(db, row)
    else:
        reindex_queued = 0
        await db.commit()
        await db.refresh(row)
    return _model_profile_view(row) | {"reindex_queued": reindex_queued}


@app.post("/api/model-profiles/{profile_id}/activate")
async def activate_model_profile(
    profile_id: str, user=Depends(current_user), db: AsyncSession = Depends(get_session)
):
    row = await db.get(ModelProfile, _uuid(profile_id, "model profile"))
    if not row:
        raise HTTPException(404, "model profile not found")
    reindex_queued = await _activate_model_profile(db, row)
    message = f"已切换当前{ {'llm': '大语言模型', 'embedding': 'Embedding 模型', 'rerank': 'Rerank 模型'}[row.kind] }。"
    if reindex_queued:
        message += f" 已安排 {reindex_queued} 个知识文档重新索引。"
    return {"ok": True, "message": message, "reindex_queued": reindex_queued}


@app.delete("/api/model-profiles/{profile_id}")
async def delete_model_profile(
    profile_id: str, user=Depends(current_user), db: AsyncSession = Depends(get_session)
):
    row = await db.get(ModelProfile, _uuid(profile_id, "model profile"))
    if not row:
        raise HTTPException(404, "model profile not found")
    if row.enabled:
        raise HTTPException(409, "当前正在使用此模型；请先切换到其他配置，再删除。")
    await db.delete(row)
    await db.commit()
    return {"ok": True}


def _model_api_error(status_code: int) -> str:
    if status_code in (401, 403):
        reason = "API Key 无效或没有调用权限"
    elif status_code == 402:
        reason = "服务商账户余额或计费状态异常"
    elif status_code == 429:
        reason = "调用频率受限或模型额度不足"
    elif status_code == 404:
        reason = "接口地址或模型名称不存在"
    elif status_code >= 500:
        reason = "模型服务商暂时不可用"
    else:
        reason = "请求配置错误"
    return f"模型服务异常：{reason}（HTTP {status_code}）"


def _model_endpoints(base_url: str, suffix: str) -> list[str]:
    base = base_url.strip().rstrip("/")
    paths = [base]
    if not base.endswith("/v1"):
        paths.append(base + "/v1")
    return [f"{path}/{suffix.lstrip('/')}" for path in paths]


@app.post("/api/settings/models/test")
async def test_model_connection(
    dto: ModelProbeDTO, user=Depends(current_user), db: AsyncSession = Depends(get_session)
):
    key = await _profile_api_key(dto, db)
    if not key:
        return {"ok": False, "message": "请先填写该模型服务的 API Key。"}
    headers = {"Authorization": f"Bearer {key}"}
    try:
        embedding_dimension = None
        async with httpx.AsyncClient(timeout=20) as client:
            if dto.service == "llm":
                if not dto.model.strip():
                    return {"ok": False, "message": "请先选择或填写模型名称。"}
                response = None
                for endpoint in _model_endpoints(dto.base_url, "chat/completions"):
                    response = await client.post(
                        endpoint,
                        headers=headers,
                        json={
                            "model": dto.model.strip(),
                            "messages": [{"role": "user", "content": "Reply with OK."}],
                            "max_tokens": 4,
                            "temperature": 0,
                        },
                    )
                    if response.status_code != 404:
                        break
            elif dto.service == "embedding":
                if not dto.model.strip():
                    return {"ok": False, "message": "请先选择或填写 Embedding 模型名称。"}
                response = None
                for endpoint in _model_endpoints(dto.base_url, "embeddings"):
                    response = await client.post(
                        endpoint,
                        headers=headers,
                        json={"model": dto.model.strip(), "input": ["connection check"]},
                    )
                    if response.status_code != 404:
                        break
            else:
                if not dto.model.strip():
                    return {"ok": False, "message": "请先选择或填写 Rerank 模型名称。"}
                response = await client.post(
                    dto.base_url.strip(),
                    headers=headers,
                    json={
                        "model": dto.model.strip(),
                        "query": "connection check",
                        "documents": ["connection check"],
                        "top_n": 1,
                    },
                )
            response.raise_for_status()
            if dto.service == "embedding":
                payload = response.json()
                vectors = payload.get("data", []) if isinstance(payload, dict) else []
                vector = vectors[0].get("embedding") if vectors and isinstance(vectors[0], dict) else None
                if not isinstance(vector, list) or not vector:
                    return {"ok": False, "message": "Embedding 服务已响应，但没有返回有效向量。"}
                embedding_dimension = len(vector)
        return {
            "ok": True,
            "message": (
                f"连接成功，已识别向量维度 {embedding_dimension}。"
                if embedding_dimension
                else "连接成功，模型服务已响应。"
            ),
            "embedding_dimension": embedding_dimension,
        }
    except httpx.HTTPStatusError as exc:
        return {"ok": False, "message": _model_api_error(exc.response.status_code)}
    except httpx.TimeoutException:
        return {"ok": False, "message": "模型服务异常：连接超时，请检查 Endpoint 和网络。"}
    except httpx.RequestError:
        return {"ok": False, "message": "模型服务异常：无法连接，请检查 Endpoint 和网络。"}
    except Exception:
        return {"ok": False, "message": "模型服务异常：测试请求失败，请检查 Endpoint、模型名称和请求格式。"}


@app.post("/api/settings/models/discover")
async def discover_models(
    dto: ModelProbeDTO, user=Depends(current_user), db: AsyncSession = Depends(get_session)
):
    key = await _profile_api_key(dto, db)
    if not key:
        return {"ok": False, "message": "请先填写该模型服务的 API Key。", "models": []}
    try:
        discover_base = dto.base_url.strip().rstrip("/")
        if dto.service == "rerank" and discover_base.endswith("/rerank"):
            discover_base = discover_base[: -len("/rerank")]
        async with httpx.AsyncClient(timeout=20) as client:
            response = None
            for endpoint in _model_endpoints(discover_base, "models"):
                response = await client.get(endpoint, headers={"Authorization": f"Bearer {key}"})
                if response.status_code != 404:
                    break
            response.raise_for_status()
        payload = response.json()
        data = payload.get("data", []) if isinstance(payload, dict) else []
        names = sorted(
            {
                str(item.get("id") or item.get("name") or "").strip()
                for item in data
                if isinstance(item, dict) and (item.get("id") or item.get("name"))
            }
        )
        return {
            "ok": True,
            "models": names,
            "message": f"发现 {len(names)} 个模型。" if names else "服务响应正常，但没有返回可选模型。",
        }
    except httpx.HTTPStatusError as exc:
        return {"ok": False, "message": _model_api_error(exc.response.status_code), "models": []}
    except httpx.TimeoutException:
        return {"ok": False, "message": "模型服务异常：发现模型请求超时。", "models": []}
    except httpx.RequestError:
        return {"ok": False, "message": "模型服务异常：无法连接，请检查 Endpoint 和网络。", "models": []}
    except Exception:
        return {"ok": False, "message": "发现模型失败，请确认服务商支持 OpenAI 兼容的 /models 接口。", "models": []}
@app.get("/api/settings/feishu")
async def feishu_settings(user=Depends(current_user)):
    return {
        "enabled": s.feishu_enabled,
        "app_id": s.feishu_app_id,
        "app_secret_configured": bool(s.feishu_app_secret),
        "default_receive_id": s.feishu_default_receive_id,
        "default_receive_id_type": s.feishu_default_receive_id_type,
        "restart_required": True,
    }


@app.put("/api/settings/feishu")
async def update_feishu_settings(dto: FeishuSettingsDTO, user=Depends(current_user)):
    app_id = dto.app_id.strip()
    app_secret = (dto.app_secret or "").strip() or s.feishu_app_secret
    if dto.enabled and (not app_id or not app_secret):
        raise HTTPException(400, "启用飞书时必须填写 App ID 和 App Secret")
    if dto.enabled:
        import httpx

        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.post(
                    "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                    json={"app_id": app_id, "app_secret": app_secret},
                )
            body = response.json()
        except Exception as exc:
            raise HTTPException(400, f"飞书凭证验证失败：{exc}")
        if (
            response.status_code >= 400
            or body.get("code") != 0
            or not body.get("tenant_access_token")
        ):
            raise HTTPException(400, f"飞书凭证验证失败：{body.get('msg') or 'unknown error'}")
    update_env_values(
        {
            "ONCALL_FEISHU_ENABLED": str(dto.enabled).lower(),
            "ONCALL_FEISHU_APP_ID": app_id,
            "ONCALL_FEISHU_APP_SECRET": app_secret,
            "ONCALL_FEISHU_DEFAULT_RECEIVE_ID": dto.default_receive_id.strip(),
            "ONCALL_FEISHU_DEFAULT_RECEIVE_ID_TYPE": dto.default_receive_id_type,
        }
    )
    return {
        "ok": True,
        "message": "飞书配置已保存，重启 API 和 Agent Worker 后生效",
        "restart_required": True,
    }


@app.get("/api/settings/tool-contracts")
async def settings_tool_contracts(user=Depends(current_user)):
    from oncall.agent.tool_contracts import public_tool_specs

    return {"tools": public_tool_specs()}


def run():
    uvicorn.run("oncall.api.main:app", host=s.host, port=s.port, reload=False)
