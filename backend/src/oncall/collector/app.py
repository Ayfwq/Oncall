from __future__ import annotations

import asyncio
import os
import re
import time
from typing import Literal
from urllib.parse import urlsplit

import asyncpg
import docker
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from oncall.security.redact import redact_text

app = FastAPI(title="PulseOps Collector", docs_url=None, redoc_url=None)


def _auth(
    authorization: str | None = Header(default=None),
    x_oncall_token: str | None = Header(default=None),
) -> None:
    expected = os.getenv("ONCALL_COLLECTOR_TOKEN", "oncall-development-token")
    supplied = x_oncall_token or ((authorization or "").removeprefix("Bearer ").strip())
    if not supplied or supplied != expected:
        raise HTTPException(401, "invalid collector token")


class DiscoverRequest(BaseModel):
    target_urls: list[str] = Field(default_factory=list, max_length=10)


class LogQuery(DiscoverRequest):
    compose_project: str | None = None
    services: list[str] = Field(default_factory=list, max_length=50)
    query: str = ""
    level: str | None = None
    since_minutes: int = Field(default=10, ge=1, le=1440)
    limit: int = Field(default=200, ge=1, le=1000)


class DatabaseRequest(BaseModel):
    dsn: str
    checks: list[
        Literal[
            "availability",
            "connections",
            "long_transactions",
            "lock_waits",
            "blocking_chain",
            "deadlocks",
            "replication",
            "slow_queries",
            "cache_hit",
        ]
    ] = Field(default_factory=list, max_length=9)
    slow_query_limit: int = Field(default=10, ge=1, le=50)


class RuntimeQuery(DiscoverRequest):
    compose_project: str | None = None
    services: list[str] = Field(default_factory=list, max_length=20)
    checks: list[Literal["status", "cpu", "memory", "restarts", "oom", "processes", "ports"]] = (
        Field(default_factory=list, max_length=7)
    )
    top_n: int = Field(default=5, ge=1, le=20)


def _docker_client():
    return docker.from_env(timeout=6)


def _container_row(c) -> dict:
    attrs = c.attrs
    labels = attrs.get("Config", {}).get("Labels") or {}
    return {
        "id": c.id[:12],
        "name": c.name,
        "image": (attrs.get("Config", {}).get("Image") or "")[:300],
        "status": c.status,
        "compose_project": labels.get("com.docker.compose.project"),
        "compose_service": labels.get("com.docker.compose.service"),
        "ports": attrs.get("NetworkSettings", {}).get("Ports") or {},
    }


def _target_ports(urls: list[str]) -> set[int]:
    ports: set[int] = set()
    for value in urls:
        try:
            p = urlsplit(value)
            ports.add(p.port or (443 if p.scheme == "https" else 80))
        except ValueError:
            continue
    return ports


def _discover_sync(urls: list[str]) -> dict:
    client = _docker_client()
    try:
        rows = [_container_row(c) for c in client.containers.list()]
    finally:
        client.close()
    wanted = _target_ports(urls)
    primary: list[dict] = []
    for row in rows:
        for binding in row["ports"].values():
            if any(int(x.get("HostPort", 0)) in wanted for x in (binding or [])):
                primary.append(row)
                break
    projects = {x["compose_project"] for x in primary if x.get("compose_project")}
    excluded = re.compile(
        r"(^|[/_-])(postgres|mysql|mariadb|redis|mongo|etcd|minio|milvus)([:/_-]|$)", re.I
    )
    matched = [
        row
        for row in rows
        if row in primary
        or (row.get("compose_project") in projects and not excluded.search(row.get("image", "")))
    ]
    return {
        "ok": bool(matched),
        "compose_project": next(iter(projects), None),
        "containers": matched,
        "discovered_count": len(rows),
        "error": None if matched else "没有根据 Metrics 端口匹配到业务容器",
    }


