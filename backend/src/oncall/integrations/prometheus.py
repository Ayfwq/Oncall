from __future__ import annotations

import base64
import time
from datetime import datetime
from typing import Any

import httpx
from prometheus_client.parser import text_string_to_metric_families

from oncall.application.dtos import MetricsSourceDTO
from oncall.domain.schemas import ToolResult
from oncall.infrastructure.db.models import MetricCursor
from oncall.integrations.base import CollectResult

# Cumulative request counters and latency histograms per web framework. The
# counter is authoritative for request/error totals; the histogram supplies the
# latency buckets for percentile computation.
_REQUEST_TOTAL_NAMES = (
    'http_requests_total',
    'starlette_requests_total',
    'django_http_requests_total_before_middleware',
    'django_http_requests_total_after_middleware',
    'flask_http_request_total',
)
_DURATION_HIST_NAMES = (
    'http_request_duration_seconds',
    'http_server_requests_seconds',
    'django_http_requests_duration_seconds',
    'flask_http_request_duration_seconds',
)


def _route_of(labels: dict[str, str], route_label: str) -> str:
    return (
        labels.get(route_label)
        or labels.get('handler')
        or labels.get('uri')
        or labels.get('view')
        or labels.get('path')
        or 'unknown'
    )


def _parse_le(value: str | None) -> float:
    if value is None or value == '+Inf':
        return float('inf')
    try:
        return float(value)
    except (TypeError, ValueError):
        return float('inf')


def _status_code(labels: dict[str, str]) -> int:
    """Normalise the status label to an integer.

    prometheus-fastapi-instrumentator buckets statuses into groups (``2xx``,
    ``3xx``, ``4xx``, ``5xx``) by default, while Spring Boot / Django expose the
    raw code (``500``). Both must be treated as errors when >= 500.
    """
    raw = labels.get('status')
    if raw is None:
        return 200
    if raw.endswith('xx'):
        try:
            return int(raw[0]) * 100
        except (TypeError, ValueError):
            return 200
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 200


def _hist_quantile(buckets: dict[float, float], q: float) -> float:
    """Prometheus-style histogram_quantile over cumulative bucket counts."""
    items = sorted(buckets.items())
    if not items:
        return 0.0
    total = items[-1][1]
    if total <= 0:
        return 0.0
    rank = q * total
    for i, (le, count) in enumerate(items):
        if count >= rank:
            if i == 0:
                return le
            prev_le, prev_count = items[i - 1]
            if count == prev_count:
                return le
            return prev_le + (le - prev_le) * (rank - prev_count) / (count - prev_count)
    return items[-1][0]


def _new_route() -> dict[str, Any]:
    return {'total': 0.0, 'err5xx': 0.0, 'buckets': {}}


