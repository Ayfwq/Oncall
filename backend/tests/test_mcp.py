import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from oncall.domain.schemas import ToolResult
from oncall.infrastructure.db.models import Conversation, Incident, Project
from oncall.mcp.backend import ToolExecutionContext
from oncall.mcp.client import MCPToolClient
from oncall.mcp.contracts import ALLOWED_TOOLS, TOOL_SPECS, public_tool_specs, validate_tool_args
from oncall.mcp.http import MCPHttpEndpoint, execute_external
from starlette.applications import Starlette
from starlette.routing import Route


def scoped_client():
    db = SimpleNamespace(scalar=AsyncMock(return_value=None))
    return MCPToolClient(db), ToolExecutionContext(uuid4(), uuid4(), uuid4())


@pytest.mark.asyncio
async def test_agent_discovers_and_calls_over_mcp_with_injected_scope():
    client, scope = scoped_client()
    dispatched = AsyncMock(return_value=ToolResult(ok=True, summary="正常", data={"cpu": 42}))
    client.backend._dispatch = dispatched
    tools = await client.list_tools()
    assert {t["name"] for t in tools} == ALLOWED_TOOLS
    result = await client.execute("query_current_metrics", {}, scope)
    assert result.ok and result.data == {"cpu": 42}
    dispatched.assert_awaited_once_with("query_current_metrics", {}, scope)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name,args,error",
    [
        ("restart_container", {}, "TOOL_NOT_ALLOWED"),
        ("query_current_metrics", {"project_id": str(uuid4())}, "INVALID_TOOL_ARGS"),
        ("search_logs", {"limit": 1001}, "INVALID_TOOL_ARGS"),
    ],
)
async def test_mcp_rejects_unknown_tools_and_scope_override(name, args, error):
    client, scope = scoped_client()
    client.backend._dispatch = AsyncMock()
    result = await client.execute(name, args, scope)
    assert not result.ok and result.error_code == error
    client.backend._dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_mcp_preserves_backend_timeout_and_project_requirement():
    client, scope = scoped_client()

    async def slow(*args):
        await asyncio.sleep(1)

    client.backend._dispatch = slow
    result = await client.execute("query_current_metrics", {}, scope, timeout=0.01)
    assert result.error_code == "TIMEOUT"
    result = await client.execute(
        "query_current_metrics", {}, ToolExecutionContext(None, None, uuid4())
    )
    assert result.error_code == "PROJECT_REQUIRED"


