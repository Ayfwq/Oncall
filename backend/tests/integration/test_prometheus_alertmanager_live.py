from __future__ import annotations

import os
from uuid import uuid4

import httpx
import pytest

API_URL = os.getenv("ONCALL_TEST_API_URL", "http://127.0.0.1:9900")
PROMETHEUS_URL = os.getenv("ONCALL_TEST_PROMETHEUS_URL", "http://127.0.0.1:19090")
ALERTMANAGER_URL = os.getenv("ONCALL_TEST_ALERTMANAGER_URL", "http://127.0.0.1:19093")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_live_prometheus_alertmanager_and_grouped_incident_pipeline(service_gate):
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            health = await client.get(f"{API_URL}/api/health")
            prometheus = await client.get(f"{PROMETHEUS_URL}/-/ready")
            alertmanager = await client.get(f"{ALERTMANAGER_URL}/-/ready")
            available = health.is_success and prometheus.is_success and alertmanager.is_success
        except httpx.HTTPError:
            available = False
        service_gate(available, "API, Prometheus or Alertmanager is unreachable")

        suffix = uuid4().hex[:10]
        server_id = None
        project_id = None
        incident_id = None
        try:
            server_response = await client.post(
                f"{API_URL}/api/servers",
                json={
                    "name": f"Alert Pipeline Test {suffix}",
                    "node_metrics_url": "http://127.0.0.1:19100/metrics",
                    "container_metrics_url": "http://127.0.0.1:19101/metrics",
                    "enabled": False,
                },
            )
            server_response.raise_for_status()
            server_id = server_response.json()["id"]
            project_response = await client.post(
                f"{API_URL}/api/projects",
                json={
                    "name": f"Alert Pipeline Test {suffix}",
                    "server_id": server_id,
                    "enabled": False,
                    "metrics_sources": [],
                    "log_sources": [],
                    "database_profiles": [],
                },
            )
            project_response.raise_for_status()
            project_id = project_response.json()["id"]

            group_key = f'{{}}:{{project_id="{project_id}",alertname="DiskHigh",category="host"}}'
            alerts = [
                {
                    "status": "firing",
                    "labels": {
                        "project_id": project_id,
                        "alertname": "DiskHigh",
                        "category": "host",
                        "severity": "warning",
                        "instance": instance,
                    },
                    "annotations": {"summary": "磁盘压力测试"},
                    "fingerprint": f"{suffix}-{instance}",
                    "value": "91",
                }
                for instance in ("disk-a", "disk-b")
            ]
            firing = {"status": "firing", "groupKey": group_key, "alerts": alerts}
            first = await client.post(f"{API_URL}/api/webhooks/alertmanager", json=firing)
            duplicate = await client.post(f"{API_URL}/api/webhooks/alertmanager", json=firing)
            first.raise_for_status()
            duplicate.raise_for_status()
            assert len(first.json()["incidents"]) == 1
            assert duplicate.json()["incidents"] == first.json()["incidents"]
            incident_id = first.json()["incidents"][0]

            for alert in alerts:
                alert["status"] = "resolved"
            resolved = await client.post(
                f"{API_URL}/api/webhooks/alertmanager",
                json={"status": "resolved", "groupKey": group_key, "alerts": alerts},
            )
            resolved.raise_for_status()
            detail = await client.get(f"{API_URL}/api/incidents/{incident_id}")
            detail.raise_for_status()
            assert detail.json()["status"] == "resolved"
            assert "已合并 2 个实例" in detail.json()["summary"]
        finally:
            if incident_id:
                await client.delete(f"{API_URL}/api/incidents/{incident_id}")
            if project_id:
                await client.delete(f"{API_URL}/api/projects/{project_id}")
            if server_id:
                await client.delete(f"{API_URL}/api/servers/{server_id}")
