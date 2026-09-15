"""The single supported remote-Python monitoring contract."""

PYTHON_SIGNALS = (
    # Node Exporter / host metrics (11)
    'host.exporter.up', 'host.cpu.percent', 'host.memory.percent',
    'host.memory.available_bytes', 'host.disk.usage_percent', 'host.disk.free_bytes',
    'host.disk.read_bytes_per_sec', 'host.disk.write_bytes_per_sec',
    'host.net.rx_bytes_per_sec', 'host.net.tx_bytes_per_sec', 'host.load.1m',
    # Health endpoint (4)
    'service.reachable', 'service.status_code', 'service.latency_ms',
    'service.consecutive_failures',
    # Application HTTP metrics (6)
    'app.up', 'app.http.rps', 'app.http.error_rate', 'app.http.p95_ms',
    'app.http.p99_ms', 'app.http.availability',
    # Python Prometheus process collector (7)
    'process.target.alive', 'process.target.count', 'process.target.cpu_percent_sum',
    'process.target.rss_bytes_sum', 'process.target.virtual_memory_bytes_sum',
    'process.target.open_fds_sum', 'process.target.uptime_seconds',
)

PYTHON_GPU_SIGNALS = (
    'host.gpu.exporter.up', 'host.gpu.available', 'host.gpu.utilization_percent',
    'host.gpu.memory_percent', 'host.gpu.temperature_celsius', 'host.gpu.power_watts',
)

OBSERVABILITY_SIGNALS = (
    'log.collector.up', 'log.error_count', 'log.exception_count',
    'db.up', 'db.connections.active', 'db.connections.max',
    'db.connections.utilization_percent', 'db.long_transactions', 'db.lock_waits',
    'db.deadlocks_total', 'db.cache_hit_percent', 'db.replication_lag_seconds',
    'db.slow_queries',
)

SUPPORTED_SIGNALS = frozenset((*PYTHON_SIGNALS, *PYTHON_GPU_SIGNALS, *OBSERVABILITY_SIGNALS))

assert len(PYTHON_SIGNALS) == 28
assert len(PYTHON_GPU_SIGNALS) == 6
assert len(OBSERVABILITY_SIGNALS) == 13
