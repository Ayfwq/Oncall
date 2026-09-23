from __future__ import annotations

import json
import logging
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from sqlalchemy import select

from oncall.bootstrap.config import get_settings
from oncall.infrastructure.db.models import (
    MonitoredServer,
    Project,
    ProjectDatabaseProfile,
    ProjectLogSource,
    ProjectMetricsSource,
)

logger = logging.getLogger(__name__)


def project_metric_promql(metric: str, project_id: UUID) -> str | None:
    """Return the canonical PromQL used by both the UI and LLM tools."""
    pid = str(project_id)
    node = f'{{project_id="{pid}",component="node"}}'
    node_idle = f'{{project_id="{pid}",component="node",mode="idle"}}'
    node_disk = f'{{project_id="{pid}",component="node",fstype!~"tmpfs|overlay"}}'
    app = f'{{project_id="{pid}",component="application"}}'
    app_errors = f'{{project_id="{pid}",component="application",status=~"5.."}}'
    gpu = f'{{project_id="{pid}",component="gpu"}}'
    database = f'{{project_id="{pid}",component="database"}}'
    expressions = {
        "host.exporter.up": f"min(up{node})",
        "host.cpu.percent": f"100 - (avg(rate(node_cpu_seconds_total{node_idle}[5m])) * 100)",
        "host.memory.percent": f"(1 - node_memory_MemAvailable_bytes{node} / node_memory_MemTotal_bytes{node}) * 100",
        "host.memory.available_bytes": f"node_memory_MemAvailable_bytes{node}",
        "host.disk.usage_percent": f"max((1 - node_filesystem_avail_bytes{node_disk} / node_filesystem_size_bytes{node_disk}) * 100)",
        "host.disk.free_bytes": f"min(node_filesystem_avail_bytes{node_disk})",
        "host.disk.read_bytes_per_sec": f"sum(rate(node_disk_read_bytes_total{node}[5m]))",
        "host.disk.write_bytes_per_sec": f"sum(rate(node_disk_written_bytes_total{node}[5m]))",
        "host.net.rx_bytes_per_sec": f"sum(rate(node_network_receive_bytes_total{node}[5m]))",
        "host.net.tx_bytes_per_sec": f"sum(rate(node_network_transmit_bytes_total{node}[5m]))",
        "host.load.1m": f"node_load1{node}",
        "host.gpu.exporter.up": f"min(up{gpu})",
        "host.gpu.available": f"count(DCGM_FI_DEV_GPU_UTIL{gpu})",
        "host.gpu.utilization_percent": f"avg(DCGM_FI_DEV_GPU_UTIL{gpu})",
        "host.gpu.memory_percent": f"100 * sum(DCGM_FI_DEV_FB_USED{gpu}) / clamp_min(sum(DCGM_FI_DEV_FB_USED{gpu}) + sum(DCGM_FI_DEV_FB_FREE{gpu}), 1)",
        "host.gpu.temperature_celsius": f"max(DCGM_FI_DEV_GPU_TEMP{gpu})",
        "host.gpu.power_watts": f"sum(DCGM_FI_DEV_POWER_USAGE{gpu})",
        "app.up": f"min(up{app})",
        "app.http.rps": f"sum(rate(http_requests_total{app}[5m]))",
        "app.http.error_rate": f"sum(rate(http_requests_total{app_errors}[5m])) / clamp_min(sum(rate(http_requests_total{app}[5m])), 1)",
        "app.http.availability": f"100 * (1 - sum(rate(http_requests_total{app_errors}[5m])) / clamp_min(sum(rate(http_requests_total{app}[5m])), 1))",
        "app.http.p95_ms": f"histogram_quantile(0.95, sum by (le) (rate(http_request_duration_seconds_bucket{app}[5m]))) * 1000",
        "app.http.p99_ms": f"histogram_quantile(0.99, sum by (le) (rate(http_request_duration_seconds_bucket{app}[5m]))) * 1000",
        "process.target.alive": f"count(process_start_time_seconds{app})",
        "process.target.count": f"count(process_start_time_seconds{app})",
        "process.target.rss_bytes_sum": f"sum(process_resident_memory_bytes{app})",
        "process.target.virtual_memory_bytes_sum": f"sum(process_virtual_memory_bytes{app})",
        "process.target.open_fds_sum": f"sum(process_open_fds{app})",
        "process.target.cpu_percent_sum": f"sum(rate(process_cpu_seconds_total{app}[5m])) * 100",
        "process.target.uptime_seconds": f"time() - min(process_start_time_seconds{app})",
        "db.up": f"min(oncall_postgres_up{database})",
        "db.connections.active": f"oncall_postgres_connections_active{database}",
        "db.connections.max": f"oncall_postgres_connections_max{database}",
        "db.connections.utilization_percent": f"oncall_postgres_connections_utilization_percent{database}",
        "db.long_transactions": f"oncall_postgres_long_transactions{database}",
        "db.lock_waits": f"oncall_postgres_lock_waits{database}",
        "db.deadlocks_total": f"oncall_postgres_deadlocks_total{database}",
        "db.cache_hit_percent": f"oncall_postgres_cache_hit_percent{database}",
        "db.replication_lag_seconds": f"oncall_postgres_replication_lag_seconds{database}",
    }
    return expressions.get(metric)


