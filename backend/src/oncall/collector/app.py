from __future__ import annotations

import asyncio
import os
import re
import time
from urllib.parse import urlsplit

import asyncpg
import docker
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from oncall.security.redact import redact_text

app = FastAPI(title="Oncall Collector", docs_url=None, redoc_url=None)


def _auth(authorization: str | None = Header(default=None), x_oncall_token: str | None = Header(default=None)) -> None:
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
    excluded = re.compile(r"(^|[/_-])(postgres|mysql|mariadb|redis|mongo|etcd|minio|milvus)([:/_-]|$)", re.I)
    matched = [
        row for row in rows
        if row in primary or (row.get("compose_project") in projects and not excluded.search(row.get("image", "")))
    ]
    return {
        "ok": bool(matched),
        "compose_project": next(iter(projects), None),
        "containers": matched,
        "discovered_count": len(rows),
        "error": None if matched else "没有根据 Health/Metrics 端口匹配到业务容器",
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
    discovery = _discover_sync(req.target_urls)
    client = _docker_client()
    try:
        containers = client.containers.list()
        selected = []
        for c in containers:
            row = _container_row(c)
            if req.compose_project and row.get("compose_project") != req.compose_project:
                continue
            if req.services and row.get("compose_service") not in req.services:
                continue
            if not req.compose_project:
                ids = {x["id"] for x in discovery.get("containers", [])}
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
        lines = lines[-req.limit:]
        errors = sum(1 for x in lines if re.search(r"\b(ERROR|FATAL|PANIC)\b", x["line"], re.I))
        exceptions = sum(1 for x in lines if "Traceback (most recent call last)" in x["line"] or re.search(r"\w+(Error|Exception):", x["line"]))
        return {"ok": bool(selected), "containers": [c.name for c in selected], "lines": lines, "error_count": errors, "exception_count": exceptions, "error": None if selected else discovery.get("error")}
    finally:
        client.close()


@app.post("/v1/logs/search", dependencies=[Depends(_auth)])
async def logs(req: LogQuery):
    try:
        return await asyncio.to_thread(_logs_sync, req)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "lines": [], "error": redact_text(str(exc))}


async def _database(dsn: str) -> dict:
    conn = await asyncpg.connect(dsn=dsn, timeout=6, statement_cache_size=0)
    try:
        max_connections = int(await conn.fetchval("SHOW max_connections"))
        active = int(await conn.fetchval("SELECT count(*) FROM pg_stat_activity WHERE pid <> pg_backend_pid()"))
        long_transactions = int(await conn.fetchval("SELECT count(*) FROM pg_stat_activity WHERE xact_start IS NOT NULL AND now()-xact_start > interval '5 minutes' AND pid <> pg_backend_pid()"))
        lock_waits = int(await conn.fetchval("SELECT count(*) FROM pg_stat_activity WHERE wait_event_type='Lock'"))
        stats = await conn.fetchrow("SELECT COALESCE(sum(deadlocks),0) deadlocks, CASE WHEN sum(blks_hit)+sum(blks_read)=0 THEN 100 ELSE 100.0*sum(blks_hit)/(sum(blks_hit)+sum(blks_read)) END cache_hit FROM pg_stat_database")
        in_recovery = bool(await conn.fetchval("SELECT pg_is_in_recovery()"))
        lag = await conn.fetchval("SELECT CASE WHEN pg_is_in_recovery() THEN COALESCE(EXTRACT(EPOCH FROM now()-pg_last_xact_replay_timestamp()),0) ELSE 0 END")
        has_statements = bool(await conn.fetchval("SELECT EXISTS(SELECT 1 FROM pg_extension WHERE extname='pg_stat_statements')"))
        slow: list[dict] = []
        if has_statements:
            rows = await conn.fetch("SELECT calls, round(mean_exec_time::numeric,2) mean_ms, round(total_exec_time::numeric,2) total_ms, left(query,300) query FROM pg_stat_statements WHERE query NOT ILIKE '%pg_stat_statements%' ORDER BY mean_exec_time DESC LIMIT 10")
            slow = [{"calls": int(r["calls"]), "mean_ms": float(r["mean_ms"]), "total_ms": float(r["total_ms"]), "query": redact_text(r["query"])} for r in rows]
        signals = {
            "db.up": 1.0,
            "db.connections.active": float(active),
            "db.connections.max": float(max_connections),
            "db.connections.utilization_percent": active / max_connections * 100.0 if max_connections else 0.0,
            "db.long_transactions": float(long_transactions),
            "db.lock_waits": float(lock_waits),
            "db.deadlocks_total": float(stats["deadlocks"]),
            "db.cache_hit_percent": float(stats["cache_hit"] or 0),
            "db.replication_lag_seconds": float(lag or 0),
            "db.slow_queries": float(len(slow)),
        }
        return {"ok": True, "signals": signals, "slow_queries": slow, "capabilities": {"activity": True, "locks": True, "slow_sql": has_statements, "replication": in_recovery}}
    finally:
        await conn.close()


@app.post("/v1/database/diagnose", dependencies=[Depends(_auth)])
async def database(req: DatabaseRequest):
    try:
        return await _database(req.dsn)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "signals": {"db.up": 0.0}, "error": redact_text(str(exc))}


def run() -> None:
    import uvicorn

    uvicorn.run(app, host=os.getenv("ONCALL_COLLECTOR_HOST", "0.0.0.0"), port=int(os.getenv("ONCALL_COLLECTOR_PORT", "9910")))


if __name__ == "__main__":
    run()