@pytest.mark.asyncio
async def test_streamable_http_auth_discovery_call_and_host_guard(monkeypatch):
    settings = SimpleNamespace(
        mcp_access_token="test-secret",
        mcp_conversation_id=uuid4(),
        mcp_allowed_hosts=["testserver"],
        web_origin="http://testserver",
    )
    monkeypatch.setattr("oncall.mcp.http.get_settings", lambda: settings)
    execute = AsyncMock(return_value=ToolResult(ok=True, summary="查询完成", data={"lines": []}))
    monkeypatch.setattr("oncall.mcp.http.execute_external", execute)
    endpoint = MCPHttpEndpoint()
    app = Starlette(routes=[Route("/api/mcp", endpoint, methods=["GET", "POST", "DELETE"])])
    async with endpoint.lifespan(), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as http:
        assert (await http.post("/api/mcp")).status_code == 401
        http.headers["Authorization"] = "Bearer wrong-secret"
        assert (await http.post("/api/mcp")).status_code == 401
        http.headers["Authorization"] = "Bearer test-secret"
        response = await http.post("/api/mcp", headers={"Host": "evil.example"}, json={})
        assert response.status_code == 421
        async with streamable_http_client("http://testserver/api/mcp", http_client=http) as streams:
            async with ClientSession(streams[0], streams[1]) as client:
                await client.initialize()
                listed = await client.list_tools()
                assert {t.name for t in listed.tools} == ALLOWED_TOOLS
                assert all(t.annotations.readOnlyHint for t in listed.tools)
                result = await client.call_tool("search_logs", {"query": "ERROR"})
                assert not result.isError and result.structuredContent["ok"]
        execute.assert_awaited_once_with("search_logs", {"query": "ERROR"})
        settings.mcp_access_token = ""
        assert (await http.post("/api/mcp")).status_code == 503


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_scope", ["owner", "archived", "incident", "project_owner"])
async def test_external_mcp_refuses_invalid_bound_scope(monkeypatch, invalid_scope):
    from contextlib import asynccontextmanager

    user_id, conversation_id, project_id, incident_id = (uuid4() for _ in range(4))
    conversation = SimpleNamespace(
        user_id=uuid4() if invalid_scope == "owner" else user_id,
        archived=invalid_scope == "archived",
        project_id=project_id,
        incident_id=incident_id,
    )
    incident = SimpleNamespace(project_id=uuid4() if invalid_scope == "incident" else project_id)
    project = SimpleNamespace(user_id=uuid4() if invalid_scope == "project_owner" else user_id)
    db = SimpleNamespace(
        get=AsyncMock(side_effect=lambda model, identity: {
            Conversation: conversation, Incident: incident, Project: project
        }[model]),
        add=AsyncMock(),
    )

    @asynccontextmanager
    async def factory():
        yield db

    monkeypatch.setattr("oncall.mcp.http.SessionFactory", factory)
    monkeypatch.setattr("oncall.mcp.http.get_settings", lambda: SimpleNamespace(
        mcp_conversation_id=conversation_id
    ))
    monkeypatch.setattr("oncall.application.workspace_service.ensure_local_user", AsyncMock(
        return_value=SimpleNamespace(id=user_id)
    ))
    result = await execute_external("query_current_metrics", {})
    assert result.error_code == "MCP_SCOPE_INVALID"
    db.add.assert_not_called()


def test_every_tool_has_llm_visible_description_and_json_schema():
    assert set(TOOL_SPECS) == set(ALLOWED_TOOLS)
    for name, spec in TOOL_SPECS.items():
        assert spec["description"].strip(), name
        schema = spec["parameters"]
        assert schema["type"] == "object"
        # Runtime injects scope; the LLM is not allowed to choose it.
        assert "project_id" not in schema.get("properties", {})
        assert "incident_id" not in schema.get("properties", {})
        assert schema.get("additionalProperties") is False
    assert len(public_tool_specs()) == 8


def test_tool_args_reject_scope_escape_and_bad_types():
    ok, _ = validate_tool_args("query_current_metrics", {"project_id": "escape"})
    assert not ok
    ok, _ = validate_tool_args(
        "query_metric_history", {"metrics": ["host.cpu.percent"], "minutes": 0}
    )
    assert not ok
    ok, _ = validate_tool_args("query_metric_history", {"metrics": [30]})
    assert not ok
    ok, _ = validate_tool_args("query_database_health", {"checks": ["not-a-check"]})
    assert not ok
    ok, _ = validate_tool_args("query_runtime_resources", {"checks": ["cpu", "cpu"]})
    assert not ok


def test_tool_args_accept_contract_defaults_and_required_values():
    assert validate_tool_args("query_current_metrics", {}) == (True, None)
    assert validate_tool_args(
        "query_metric_history", {"metrics": ["host.cpu.percent"], "minutes": 30}
    ) == (True, None)
    assert validate_tool_args(
        "query_runtime_resources", {"checks": ["cpu", "memory"], "top_n": 5}
    ) == (True, None)
    assert validate_tool_args("search_knowledge", {"query": "CPU 高 处理方案", "top_k": 5}) == (
        True,
        None,
    )


def test_has_only_remote_read_tools():
    assert ALLOWED_TOOLS == {
        "query_active_alerts",
        "query_incident_context",
        "query_current_metrics",
        "query_metric_history",
        "search_knowledge",
        "search_logs",
        "query_database_health",
        "query_runtime_resources",
    }
