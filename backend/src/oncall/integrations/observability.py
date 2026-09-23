from __future__ import annotations

import asyncio
import re
from urllib.parse import quote

import httpx

from oncall.application.dtos import DatabaseProfileDTO, LogSourceDTO, MonitoredServerDTO
from oncall.domain.schemas import ToolResult
from oncall.integrations.base import CollectResult


class RemoteObservabilityIntegration:
    name = "observability"

    def __init__(
        self,
        server: MonitoredServerDTO | None,
        logs: list[LogSourceDTO],
        databases: list[DatabaseProfileDTO],
    ):
        self.server = server
        self.logs = [x for x in logs if x.enabled]
        self.databases = [x for x in databases if x.enabled]

    def _headers(self) -> dict[str, str]:
        return {"X-Oncall-Token": self.server.collector_token or ""} if self.server else {}

    def _dsn(self, p: DatabaseProfileDTO) -> str:
        user = quote(p.username, safe="")
        password = quote(p.password or "", safe="")
        return f"postgresql://{user}:{password}@{p.host}:{p.port}/{quote(p.database, safe='')}?sslmode={p.sslmode}"

    async def _post(self, path: str, body: dict) -> dict:
        if not self.server or not self.server.collector_url:
            return {"ok": False, "error": "服务器未配置 PulseOps Collector"}
        async with httpx.AsyncClient(timeout=6, trust_env=False) as client:
            response = await client.post(
                self.server.collector_url.rstrip("/") + path, headers=self._headers(), json=body
            )
            response.raise_for_status()
            return response.json()

    async def collect(self) -> CollectResult:
        signals: dict[str, float] = {}
        resources: dict = {}
        errors: list[str] = []

        async def collect_logs() -> tuple[dict, str | None]:
            if not self.logs:
                return {}, None
            src = self.logs[0]
            cfg = src.parser_config or {}
            try:
                result = await self._post(
                    "/v1/logs/search",
                    {
                        "target_urls": cfg.get("target_urls", []),
                        "compose_project": cfg.get("compose_project"),
                        "services": cfg.get("services", []),
                        "since_minutes": 2,
                        "limit": 500,
                    },
                )
            except Exception as exc:  # a dead collector must become an alertable signal
                result = {"ok": False, "lines": [], "error": str(exc)}
            return result, None if result.get("ok") else "日志: " + str(
                result.get("error", "采集失败")
            )

        async def collect_database() -> tuple[dict, str | None]:
            if not self.databases:
                return {}, None
            try:
                result = await self._post(
                    "/v1/database/diagnose", {"dsn": self._dsn(self.databases[0])}
                )
            except Exception as exc:  # keep DB availability independent from log collection
                result = {"ok": False, "signals": {"db.up": 0.0}, "error": str(exc)}
            return result, None if result.get("ok") else "数据库: " + str(
                result.get("error", "采集失败")
            )

        log_pair, database_pair = await asyncio.gather(collect_logs(), collect_database())
        log_result, log_error = log_pair
        if self.logs:
            resources["logs"] = log_result
            signals.update(
                {
                    "log.collector.up": 1.0 if log_result.get("ok") else 0.0,
                    "log.error_count": float(log_result.get("error_count", 0)),
                    "log.exception_count": float(log_result.get("exception_count", 0)),
                }
            )
            if log_error:
                errors.append(log_error)
        database_result, database_error = database_pair
        if self.databases:
            resources["database"] = database_result
            signals.update({k: float(v) for k, v in (database_result.get("signals") or {}).items()})
            # A malformed collector response must not silently remove db.up.
            signals.setdefault("db.up", 1.0 if database_result.get("ok") else 0.0)
            if database_error:
                errors.append(database_error)
        return CollectResult(
            name=self.name,
            ok=not errors,
            signals=signals,
            resources=resources,
            error="; ".join(errors) or None,
        )

    @staticmethod
    def _signature(message: str) -> str:
        value = re.sub(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F-]{27,}\b", "<uuid>", message)
        value = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "<ip>", value)
        value = re.sub(r"\b\d+\b", "<n>", value)
        return value[:300]

    async def search_logs(
        self,
        query: str = "",
        level: str | None = None,
        since_minutes: int = 30,
        limit: int = 200,
        services: list[str] | None = None,
        group_by_signature: bool = True,
    ) -> ToolResult:
        if not self.logs:
            return ToolResult(ok=False, summary="项目没有日志源", error_code="LOG_SOURCE_REQUIRED")
        cfg = self.logs[0].parser_config or {}
        configured = list(cfg.get("services", []))
        if services and configured and not set(services).issubset(configured):
            return ToolResult(
                ok=False, summary="请求包含未配置的日志服务", error_code="SERVICE_NOT_ALLOWED"
            )
        result = await self._post(
            "/v1/logs/search",
            {
                "target_urls": cfg.get("target_urls", []),
                "compose_project": cfg.get("compose_project"),
                "services": services or configured,
                "query": query,
                "level": level,
                "since_minutes": since_minutes,
                "limit": limit,
            },
        )
        lines = result.get("lines", [])
        signatures = []
        if group_by_signature:
            grouped: dict[str, dict] = {}
            for line in lines:
                message = (
                    str(line.get("line", line.get("message", line)))
                    if isinstance(line, dict)
                    else str(line)
                )
                signature = self._signature(message)
                item = grouped.setdefault(
                    signature,
                    {"signature": signature, "count": 0, "sample": line, "containers": set()},
                )
                item["count"] += 1
                if isinstance(line, dict) and line.get("container"):
                    item["containers"].add(str(line["container"]))
            signatures = [
                {**x, "containers": sorted(x["containers"])}
                for x in sorted(grouped.values(), key=lambda x: x["count"], reverse=True)[:20]
            ]
        data = {
            "lines": lines,
            "signatures": signatures,
            "containers": result.get("containers", []),
        }
        return ToolResult(
            ok=bool(result.get("ok")),
            summary=f"从 {len(result.get('containers', []))} 个容器命中 {len(lines)} 行日志",
            data=data,
            truncated=len(lines) >= limit,
            error_code=None if result.get("ok") else "LOG_UNAVAILABLE",
        )

    async def query_database(
        self, checks: list[str] | None = None, slow_query_limit: int = 10
    ) -> ToolResult:
        if not self.databases:
            return ToolResult(
                ok=False, summary="项目没有数据库配置", error_code="DATABASE_REQUIRED"
            )
        result = await self._post(
            "/v1/database/diagnose",
            {
                "dsn": self._dsn(self.databases[0]),
                "checks": checks or [],
                "slow_query_limit": slow_query_limit,
            },
        )
        return ToolResult(
            ok=bool(result.get("ok")),
            summary="数据库诊断完成" if result.get("ok") else "数据库诊断失败",
            data=result,
            error_code=None if result.get("ok") else "DATABASE_UNAVAILABLE",
        )

    async def query_runtime_resources(
        self, services: list[str] | None = None, checks: list[str] | None = None, top_n: int = 5
    ) -> ToolResult:
        if not self.server:
            return ToolResult(ok=False, summary="项目没有服务器配置", error_code="SERVER_REQUIRED")
        log_cfg = (self.logs[0].parser_config or {}) if self.logs else {}
        configured = list(log_cfg.get("services", []))
        if services and configured and not set(services).issubset(configured):
            return ToolResult(
                ok=False, summary="请求包含未配置的运行服务", error_code="SERVICE_NOT_ALLOWED"
            )
        result = await self._post(
            "/v1/runtime/diagnose",
            {
                "target_urls": log_cfg.get("target_urls", []),
                "compose_project": log_cfg.get("compose_project"),
                "services": services or configured,
                "checks": checks or [],
                "top_n": top_n,
            },
        )
        return ToolResult(
            ok=bool(result.get("ok")),
            summary=f"检查 {len(result.get('containers', []))} 个运行容器",
            data=result,
            error_code=None if result.get("ok") else "RUNTIME_UNAVAILABLE",
        )