class PrometheusAPIError(RuntimeError):
    pass


class PrometheusClient:
    """Small, read-only Prometheus HTTP API client used by the agent tools."""

    def __init__(self, base_url: str | None = None):
        self.base_url = (base_url or get_settings().prometheus_url).rstrip("/")

    async def _get(self, path: str, params: dict[str, str]) -> dict:
        async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
            response = await client.get(f"{self.base_url}{path}", params=params)
            response.raise_for_status()
            body = response.json()
        if body.get("status") != "success":
            raise PrometheusAPIError(str(body.get("error") or "Prometheus query failed"))
        return body.get("data") or {}

    async def query(self, expression: str) -> list[dict]:
        data = await self._get("/api/v1/query", {"query": expression})
        return list(data.get("result") or [])

    async def query_range(
        self, expression: str, start: str, end: str, step: str = "30s"
    ) -> list[dict]:
        data = await self._get(
            "/api/v1/query_range", {"query": expression, "start": start, "end": end, "step": step}
        )
        return list(data.get("result") or [])

    async def status(self) -> dict:
        return await self._get("/api/v1/status/buildinfo", {})


def _target_from_url(raw_url: str) -> tuple[str, str, str]:
    parsed = urlsplit(raw_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"invalid Prometheus target URL: {raw_url}")
    path = parsed.path or "/metrics"
    return parsed.netloc, path, parsed.scheme


def _yaml_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _promql_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _rule(
    name: str,
    expr: str,
    *,
    project_id: str,
    project_name: str,
    category: str,
    severity: str,
    duration: str,
    summary: str,
    description: str,
) -> str:
    labels = {
        "project_id": project_id,
        "project_name": project_name,
        "category": category,
        "severity": severity,
    }
    lines = [f"  - alert: {name}", f"    expr: {expr}", f"    for: {duration}", "    labels:"]
    for key, value in labels.items():
        lines.append(f"      {key}: {_yaml_quote(value)}")
    lines.extend(
        [
            "    annotations:",
            f"      summary: {_yaml_quote(summary)}",
            f"      description: {_yaml_quote(description)}",
            '      runbook: "oncall://prometheus"',
        ]
    )
    return "\n".join(lines)


