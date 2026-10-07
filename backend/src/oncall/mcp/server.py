"""One tool server shared by in-process and Streamable HTTP transports."""

import logging
from collections.abc import Awaitable, Callable

from mcp.server.lowlevel import Server
from mcp.types import CallToolResult, TextContent, Tool, ToolAnnotations

from oncall.domain.schemas import ToolResult
from oncall.mcp.contracts import TOOL_SPECS

ToolExecutor = Callable[[str, dict], Awaitable[ToolResult]]
logger = logging.getLogger(__name__)


def mcp_tool_specs() -> list[Tool]:
    return [
        Tool(
            name=name,
            description=spec["description"],
            inputSchema=spec["parameters"],
            outputSchema=ToolResult.model_json_schema(),
            annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False),
        )
        for name, spec in TOOL_SPECS.items()
    ]


def create_tool_server(execute: ToolExecutor) -> Server:
    server = Server("pulseops-diagnostics", version="1.0.0")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return mcp_tool_specs()

    # Backend validation retains structured error codes and audited failures.
    @server.call_tool(validate_input=False)
    async def call_tool(name: str, arguments: dict) -> CallToolResult:
        try:
            result = await execute(name, arguments)
        except Exception:
            logger.exception("MCP tool execution failed", extra={"tool_name": name})
            result = ToolResult(ok=False, summary="MCP 工具执行失败", error_code="MCP_ERROR")
        payload = result.model_dump(mode="json")
        return CallToolResult(
            content=[TextContent(type="text", text=result.model_dump_json())],
            structuredContent=payload,
            isError=not result.ok,
        )

    return server
