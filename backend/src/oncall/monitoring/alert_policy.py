"""Decide which detector signals are actionable enough to open an Incident.

All rules still run and persist transitions.  Resource-only signals are kept as
diagnostic evidence because a busy shared host does not prove that one project
is unhealthy.  The Agent will read them when an application/database/log rule
opens an incident.
"""

EVIDENCE_ONLY_METRICS = frozenset({
    'host.cpu.percent',
    'host.memory.percent',
    'process.target.cpu_percent_sum',
    'process.target.rss_bytes_sum',
})


def should_open_incident(metric_key: str) -> bool:
    return metric_key not in EVIDENCE_ONLY_METRICS