def _project_rules(
    project: Project,
    has_container_metrics: bool,
    *,
    has_database_metrics: bool = False,
    compose_project: str | None = None,
    compose_services: list[str] | None = None,
) -> list[str]:
    pid = str(project.id)
    pname = project.name

    def selector(extra: str = "") -> str:
        return '{project_id="' + pid + '"' + ("," + extra if extra else "") + "}"

    app_selector = selector('component="application"')
    node_selector = selector('component="node"')
    error_selector = selector('status=~"5.."')
    disk_selector = selector('fstype!~"tmpfs|overlay"')
    container_matchers = ['container!=""']
    if compose_project:
        container_matchers.append(
            "container_label_com_docker_compose_project=" + _promql_quote(compose_project)
        )
    if compose_services:
        service_pattern = "|".join(
            service.replace("\\", "\\\\").replace("|", "\\|")
            for service in compose_services
            if service
        )
        if service_pattern:
            container_matchers.append(
                "container_label_com_docker_compose_service=~" + _promql_quote(service_pattern)
            )
    container_selector = selector(",".join(container_matchers))
    database_selector = selector('component="database"')

    # These are intentionally conservative first-version rules. Prometheus
    # handles the `for` window and Alertmanager handles grouping/repeat policy.
    rules = [
        _rule(
            "OncallApplicationMetricsDown",
            f"up{app_selector} == 0",
            project_id=pid,
            project_name=pname,
            category="availability",
            severity="critical",
            duration="2m",
            summary="应用指标端点不可用",
            description="Prometheus 连续 2 分钟无法抓取项目的 /metrics。",
        ),
        _rule(
            "OncallHostExporterDown",
            f"up{node_selector} == 0",
            project_id=pid,
            project_name=pname,
            category="availability",
            severity="critical",
            duration="2m",
            summary="服务器指标采集器不可用",
            description="Prometheus 连续 2 分钟无法抓取 Node Exporter。",
        ),
        _rule(
            "OncallApplicationErrorRateHigh",
            f"(sum(rate(http_requests_total{error_selector}[5m])) / clamp_min(sum(rate(http_requests_total{selector()}[5m])), 1)) > 0.10",
            project_id=pid,
            project_name=pname,
            category="application",
            severity="warning",
            duration="5m",
            summary="应用 5xx 错误率过高",
            description="过去 5 分钟 5xx 错误率超过 10%，且已经持续 5 分钟。",
        ),
        _rule(
            "OncallApplicationLatencyHigh",
            f"histogram_quantile(0.95, sum by (le) (rate(http_request_duration_seconds_bucket{selector()}[5m]))) > 1.5",
            project_id=pid,
            project_name=pname,
            category="application",
            severity="warning",
            duration="5m",
            summary="应用 P95 延迟过高",
            description="过去 5 分钟应用 P95 延迟超过 1.5 秒。",
        ),
        _rule(
            "OncallHostMemoryHigh",
            f"(1 - node_memory_MemAvailable_bytes{selector()} / node_memory_MemTotal_bytes{selector()}) * 100 > 90",
            project_id=pid,
            project_name=pname,
            category="host",
            severity="warning",
            duration="10m",
            summary="服务器内存使用率过高",
            description="服务器内存使用率超过 90%，且已经持续 10 分钟。",
        ),
        _rule(
            "OncallHostDiskHigh",
            f"(1 - node_filesystem_avail_bytes{disk_selector} / node_filesystem_size_bytes{disk_selector}) * 100 > 90",
            project_id=pid,
            project_name=pname,
            category="host",
            severity="warning",
            duration="10m",
            summary="服务器磁盘空间不足",
            description="服务器文件系统使用率超过 90%，且已经持续 10 分钟。",
        ),
    ]
    if has_database_metrics:
        rules.extend(
            [
                _rule(
                    "OncallDatabaseDown",
                    f"(up{database_selector} == 0) or (oncall_postgres_up{selector()} == 0)",
                    project_id=pid,
                    project_name=pname,
                    category="database",
                    severity="critical",
                    duration="2m",
                    summary="PostgreSQL 不可用",
                    description="Prometheus 连续 2 分钟无法取得数据库健康指标。",
                ),
                _rule(
                    "OncallDatabaseConnectionsHigh",
                    f"oncall_postgres_connections_utilization_percent{selector()} > 85",
                    project_id=pid,
                    project_name=pname,
                    category="database",
                    severity="warning",
                    duration="10m",
                    summary="数据库连接压力过高",
                    description="数据库连接使用率超过 85%，且已经持续 10 分钟。",
                ),
                _rule(
                    "OncallDatabaseLockWait",
                    f"oncall_postgres_lock_waits{selector()} > 0",
                    project_id=pid,
                    project_name=pname,
                    category="database",
                    severity="warning",
                    duration="5m",
                    summary="数据库出现锁等待",
                    description="数据库存在锁等待，且已经持续 5 分钟。",
                ),
                _rule(
                    "OncallDatabaseLongTransaction",
                    f"oncall_postgres_long_transactions{selector()} > 0",
                    project_id=pid,
                    project_name=pname,
                    category="database",
                    severity="warning",
                    duration="5m",
                    summary="数据库存在长事务",
                    description="数据库存在超过 5 分钟的事务，且已经持续 5 分钟。",
                ),
                _rule(
                    "OncallDatabaseReplicationLagHigh",
                    f"oncall_postgres_replication_lag_seconds{selector()} > 60",
                    project_id=pid,
                    project_name=pname,
                    category="database",
                    severity="warning",
                    duration="5m",
                    summary="数据库复制延迟过高",
                    description="PostgreSQL 复制延迟超过 60 秒，且已经持续 5 分钟。",
                ),
            ]
        )
    if has_container_metrics:
        container_up_selector = selector('component="container"')
        rules.extend(
            [
                _rule(
                    "OncallContainerMetricsDown",
                    f"up{container_up_selector} == 0",
                    project_id=pid,
                    project_name=pname,
                    category="availability",
                    severity="critical",
                    duration="2m",
                    summary="Docker 容器指标采集器不可用",
                    description="Prometheus 连续 2 分钟无法抓取 cAdvisor。",
                ),
                _rule(
                    "OncallContainerMemoryHigh",
                    f"100 * container_memory_working_set_bytes{container_selector} / clamp_min(container_spec_memory_limit_bytes{container_selector}, 1) > 90",
                    project_id=pid,
                    project_name=pname,
                    category="container",
                    severity="warning",
                    duration="10m",
                    summary="Docker 容器内存过高",
                    description="单个容器内存使用率超过 90%，且已经持续 10 分钟。",
                ),
                _rule(
                    "OncallContainerCpuHigh",
                    f"rate(container_cpu_usage_seconds_total{container_selector}[5m]) > 0.9",
                    project_id=pid,
                    project_name=pname,
                    category="container",
                    severity="warning",
                    duration="10m",
                    summary="Docker 容器 CPU 过高",
                    description="单个容器 CPU 使用率接近一个完整核心，且已经持续 10 分钟。",
                ),
            ]
        )
    return rules