class PrometheusIntegration:
    """Scrapes a Prometheus text /metrics endpoint and derives API golden signals.

    Counters are cumulative, so request rate and error rate are computed as the
    delta between this scrape and the previous one (persisted in ``metric_cursors``
    so a monitoring-worker restart does not fabricate a spike). Latency percentiles
    are read directly from the histogram buckets (cumulative since process start).
    """

    name = 'prometheus'

    def __init__(self, session, project_id, sources: list[MetricsSourceDTO], *, persist_state: bool = True):
        self.session = session
        self.project_id = project_id
        self.sources = sources
        self.persist_state = persist_state

    async def _get_cursor(self, resource_key: str, metric_key: str) -> float | None:
        row = await self.session.get(MetricCursor, (self.project_id, resource_key, metric_key))
        return float(row.last_value) if row is not None else None

    async def _put_cursor(self, resource_key: str, metric_key: str, value: float) -> None:
        now = datetime.now().astimezone()
        row = await self.session.get(MetricCursor, (self.project_id, resource_key, metric_key))
        if row is None:
            self.session.add(
                MetricCursor(project_id=self.project_id, resource_key=resource_key, metric_key=metric_key, last_value=value, last_ts=now)
            )
        else:
            row.last_value = value
            row.last_ts = now

    async def _scrape_source(self, src: MetricsSourceDTO) -> dict[str, Any]:
        headers: dict[str, str] = {}
        if src.auth_type == 'bearer' and src.token:
            headers['Authorization'] = f'Bearer {src.token}'
        elif src.auth_type == 'basic' and src.token:
            headers['Authorization'] = 'Basic ' + base64.b64encode(src.token.encode()).decode()
        async with httpx.AsyncClient(timeout=src.scrape_timeout_ms / 1000.0, trust_env=False) as client:
            resp = await client.get(src.url, headers=headers)
            resp.raise_for_status()
            text = resp.text
        families = list(text_string_to_metric_families(text))
        all_samples = [sample for family in families for sample in family.samples]

        def sample_value(name: str) -> float | None:
            for sample in all_samples:
                if sample.name == name:
                    return float(sample.value)
            return None

        process = {
            'cpu_seconds_total': sample_value('process_cpu_seconds_total'),
            'rss_bytes': sample_value('process_resident_memory_bytes'),
            'virtual_memory_bytes': sample_value('process_virtual_memory_bytes'),
            'open_fds': sample_value('process_open_fds'),
            'start_time_seconds': sample_value('process_start_time_seconds'),
        }

        routes: dict[str, dict[str, Any]] = {}
        has_counter = False
        # pass 1: request counters are authoritative for totals/errors. Detect them
        # by shape (a counter carrying both a route label and a status label) rather
        # than by name, because frameworks disagree: instrumentator v8 emits
        # ``http_requests``, older versions ``http_requests_total``, Starlette
        # ``starlette_requests_total``, etc. The status label may be a raw code
        # ("500") or a group ("5xx").
        for fam in families:
            if fam.type != 'counter':
                continue
            has_route = any((src.route_label in s.labels or 'handler' in s.labels or 'uri' in s.labels or 'view' in s.labels) for s in fam.samples)
            has_status = any('status' in s.labels for s in fam.samples)
            if not (has_route and has_status):
                continue
            has_counter = True
            for s in fam.samples:
                handler = _route_of(s.labels, src.route_label)
                status = _status_code(s.labels)
                r = routes.setdefault(handler, _new_route())
                r['total'] += float(s.value)
                if status >= 500:
                    r['err5xx'] += float(s.value)
        # pass 2: histograms for latency + (if no counter) totals
        for fam in families:
            if fam.type != 'histogram':
                continue
            if fam.name not in _DURATION_HIST_NAMES and not fam.name.endswith('_seconds'):
                continue
            # Skip route-less histograms (e.g. instrumentator's extra
            # http_request_duration_highr_seconds, which carries only an "le" label
            # and would otherwise become a bogus "route:unknown").
            if not any((src.route_label in s.labels or 'handler' in s.labels or 'uri' in s.labels or 'view' in s.labels) for s in fam.samples):
                continue
            for s in fam.samples:
                handler = _route_of(s.labels, src.route_label)
                r = routes.setdefault(handler, _new_route())
                if s.name.endswith('_bucket'):
                    le = _parse_le(s.labels.get('le'))
                    r['buckets'][le] = r['buckets'].get(le, 0.0) + float(s.value)
                elif s.name.endswith('_count') and not has_counter:
                    status = _status_code(s.labels)
                    r['total'] += float(s.value)
                    if status >= 500:
                        r['err5xx'] += float(s.value)
        return {'ok': True, 'routes': routes, 'process': process, 'meta': {'name': src.name, 'url': src.url, 'ok': True, 'routes': len(routes), 'process_metrics': process['cpu_seconds_total'] is not None}}

    async def collect(self) -> CollectResult:
        signals: dict[str, float] = {}
        resource_signals: dict[str, dict[str, float]] = {}
        resources: dict[str, Any] = {'sources': []}
        any_ok = False
        global_req_delta = 0.0
        global_err_delta = 0.0
        global_rps = 0.0
        global_p95 = 0.0
        global_p99 = 0.0
        global_availability = 100.0
        process_count = 0.0
        process_cpu_percent = 0.0
        process_rss_bytes = 0.0
        process_virtual_memory_bytes = 0.0
        process_open_fds = 0.0
        process_uptime_seconds = 0.0

        enabled = [s for s in self.sources if s.enabled]
        if not enabled:
            return CollectResult(name=self.name, ok=True, signals=signals, resource_signals=resource_signals, resources=resources)

        for src in enabled:
            try:
                data = await self._scrape_source(src)
            except Exception as exc:  # noqa: BLE001 — one bad target must not sink the others
                resources['sources'].append({'name': src.name, 'url': src.url, 'ok': False, 'error': str(exc)})
                continue
            any_ok = True
            resources['sources'].append(data['meta'])
            routes = data['routes']

            # The scrape clock belongs to this source. Reusing one global clock
            # made source #2 see an almost-zero interval and fabricate a huge RPS.
            scrape_cursor_key = f'_scrape:{src.name}'
            prev_ts = await self._get_cursor(scrape_cursor_key, 'ts')
            now = time.time()
            dt = now - prev_ts if prev_ts else 0.0

            process = data.get('process') or {}
            process_cpu = process.get('cpu_seconds_total')
            if process_cpu is not None:
                process_count += 1.0
                previous_process_cpu = await self._get_cursor(scrape_cursor_key, '_process_cpu')
                delta_process_cpu = float(process_cpu) - previous_process_cpu if previous_process_cpu is not None else 0.0
                if delta_process_cpu >= 0 and dt > 0:
                    process_cpu_percent += delta_process_cpu / dt * 100.0
                process_rss_bytes += float(process.get('rss_bytes') or 0.0)
                process_virtual_memory_bytes += float(process.get('virtual_memory_bytes') or 0.0)
                process_open_fds += float(process.get('open_fds') or 0.0)
                start_time = process.get('start_time_seconds')
                if start_time is not None:
                    process_uptime_seconds = max(process_uptime_seconds, max(0.0, now - float(start_time)))
                if self.persist_state:
                    await self._put_cursor(scrape_cursor_key, '_process_cpu', float(process_cpu))

            agg_total = 0.0
            agg_5xx = 0.0
            agg_rps = 0.0
            agg_req_delta = 0.0
            agg_err_delta = 0.0
            combined_buckets: dict[float, float] = {}

            for handler, r in routes.items():
                route_key = f'route:{handler}'
                cum_total = float(r['total'])
                cum_5xx = float(r['err5xx'])
                cursor_route_key = f'{src.name}:{route_key}'
                prev_total = await self._get_cursor(cursor_route_key, '_total')
                prev_5xx = await self._get_cursor(cursor_route_key, '_5xx')
                d_total = cum_total - prev_total if prev_total is not None else 0.0
                d_5xx = cum_5xx - prev_5xx if prev_5xx is not None else 0.0
                if d_total < 0:  # counter reset on app restart
                    d_total = 0.0
                    d_5xx = 0.0
                d_5xx = max(0.0, min(d_total, d_5xx))
                rps = d_total / dt if dt > 0 else 0.0
                err_rate = (d_5xx / d_total) if d_total > 0 else 0.0
                # Availability and latency must describe this scrape interval,
                # not the process lifetime. Lifetime counters/histograms can hide
                # a fresh outage behind hours of healthy historical traffic.
                avail = (1.0 - err_rate) * 100.0 if d_total > 0 else 100.0
                interval_buckets: dict[float, float] = {}
                bucket_reset = False
                for le, cumulative in r['buckets'].items():
                    cursor_key = f'_bucket:{le!r}'
                    previous_bucket = await self._get_cursor(cursor_route_key, cursor_key)
                    delta_bucket = float(cumulative) - previous_bucket if previous_bucket is not None else 0.0
                    if delta_bucket < 0:
                        bucket_reset = True
                    interval_buckets[le] = max(0.0, delta_bucket)
                    if self.persist_state:
                        await self._put_cursor(cursor_route_key, cursor_key, float(cumulative))
                if bucket_reset:
                    interval_buckets = {le: 0.0 for le in interval_buckets}
                p95 = _hist_quantile(interval_buckets, 0.95) * 1000.0
                p99 = _hist_quantile(interval_buckets, 0.99) * 1000.0

                resource_signals[route_key] = {
                    'app.http.rps': rps,
                    'app.http.error_rate': err_rate,
                    'app.http.p95_ms': p95,
                    'app.http.p99_ms': p99,
                    'app.http.availability': avail,
                }
                if self.persist_state:
                    await self._put_cursor(cursor_route_key, '_total', cum_total)
                    await self._put_cursor(cursor_route_key, '_5xx', cum_5xx)

                agg_total += cum_total
                agg_5xx += cum_5xx
                agg_rps += rps
                agg_req_delta += d_total
                agg_err_delta += d_5xx
                for le, c in interval_buckets.items():
                    combined_buckets[le] = combined_buckets.get(le, 0.0) + c

            agg_avail = (1.0 - agg_err_delta / agg_req_delta) * 100.0 if agg_req_delta > 0 else 100.0
            agg_p95 = _hist_quantile(combined_buckets, 0.95) * 1000.0
            agg_p99 = _hist_quantile(combined_buckets, 0.99) * 1000.0

            global_req_delta += agg_req_delta
            global_err_delta += agg_err_delta
            global_rps += agg_rps
            global_p95 = max(global_p95, agg_p95)
            global_p99 = max(global_p99, agg_p99)
            global_availability = min(global_availability, agg_avail)
            if self.persist_state:
                await self._put_cursor(scrape_cursor_key, 'ts', now)

        if not any_ok:
            signals['app.up'] = 0.0
        else:
            # Error rate is a request-weighted aggregate, not the sum of each
            # source's percentage. Percentiles are intentionally conservative
            # (max) because the sources may represent different app instances.
            signals.update({
                'app.up': 1.0,
                'app.http.rps': global_rps,
                'app.http.error_rate': (global_err_delta / global_req_delta) if global_req_delta > 0 else 0.0,
                'app.http.p95_ms': global_p95,
                'app.http.p99_ms': global_p99,
                'app.http.availability': global_availability,
            })
            if process_count:
                signals.update({
                    'process.target.alive': 1.0,
                    'process.target.count': process_count,
                    'process.target.cpu_percent_sum': process_cpu_percent,
                    'process.target.rss_bytes_sum': process_rss_bytes,
                    'process.target.virtual_memory_bytes_sum': process_virtual_memory_bytes,
                    'process.target.open_fds_sum': process_open_fds,
                    'process.target.uptime_seconds': process_uptime_seconds,
                })
        return CollectResult(name=self.name, ok=any_ok, signals=signals, resource_signals=resource_signals, resources=resources)

    async def query(self) -> ToolResult:
        result = await self.collect()
        routes = [
            {'route': k, **{m: round(v, 3) for m, v in vals.items()}}
            for k, vals in result.resource_signals.items()
        ]
        return ToolResult(
            ok=result.ok,
            summary=f'采集 {len(result.resource_signals)} 个路由的 API 指标' if result.ok else '未能抓取任何指标源',
            data={'signals': result.signals, 'routes': routes, 'sources': result.resources.get('sources', [])},
        )
