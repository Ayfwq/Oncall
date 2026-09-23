from __future__ import annotations

import os
from uuid import uuid4

import httpx
import pytest

API_URL = os.getenv("ONCALL_TEST_API_URL", "http://127.0.0.1:9900")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_core_api_crud_uses_postgres_and_current_project_contract(service_gate):
    async with httpx.AsyncClient(base_url=API_URL, timeout=10) as client:
        try:
            health = await client.get("/api/health")
            available = health.is_success and health.json().get("database") is True
        except httpx.HTTPError:
            available = False
        service_gate(available, "Oncall API or PostgreSQL is unreachable")

        suffix = uuid4().hex[:10]
        server_id = project_id = conversation_id = None
        try:
            server = await client.post(
                "/api/servers",
                json={
                    "name": f"Core API Test {suffix}",
                    "node_metrics_url": "http://127.0.0.1:19100/metrics",
                    "container_metrics_url": "http://127.0.0.1:19101/metrics",
                    "enabled": False,
                },
            )
            server.raise_for_status()
            server_id = server.json()["id"]

            project = await client.post(
                "/api/projects",
                json={
                    "name": f"Core API Test {suffix}",
                    "description": "current Prometheus project contract",
                    "server_id": server_id,
                    "enabled": False,
                    "metrics_sources": [],
                    "log_sources": [],
                    "database_profiles": [],
                },
            )
            project.raise_for_status()
            project_id = project.json()["id"]
            detail = await client.get(f"/api/projects/{project_id}")
            detail.raise_for_status()
            assert detail.json()["server_id"] == server_id
            assert "rules" not in detail.json()

            conversation = await client.post(
                "/api/conversations",
                json={
                    "title": f"Core API Test {suffix}",
                    "project_id": project_id,
                },
            )
            conversation.raise_for_status()
            conversation_id = conversation.json()["id"]
            renamed = await client.patch(
                f"/api/conversations/{conversation_id}",
                json={"title": f"Core API Renamed {suffix}"},
            )
            renamed.raise_for_status()
            assert renamed.json()["title"] == f"Core API Renamed {suffix}"
            rows = (await client.get("/api/conversations", params={"q": suffix})).json()
            assert any(row["id"] == conversation_id for row in rows)
            assert (await client.get(f"/api/conversations/{conversation_id}/messages")).json() == []

            readiness = await client.get("/api/settings/readiness")
            readiness.raise_for_status()
            assert readiness.json()["storage"]["database"] == "postgresql"
            contracts = await client.get("/api/settings/tool-contracts")
            contracts.raise_for_status()
            assert any(
                item["name"] == "query_current_metrics" for item in contracts.json()["tools"]
            )
        finally:
            if conversation_id:
                await client.delete(f"/api/conversations/{conversation_id}")
            if project_id:
                await client.delete(f"/api/projects/{project_id}")
            if server_id:
                await client.delete(f"/api/servers/{server_id}")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_malformed_resource_ids_return_client_errors(service_gate):
    async with httpx.AsyncClient(base_url=API_URL, timeout=10) as client:
        try:
            health = await client.get("/api/health")
            available = health.is_success
        except httpx.HTTPError:
            available = False
        service_gate(available, "Oncall API is unreachable")

        for method, path in (
            ("GET", "/api/projects/not-a-uuid"),
            ("GET", "/api/incidents/not-a-uuid"),
            ("GET", "/api/conversations/not-a-uuid/messages"),
            ("DELETE", "/api/servers/not-a-uuid"),
        ):
            response = await client.request(method, path)
            assert 400 <= response.status_code < 500
