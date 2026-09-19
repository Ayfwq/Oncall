"""Turn detector signals into user-facing incidents without creating noise.

Every enabled rule still records state transitions.  A transition becomes an
Incident only when it is actionable: shared resource pressure is supporting
evidence, and database long transactions need a second impact signal.  This
keeps small, self-healing fluctuations visible in snapshots without presenting
them as service failures.
"""

from collections.abc import Mapping

EVIDENCE_ONLY_METRICS = frozenset({
    'host.cpu.percent',
    'host.memory.percent',
    'host.gpu.memory_percent',
    'process.target.cpu_percent_sum',
    'process.target.rss_bytes_sum',
})


def _number(signals: Mapping[str, object], key: str, default: float = 0.0) -> float:
    value = signals.get(key, default)
    return float(value) if isinstance(value, (int, float, bool)) else default


def should_open_incident(metric_key: str, signals: Mapping[str, object] | None = None) -> bool:
    if metric_key in EVIDENCE_ONLY_METRICS:
        return False
    if metric_key != 'db.long_transactions':
        return True

    # A few long-running transactions are common in batch workers.  Escalate
    # only when the database or application also shows measurable impact.
    values = signals or {}
    return any((
        _number(values, 'db.lock_waits') >= 1,
        _number(values, 'db.connections.utilization_percent') >= 70,
        _number(values, 'db.slow_queries') >= 1,
        _number(values, 'service.consecutive_failures') >= 1,
        _number(values, 'app.http.error_rate') >= 0.05,
        _number(values, 'app.http.availability', 100) < 99,
    ))