@app.get("/health", dependencies=[Depends(_auth)])
async def health():
    try:
        result = await asyncio.to_thread(_discover_sync, [])
        return {"ok": True, "docker": True, "containers": result["discovered_count"]}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "docker": False, "error": redact_text(str(exc))}


@app.post("/v1/docker/discover", dependencies=[Depends(_auth)])
async def discover(req: DiscoverRequest):
    try:
        return await asyncio.to_thread(_discover_sync, req.target_urls)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "containers": [], "error": redact_text(str(exc))}


def _logs_sync(req: LogQuery) -> dict:
    # A manual Compose scope is authoritative. It avoids relying on the
    # externally visible URL port, which is often Nginx rather than the
    # container's published port.
    discovery = None if req.compose_project else _discover_sync(req.target_urls)
    client = _docker_client()
    try:
        selected = []
        for c in client.containers.list():
            row = _container_row(c)
            if req.compose_project and row.get("compose_project") != req.compose_project:
                continue
            if req.services and row.get("compose_service") not in req.services:
                continue
            if not req.compose_project:
                ids = {x["id"] for x in (discovery or {}).get("containers", [])}
                if row["id"] not in ids:
                    continue
            selected.append(c)
        lines: list[dict] = []
        since = int(time.time()) - req.since_minutes * 60
        needle = req.query.lower().strip()
        level = (req.level or "").upper().strip()
        for c in selected:
            raw = c.logs(stdout=True, stderr=True, timestamps=True, since=since, tail=req.limit)
            for value in raw.decode("utf-8", errors="replace").splitlines():
                clean = redact_text(value)[:4000]
                if needle and needle not in clean.lower():
                    continue
                if level and level not in clean.upper():
                    continue
                lines.append({"container": c.name, "line": clean})
        lines = lines[-req.limit :]
        errors = sum(1 for x in lines if re.search(r"\b(ERROR|FATAL|PANIC)\b", x["line"], re.I))
        exceptions = sum(
            1
            for x in lines
            if "Traceback (most recent call last)" in x["line"]
            or re.search(r"\w+(Error|Exception):", x["line"])
        )
        return {
            "ok": bool(selected),
            "containers": [c.name for c in selected],
            "lines": lines,
            "error_count": errors,
            "exception_count": exceptions,
            "error": None
            if selected
            else ((discovery or {}).get("error") or "没有匹配到手动指定的 Docker Compose 容器"),
        }
    finally:
        client.close()


