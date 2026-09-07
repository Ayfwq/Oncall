from __future__ import annotations

import time
from typing import Any

import httpx
from prometheus_client.parser import text_string_to_metric_families

from oncall.application.dtos import MonitoredServerDTO
from oncall.integrations.base import CollectResult


def _samples(text: str) -> list[Any]:
    return [sample for family in text_string_to_metric_families(text) for sample in family.samples]


def _one(samples: list[Any], name: str, **labels: str) -> float | None:
    for sample in samples:
        if sample.name == name and all(sample.labels.get(k) == v for k, v in labels.items()):
            return float(sample.value)
    return None


def _sum(samples: list[Any], name: str, *, skip_label: tuple[str, set[str]] | None = None) -> float:
    total = 0.0
    for sample in samples:
        if sample.name != name:
            continue
        if skip_label and sample.labels.get(skip_label[0], '') in skip_label[1]:
            continue
        total += float(sample.value)
    return total


def _connection_error(exc: Exception) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return 'connection timed out'
    if isinstance(exc, httpx.ConnectError):
        return 'connection failed'
    if isinstance(exc, httpx.HTTPStatusError):
        return f'HTTP {exc.response.status_code}'
    return str(exc).strip() or exc.__class__.__name__


class ServerExportersIntegration:
    """Collect host and optional NVIDIA GPU metrics from remote exporters."""

    name = 'server'

    def __init__(self, server: MonitoredServerDTO | None):
        self.server = server

    async def _scrape(self, url: str) -> tuple[list[Any], int]:
        # Monitoring targets must be reached directly. Inheriting HTTP_PROXY can
        # make an unreachable private/loopback URL appear healthy by scraping a
        # completely different exporter through the proxy.
        async with httpx.AsyncClient(timeout=6.0, follow_redirects=True, trust_env=False) as client:
            response = await client.get(url)
            response.raise_for_status()
        return _samples(response.text), len(response.content)

    @staticmethod
    def _node_signals(samples: list[Any]) -> tuple[dict[str, float], dict[str, Any]]:
        modes: dict[str, float] = {}
        for sample in samples:
            if sample.name == 'node_cpu_seconds_total':
                mode = sample.labels.get('mode', 'unknown')
                modes[mode] = modes.get(mode, 0.0) + float(sample.value)
        total_cpu = sum(modes.values())
        if total_cpu <= 0:
            raise ValueError('endpoint is reachable but does not contain Node Exporter CPU metrics')
        idle_cpu = modes.get('idle', 0.0) + modes.get('iowait', 0.0)
        cpu_percent = (1.0 - idle_cpu / total_cpu) * 100.0 if total_cpu > 0 else 0.0

        memory_total = _one(samples, 'node_memory_MemTotal_bytes') or 0.0
        memory_available = _one(samples, 'node_memory_MemAvailable_bytes')
        if memory_available is None:
            memory_available = sum(_one(samples, key) or 0.0 for key in (
                'node_memory_MemFree_bytes', 'node_memory_Buffers_bytes', 'node_memory_Cached_bytes',
            ))
        memory_percent = (1.0 - memory_available / memory_total) * 100.0 if memory_total > 0 else 0.0

        root_size = root_avail = None
        for sample in samples:
            if sample.name not in {'node_filesystem_size_bytes', 'node_filesystem_avail_bytes'}:
                continue
            if sample.labels.get('mountpoint') != '/':
                continue
            if sample.name == 'node_filesystem_size_bytes':
                root_size = float(sample.value)
            else:
                root_avail = float(sample.value)
        root_size = root_size or 0.0
        root_avail = root_avail or 0.0
        disk_percent = (1.0 - root_avail / root_size) * 100.0 if root_size > 0 else 0.0

        ignored_devices = {'lo'}
        counters = {
            'ts': time.time(),
            'cpu_total_seconds': total_cpu,
            'cpu_idle_seconds': idle_cpu,
            'disk_read_bytes': _sum(samples, 'node_disk_read_bytes_total', skip_label=('device', {'loop0'})),
            'disk_write_bytes': _sum(samples, 'node_disk_written_bytes_total', skip_label=('device', {'loop0'})),
            'net_rx_bytes': _sum(samples, 'node_network_receive_bytes_total', skip_label=('device', ignored_devices)),
            'net_tx_bytes': _sum(samples, 'node_network_transmit_bytes_total', skip_label=('device', ignored_devices)),
        }
        signals = {
            'host.exporter.up': 1.0,
            'host.cpu.percent': max(0.0, min(100.0, cpu_percent)),
            'host.memory.percent': max(0.0, min(100.0, memory_percent)),
            'host.memory.available_bytes': memory_available,
            'host.disk.usage_percent': max(0.0, min(100.0, disk_percent)),
            'host.disk.free_bytes': root_avail,
            'host.disk.read_bytes_per_sec': 0.0,
            'host.disk.write_bytes_per_sec': 0.0,
            'host.net.rx_bytes_per_sec': 0.0,
            'host.net.tx_bytes_per_sec': 0.0,
            'host.load.1m': _one(samples, 'node_load1') or 0.0,
        }
        resources = {
            'cpu_count': len({s.labels.get('cpu') for s in samples if s.name == 'node_cpu_seconds_total'}),
            'boot_time': _one(samples, 'node_boot_time_seconds'),
            'counters': counters,
            'disk_root': '/',
        }
        return signals, resources

    @staticmethod
    def _gpu_signals(samples: list[Any]) -> tuple[dict[str, float], dict[str, dict[str, float]], dict[str, Any]]:
        devices: dict[str, dict[str, Any]] = {}
        metric_map = {
            'DCGM_FI_DEV_GPU_UTIL': 'utilization_percent',
            'DCGM_FI_DEV_FB_USED': 'memory_used_mib',
            'DCGM_FI_DEV_FB_FREE': 'memory_free_mib',
            'DCGM_FI_DEV_GPU_TEMP': 'temperature_celsius',
            'DCGM_FI_DEV_POWER_USAGE': 'power_watts',
        }
        for sample in samples:
            field = metric_map.get(sample.name)
            if not field:
                continue
            key = sample.labels.get('UUID') or sample.labels.get('gpu') or sample.labels.get('Hostname') or '0'
            row = devices.setdefault(key, {'id': key, 'name': sample.labels.get('modelName') or sample.labels.get('model') or 'NVIDIA GPU'})
            row[field] = float(sample.value)
        resource_signals: dict[str, dict[str, float]] = {}
        rows = []
        for key, row in devices.items():
            used = float(row.get('memory_used_mib', 0.0))
            free = float(row.get('memory_free_mib', 0.0))
            memory_percent = used / (used + free) * 100.0 if used + free > 0 else 0.0
            row['memory_percent'] = memory_percent
            rows.append(row)
            resource_signals[f'gpu:{key}'] = {
                'host.gpu.utilization_percent': float(row.get('utilization_percent', 0.0)),
                'host.gpu.memory_percent': memory_percent,
                'host.gpu.temperature_celsius': float(row.get('temperature_celsius', 0.0)),
                'host.gpu.power_watts': float(row.get('power_watts', 0.0)),
            }
        if not rows:
            raise ValueError('endpoint is reachable but does not contain DCGM GPU metrics')
        signals = {
            'host.gpu.exporter.up': 1.0,
            'host.gpu.available': 1.0,
            'host.gpu.utilization_percent': sum(float(x.get('utilization_percent', 0.0)) for x in rows) / len(rows),
            'host.gpu.memory_percent': max(float(x.get('memory_percent', 0.0)) for x in rows),
            'host.gpu.temperature_celsius': max(float(x.get('temperature_celsius', 0.0)) for x in rows),
            'host.gpu.power_watts': sum(float(x.get('power_watts', 0.0)) for x in rows),
        }
        return signals, resource_signals, {'available': True, 'devices': rows}

    async def collect(self) -> CollectResult:
        if self.server is None or not self.server.enabled:
            return CollectResult(name=self.name, ok=True, resources={'configured': False})
        signals: dict[str, float] = {}
        resource_signals: dict[str, dict[str, float]] = {}
        resources: dict[str, Any] = {'configured': True, 'server_id': str(self.server.id), 'server_name': self.server.name}
        errors: list[str] = []
        try:
            node_samples, size = await self._scrape(self.server.node_metrics_url)
            node_signals, node_resources = self._node_signals(node_samples)
            signals.update(node_signals)
            resources['node'] = {'ok': True, 'url': self.server.node_metrics_url, 'bytes': size, **node_resources}
        except Exception as exc:  # noqa: BLE001
            signals['host.exporter.up'] = 0.0
            detail = _connection_error(exc)
            errors.append(f'Node Exporter: {detail}')
            resources['node'] = {'ok': False, 'url': self.server.node_metrics_url, 'error': detail}

        if self.server.gpu_metrics_url:
            try:
                gpu_samples, size = await self._scrape(self.server.gpu_metrics_url)
                gpu_signals, gpu_resources, gpu_meta = self._gpu_signals(gpu_samples)
                signals.update(gpu_signals)
                resource_signals.update(gpu_resources)
                resources['gpu'] = {'ok': True, 'url': self.server.gpu_metrics_url, 'bytes': size, **gpu_meta}
            except Exception as exc:  # noqa: BLE001
                signals['host.gpu.exporter.up'] = 0.0
                detail = _connection_error(exc)
                errors.append(f'DCGM Exporter: {detail}')
                resources['gpu'] = {'ok': False, 'url': self.server.gpu_metrics_url, 'error': detail}
        else:
            resources['gpu'] = {'configured': False}
        return CollectResult(
            name=self.name,
            ok=not errors,
            signals=signals,
            resource_signals=resource_signals,
            resources=resources,
            error='; '.join(errors) or None,
        )
