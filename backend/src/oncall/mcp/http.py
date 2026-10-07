"""Authenticated MCP endpoint hosted by the existing API service."""

import hmac
import logging
from contextlib import asynccontextmanager
from datetime import datetime

from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse

from oncall.bootstrap.config import get_settings
from oncall.domain.schemas import ToolResult
from oncall.infrastructure.db.models import AgentRun, Conversation, Incident, Project
from oncall.infrastructure.db.session import SessionFactory
from oncall.mcp.backend import DiagnosticTools, ToolExecutionContext
from oncall.mcp.server import create_tool_server

logger = logging.getLogger(__name__)


async def execute_external(name: str, args: dict) -> ToolResult:
    from oncall.application.workspace_service import ensure_local_user

    settings = get_settings()
    async with SessionFactory() as db:
        user = await ensure_local_user(db)
        conversation = await db.get(Conversation, settings.mcp_conversation_id)
        if not conversation or conversation.user_id != user.id or conversation.archived:
            return ToolResult(ok=False, summary="MCP 绑定会话不可用", error_code="MCP_SCOPE_INVALID")
        project_id = conversation.project_id
        incident_id = conversation.incident_id
        if incident_id:
            incident = await db.get(Incident, incident_id)
            if not incident or (project_id and incident.project_id != project_id):
                return ToolResult(ok=False, summary="MCP 绑定事件不可用", error_code="MCP_SCOPE_INVALID")
            project_id = incident.project_id
        if project_id:
            project = await db.get(Project, project_id)
            if not project or project.user_id != user.id:
                return ToolResult(ok=False, summary="MCP 绑定项目不可用", error_code="MCP_SCOPE_INVALID")
        run = AgentRun(
            conversation_id=conversation.id,
            incident_id=incident_id,
            mode="mcp",
            model_profile="external-mcp",
            status="running",
        )
        db.add(run)
        await db.commit()
        await db.refresh(run)
        run_id = run.id
        try:
            result = await DiagnosticTools(db).execute(
                name, args, ToolExecutionContext(project_id, incident_id, run_id)
            )
        except Exception:
            await db.rollback()
            logger.exception("external MCP execution failed")
            result = ToolResult(ok=False, summary="MCP 工具执行失败", error_code="MCP_ERROR")
        run = await db.get(AgentRun, run_id)
        if run:
            run.status = "completed" if result.ok else "failed"
            run.finished_at = datetime.now().astimezone()
            await db.commit()
        return result


class MCPHttpEndpoint:
    def __init__(self):
        self.manager: StreamableHTTPSessionManager | None = None

    @asynccontextmanager
    async def lifespan(self):
        settings = get_settings()
        manager = StreamableHTTPSessionManager(
            create_tool_server(execute_external),
            stateless=True,
            json_response=True,
            security_settings=TransportSecuritySettings(
                allowed_hosts=settings.mcp_allowed_hosts,
                allowed_origins=[settings.web_origin],
            ),
        )
        self.manager = manager
        try:
            async with manager.run():
                yield
        finally:
            self.manager = None

    async def __call__(self, scope, receive, send):
        settings = get_settings()
        request = Request(scope, receive)
        if not settings.mcp_access_token or not settings.mcp_conversation_id:
            await JSONResponse({"detail": "External MCP is not configured"}, status_code=503)(
                scope, receive, send
            )
            return
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(
            token.encode(), settings.mcp_access_token.encode()
        ):
            await JSONResponse(
                {"detail": "Invalid MCP credential"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )(scope, receive, send)
            return
        if self.manager is None:
            await JSONResponse({"detail": "MCP is not ready"}, status_code=503)(scope, receive, send)
            return
        await self.manager.handle_request(scope, receive, send)


mcp_http = MCPHttpEndpoint()