def _runtime_sync(req: RuntimeQuery) -> dict:
    discovery = None if req.compose_project else _discover_sync(req.target_urls)
    discovered_ids = {x["id"] for x in (discovery or {}).get("containers", [])}
    checks = set(req.checks or ["status", "cpu", "memory", "restarts", "oom", "processes", "ports"])
    client = _docker_client()
    try:
        selected = []
        for container in client.containers.list(all=True):
            row = _container_row(container)
            if req.compose_project and row.get("compose_project") != req.compose_project:
                continue
            if req.services and row.get("compose_service") not in req.services:
                continue
            if not req.compose_project and row["id"] not in discovered_ids:
                continue
            selected.append(container)
        rows = []
        for container in selected:
            container.reload()
            attrs = container.attrs
            state = attrs.get("State", {})
            base = _container_row(container)
            row = {"id": base["id"], "name": base["name"], "service": base.get("compose_service")}
            if "status" in checks:
                row["status"] = base["status"]
            if "restarts" in checks:
                row["restart_count"] = int(attrs.get("RestartCount", 0) or 0)
            if "oom" in checks:
                row["oom_killed"] = bool(state.get("OOMKilled", False))
            if "ports" in checks:
                row["ports"] = base["ports"]
            if checks & {"cpu", "memory"} and base["status"] == "running":
                try:
                    stats = container.stats(stream=False)
                except Exception as exc:
                    stats = {}
                    row["stats_error"] = redact_text(str(exc))
                cpu_delta = float(
                    stats.get("cpu_stats", {}).get("cpu_usage", {}).get("total_usage", 0)
                ) - float(stats.get("precpu_stats", {}).get("cpu_usage", {}).get("total_usage", 0))
                system_delta = float(stats.get("cpu_stats", {}).get("system_cpu_usage", 0)) - float(
                    stats.get("precpu_stats", {}).get("system_cpu_usage", 0)
                )
                online = float(stats.get("cpu_stats", {}).get("online_cpus", 1) or 1)
                if "cpu" in checks and stats:
                    row["cpu_percent"] = (
                        (cpu_delta / system_delta * online * 100.0)
                        if system_delta > 0 and cpu_delta >= 0
                        else 0.0
                    )
                memory = stats.get("memory_stats", {})
                usage = float(memory.get("usage", 0) or 0) - float(
                    memory.get("stats", {}).get("cache", 0) or 0
                )
                limit = float(memory.get("limit", 0) or 0)
                if "memory" in checks and stats:
                    row.update(
                        {
                            "memory_bytes": max(0.0, usage),
                            "memory_limit_bytes": limit,
                            "memory_percent": max(0.0, usage) / limit * 100.0 if limit else 0.0,
                        }
                    )
            if "processes" in checks and base["status"] == "running":
                try:
                    top = container.top(ps_args="-eo pid,ppid,user,%cpu,%mem,comm,args")
                    titles = top.get("Titles", [])
                    row["processes"] = [
                        dict(zip(titles, item, strict=False))
                        for item in top.get("Processes", [])[: req.top_n]
                    ]
                except Exception as exc:  # process visibility differs by Docker platform
                    row["processes_error"] = redact_text(str(exc))
            rows.append(row)
        rows.sort(
            key=lambda x: (float(x.get("cpu_percent", 0)), float(x.get("memory_percent", 0))),
            reverse=True,
        )
        return {
            "ok": bool(selected),
            "containers": rows,
            "checks": sorted(checks),
            "error": None
            if selected
            else ((discovery or {}).get("error") or "没有匹配到手动指定的 Docker Compose 容器"),
        }
    finally:
        client.close()


@app.post("/v1/runtime/diagnose", dependencies=[Depends(_auth)])
async def runtime(req: RuntimeQuery):
    try:
        return await asyncio.to_thread(_runtime_sync, req)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "containers": [], "error": redact_text(str(exc))}


@app.post("/v1/logs/search", dependencies=[Depends(_auth)])
async def logs(req: LogQuery):
    try:
        return await asyncio.to_thread(_logs_sync, req)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "lines": [], "error": redact_text(str(exc))}


