"""Agent client using the SDK's MCP transport, with no extra process or port."""

from datetime import timedelta

from mcp.shared.memory import create_connected_server_and_client_session
from sqlalchemy.ext.asyncio import AsyncSession

from oncall.domain.schemas import ToolResult
from oncall.mcp.backend import DiagnosticTools, ToolExecutionContext
from oncall.mcp.server import create_tool_server


class MCPToolClient:
    def __init__(self, session: AsyncSession):
        self.backend = DiagnosticTools(session)

    async def list_tools(self) -> list[dict]:
        async def no_execution(name: str, args: dict) -> ToolResult:
            raise RuntimeError("Discovery session cannot execute tools")

        async with create_connected_server_and_client_session(
            create_tool_server(no_execution)
        ) as client:
            listed = await client.list_tools()
            return [
                {"name": t.name, "description": t.description, "parameters": t.inputSchema}
                for t in listed.tools
            ]

    async def execute(
        self, name: str, args: dict, ctx: ToolExecutionContext, timeout: float = 15
    ) -> ToolResult:
        async def execute_scoped(tool_name: str, arguments: dict) -> ToolResult:
            return await self.backend.execute(tool_name, arguments, ctx, timeout=timeout)

        async with create_connected_server_and_client_session(
            create_tool_server(execute_scoped),
            read_timeout_seconds=timedelta(seconds=timeout + 5),
        ) as client:
            result = await client.call_tool(name, args)
            if result.structuredContent is None:
                return ToolResult(ok=False, summary="MCP 工具返回格式无效", error_code="MCP_ERROR")
            return ToolResult.model_validate(result.structuredContent)