class PrometheusProvisioner:
    """Render file-SD targets and centrally-owned rules for Prometheus."""

    def __init__(self, session):
        self.session = session
        self.root = get_settings().data_dir / "prometheus"

    async def reconcile(self) -> dict:
        projects = list(
            (await self.session.scalars(select(Project).where(Project.enabled.is_(True)))).all()
        )
        servers = {x.id: x for x in (await self.session.scalars(select(MonitoredServer))).all()}
        sources = list(
            (
                await self.session.scalars(
                    select(ProjectMetricsSource).where(ProjectMetricsSource.enabled.is_(True))
                )
            ).all()
        )
        databases = list(
            (
                await self.session.scalars(
                    select(ProjectDatabaseProfile).where(ProjectDatabaseProfile.enabled.is_(True))
                )
            ).all()
        )
        by_project: dict[UUID, list[ProjectMetricsSource]] = {}
        for source in sources:
            by_project.setdefault(source.project_id, []).append(source)

        targets: list[dict] = []
        for project in projects:
            server = servers.get(project.server_id)
            if server:
                for component, raw_url in [
                    ("node", server.node_metrics_url),
                    ("gpu", server.gpu_metrics_url),
                    ("container", server.container_metrics_url),
                ]:
                    if not raw_url:
                        continue
                    address, path, scheme = _target_from_url(raw_url)
                    targets.append(
                        {
                            "targets": [address],
                            "labels": {
                                "project_id": str(project.id),
                                "project_name": project.name,
                                "server_id": str(server.id),
                                "component": component,
                                "__metrics_path__": path,
                                "__scheme__": scheme,
                            },
                        }
                    )
            for source in by_project.get(project.id, []):
                if get_settings().prometheus_proxy_application_scrapes:
                    raw_url = (
                        get_settings().prometheus_scrape_base_url.rstrip("/")
                        + f"/api/prometheus/projects/{project.id}/application-metrics/{source.id}"
                    )
                else:
                    raw_url = source.url
                address, path, scheme = _target_from_url(raw_url)
                labels = {
                    "project_id": str(project.id),
                    "project_name": project.name,
                    "server_id": str(project.server_id),
                    "service": source.name,
                    "component": "application",
                    "__metrics_path__": path,
                    "__scheme__": scheme,
                }
                if (
                    get_settings().prometheus_proxy_application_scrapes
                    and get_settings().prometheus_scrape_token
                ):
                    labels["__param_token"] = get_settings().prometheus_scrape_token
                targets.append({"targets": [address], "labels": labels})

            has_database = any(profile.project_id == project.id for profile in databases)
            if has_database and server and server.collector_url:
                raw_url = (
                    get_settings().prometheus_scrape_base_url.rstrip("/")
                    + f"/api/prometheus/projects/{project.id}/database-metrics"
                )
                address, path, scheme = _target_from_url(raw_url)
                labels = {
                    "project_id": str(project.id),
                    "project_name": project.name,
                    "server_id": str(project.server_id),
                    "component": "database",
                    "__metrics_path__": path,
                    "__scheme__": scheme,
                }
                if get_settings().prometheus_scrape_token:
                    labels["__param_token"] = get_settings().prometheus_scrape_token
                targets.append({"targets": [address], "labels": labels})

        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "targets").mkdir(exist_ok=True)
        (self.root / "rules").mkdir(exist_ok=True)
        (self.root / "targets" / "oncall.json").write_text(
            json.dumps(targets, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        rule_text = "groups:\n- name: oncall-managed\n  rules:\n"
        log_sources = list(
            (
                await self.session.scalars(
                    select(ProjectLogSource).where(ProjectLogSource.enabled.is_(True))
                )
            ).all()
        )
        logs_by_project: dict[UUID, list[ProjectLogSource]] = {}
        for source in log_sources:
            logs_by_project.setdefault(source.project_id, []).append(source)

        for project in projects:
            server = servers.get(project.server_id)
            compose_project = None
            compose_services: list[str] = []
            for source in logs_by_project.get(project.id, []):
                config = source.parser_config or {}
                compose_project = compose_project or config.get("compose_project")
                compose_services.extend(
                    str(x).strip() for x in (config.get("services") or []) if str(x).strip()
                )
            scoped_container_metrics = bool(
                server and server.container_metrics_url and compose_project
            )
            has_database_metrics = bool(
                server
                and server.collector_url
                and any(profile.project_id == project.id for profile in databases)
            )
            rule_text += (
                "\n".join(
                    _project_rules(
                        project,
                        scoped_container_metrics,
                        has_database_metrics=has_database_metrics,
                        compose_project=compose_project,
                        compose_services=sorted(set(compose_services)),
                    )
                )
                + "\n"
            )
        (self.root / "rules" / "oncall.yml").write_text(rule_text, encoding="utf-8")
        reload_url = get_settings().prometheus_reload_url or (
            get_settings().prometheus_url.rstrip("/") + "/-/reload"
        )
        try:
            async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
                await client.post(reload_url)
        except Exception:
            logger.debug("Prometheus reload endpoint is not reachable yet", exc_info=True)
        return {
            "projects": len(projects),
            "targets": len(targets),
            "rules": rule_text.count("  - alert:"),
        }