async def _database(dsn: str, checks: list[str] | None = None, slow_query_limit: int = 10) -> dict:
    conn = await asyncpg.connect(dsn=dsn, timeout=6, statement_cache_size=0)
    try:
        requested = set(
            checks
            or [
                "availability",
                "connections",
                "long_transactions",
                "lock_waits",
                "blocking_chain",
                "deadlocks",
                "replication",
                "slow_queries",
                "cache_hit",
            ]
        )
        signals = {"db.up": 1.0} if "availability" in requested else {}
        if "connections" in requested:
            max_connections = int(await conn.fetchval("SHOW max_connections"))
            active = int(
                await conn.fetchval(
                    "SELECT count(*) FROM pg_stat_activity WHERE pid <> pg_backend_pid()"
                )
            )
            signals.update(
                {
                    "db.connections.active": float(active),
                    "db.connections.max": float(max_connections),
                    "db.connections.utilization_percent": active / max_connections * 100.0
                    if max_connections
                    else 0.0,
                }
            )
        if "long_transactions" in requested:
            signals["db.long_transactions"] = float(
                await conn.fetchval(
                    "SELECT count(*) FROM pg_stat_activity WHERE xact_start IS NOT NULL AND now()-xact_start > interval '5 minutes' AND pid <> pg_backend_pid()"
                )
            )
        if "lock_waits" in requested:
            signals["db.lock_waits"] = float(
                await conn.fetchval(
                    "SELECT count(*) FROM pg_stat_activity WHERE wait_event_type='Lock'"
                )
            )
        if requested & {"deadlocks", "cache_hit"}:
            stats = await conn.fetchrow(
                "SELECT COALESCE(sum(deadlocks),0) deadlocks, CASE WHEN sum(blks_hit)+sum(blks_read)=0 THEN 100 ELSE 100.0*sum(blks_hit)/(sum(blks_hit)+sum(blks_read)) END cache_hit FROM pg_stat_database"
            )
            if "deadlocks" in requested:
                signals["db.deadlocks_total"] = float(stats["deadlocks"])
            if "cache_hit" in requested:
                signals["db.cache_hit_percent"] = float(stats["cache_hit"] or 0)
        in_recovery = False
        if "replication" in requested:
            in_recovery = bool(await conn.fetchval("SELECT pg_is_in_recovery()"))
            lag = await conn.fetchval(
                "SELECT CASE WHEN pg_is_in_recovery() THEN COALESCE(EXTRACT(EPOCH FROM now()-pg_last_xact_replay_timestamp()),0) ELSE 0 END"
            )
            signals["db.replication_lag_seconds"] = float(lag or 0)
        blocking = []
        if "blocking_chain" in requested:
            blocking_rows = await conn.fetch(
                "SELECT blocked.pid blocked_pid, blocking.pid blocking_pid, left(blocked.query,300) blocked_query, left(blocking.query,300) blocking_query FROM pg_stat_activity blocked JOIN LATERAL unnest(pg_blocking_pids(blocked.pid)) blocker(pid) ON true JOIN pg_stat_activity blocking ON blocking.pid=blocker.pid LIMIT 20"
            )
            blocking = [
                {
                    "blocked_pid": int(r["blocked_pid"]),
                    "blocking_pid": int(r["blocking_pid"]),
                    "blocked_query": redact_text(r["blocked_query"] or ""),
                    "blocking_query": redact_text(r["blocking_query"] or ""),
                }
                for r in blocking_rows
            ]
        slow: list[dict] = []
        has_statements = None
        if "slow_queries" in requested:
            has_statements = bool(
                await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM pg_extension WHERE extname='pg_stat_statements')"
                )
            )
            if has_statements:
                rows = await conn.fetch(
                    "SELECT calls, round(mean_exec_time::numeric,2) mean_ms, round(total_exec_time::numeric,2) total_ms, left(query,300) query FROM pg_stat_statements WHERE query NOT ILIKE '%pg_stat_statements%' ORDER BY mean_exec_time DESC LIMIT $1",
                    slow_query_limit,
                )
                slow = [
                    {
                        "calls": int(r["calls"]),
                        "mean_ms": float(r["mean_ms"]),
                        "total_ms": float(r["total_ms"]),
                        "query": redact_text(r["query"]),
                    }
                    for r in rows
                ]
            signals["db.slow_queries"] = float(len(slow))
        result = {
            "ok": True,
            "signals": signals,
            "capabilities": {
                "checked": sorted(requested),
                "activity": bool(requested & {"connections", "long_transactions"}),
                "locks": bool(requested & {"lock_waits", "blocking_chain"}),
                "slow_sql": has_statements,
                "replication": in_recovery if "replication" in requested else None,
            },
        }
        if "slow_queries" in requested:
            result["slow_queries"] = slow
        if "blocking_chain" in requested:
            result["blocking_chain"] = blocking
        return result
    finally:
        await conn.close()


@app.post("/v1/database/diagnose", dependencies=[Depends(_auth)])
async def database(req: DatabaseRequest):
    try:
        return await _database(req.dsn, req.checks, req.slow_query_limit)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "signals": {"db.up": 0.0}, "error": redact_text(str(exc))}


def run() -> None:
    import uvicorn

    uvicorn.run(
        app,
        host=os.getenv("ONCALL_COLLECTOR_HOST", "0.0.0.0"),
        port=int(os.getenv("ONCALL_COLLECTOR_PORT", "9910")),
    )


if __name__ == "__main__":
    run()
