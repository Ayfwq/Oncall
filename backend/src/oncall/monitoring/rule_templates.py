from __future__ import annotations

"""Auto-discovery of white-box HTTP metrics and framework-aware rule templates.

Given a Prometheus text-format ``/metrics`` endpoint, this module:

* scrapes and parses the exposition,
* recognises which Python instrumentation library produced it (by metric-name
  fingerprint), and
* proposes a :class:`MetricsSourceDTO` plus a starter set of
  :class:`MonitoringRuleDTO` rows that map the library's HTTP golden signals
  onto the platform's ``app.*`` signals.

The goal is a one-click "detect my app" flow: paste the metrics URL, review the
suggested source + rules, apply. No manual threshold tuning required.
"""

from dataclasses import dataclass
from typing import Any

import httpx
from prometheus_client.parser import text_string_to_metric_families

from oncall.application.dtos import MetricsSourceDTO, MonitoringRuleDTO

# Route-label candidates, in priority order. The first one present on a sample
# wins; different frameworks expose the route under different label names.
_ROUTE_LABEL_CANDIDATES = ('handler', 'uri', 'view', 'path', 'route')


@dataclass(frozen=True)
class FrameworkProfile:
    key: str
    label: str
    # Metric-name markers that uniquely identify this framework's exposition.
    markers: tuple[str, ...]
    # Default route label for this library.
    route_label: str
    # Minimal snippet to add to the user's app so it starts emitting /metrics.
    snippet: str


# Fingerprints ordered most-specific first. prometheus-fastapi-instrumentator
# and starlette-exporter both emit "http_request_duration_seconds", so the
# counter names disambiguate them.
FRAMEWORK_PROFILES: dict[str, FrameworkProfile] = {
    'prometheus-fastapi-instrumentator': FrameworkProfile(
        key='prometheus-fastapi-instrumentator',
        label='FastAPI · prometheus-fastapi-instrumentator',
        markers=('http_requests', 'http_request_duration_seconds'),
        route_label='handler',
        snippet=(
            "from prometheus_fastapi_instrumentator import Instrumentator\n"
            "Instrumentator().instrument(app).expose(app, endpoint='/metrics')"
        ),
    ),
    'starlette-exporter': FrameworkProfile(
        key='starlette-exporter',
        label='Starlette · starlette-exporter',
        markers=('starlette_requests_total', 'starlette_request_duration_seconds'),
        route_label='handler',
        snippet=(
            "from starlette_exporter import HandleTimelineRequest, PrometheusMiddleware\n"
            "app.add_middleware(PrometheusMiddleware, app_name='myapp',\n"
            "                    group_paths=True, endpoint='/metrics')\n"
            "app.add_route('/metrics', HandleTimelineRequest())"
        ),
    ),
    'django-prometheus': FrameworkProfile(
        key='django-prometheus',
        label='Django · django-prometheus',
        markers=('django_http_requests_total_before_middleware',),
        route_label='view',
        snippet=(
            "# settings.py\n"
            "INSTALLED_APPS += ['django_prometheus']\n"
            "MIDDLEWARE = ['django_prometheus.middleware.PrometheusBeforeMiddleware',\n"
            "              *MIDDLEWARE,\n"
            "              'django_prometheus.middleware.PrometheusAfterMiddleware']\n"
            "from django_prometheus.exports import ExportToDjangoView\n"
            "urlpatterns = [path('metrics', ExportToDjangoView.as_view()), *urlpatterns]"
        ),
    ),
    'flask-prometheus': FrameworkProfile(
        key='flask-prometheus',
        label='Flask · prometheus-flask-exporter',
        markers=('flask_http_request_total', 'flask_http_request_duration_seconds'),
        route_label='path',
        snippet=(
            "from prometheus_flask_exporter import PrometheusMetrics\n"
            "metrics = PrometheusMetrics(app)\n"
            "metrics.expose_endpoint = '/metrics'"
        ),
    ),
    'gunicorn': FrameworkProfile(
        key='gunicorn',
        label='Gunicorn (worker metrics)',
        markers=('gunicorn_requests',),
        route_label='',
        snippet="# gunicorn exposes worker metrics automatically with --enable-stdio;\n# scrape the gunicorn master metrics endpoint.",
    ),
}


def detect_framework(metric_names: set[str]) -> FrameworkProfile:
    """Return the most specific framework whose markers appear in ``metric_names``.

    Falls back to a generic profile when nothing matches.
    """
    for profile in FRAMEWORK_PROFILES.values():
        if any(marker in metric_names for marker in profile.markers):
            return profile
    return FrameworkProfile(
        key='generic',
        label='通用 Prometheus 指标',
        markers=(),
        route_label='handler',
        snippet="# 无法识别具体库；请确认 /metrics 暴露了请求数计数器和延迟直方图。",
    )


def parse_metrics_text(text: str) -> dict[str, Any]:
    """Parse a Prometheus text exposition into metric family names + route labels.

    Returns ``{'metric_names': set[str], 'routes': list[str]}``. Routes are the
    distinct values of the first matching route label encountered across all
    samples (capped to avoid an explosion on apps with thousands of paths).
    """
    metric_names: set[str] = set()
    routes: set[str] = set()
    for family in text_string_to_metric_families(text):
        metric_names.add(family.name)
        for sample in family.samples:
            for label in _ROUTE_LABEL_CANDIDATES:
                value = sample.labels.get(label)
                if value:
                    routes.add(value)
                    break
    return {'metric_names': metric_names, 'routes': sorted(routes)[:100]}


def build_suggested_source(
    profile: FrameworkProfile,
    url: str,
    *,
    auth_type: str = 'none',
    token: str | None = None,
    route_label: str | None = None,
    scrape_timeout_ms: int = 5000,
) -> MetricsSourceDTO:
    """Build the MetricsSourceDTO proposed for a freshly discovered endpoint."""
    return MetricsSourceDTO(
        name=profile.label.split(' · ')[0].split(' ')[0].lower() or 'app',
        url=url,
        auth_type=auth_type,  # type: ignore[arg-type]
        token=token,
        route_label=route_label or profile.route_label or 'handler',
        scrape_timeout_ms=scrape_timeout_ms,
        enabled=True,
    )


def build_suggested_rules(profile: FrameworkProfile, routes: list[str]) -> list[MonitoringRuleDTO]:
    """Propose starter rules mapping the library's HTTP signals onto ``app.*``.

    Two tiers:
    * Global rules (``resource_key='default'``) cover aggregate availability,
      error rate, and latency percentiles for the whole application.
    * Per-route rules (``resource_key='route:/x'``) tighten error-rate / availability
      watching on every discovered route (distinct resource keys, so they never
      collide with the global rule for the same metric).
    """
    rules: list[MonitoringRuleDTO] = [
        # App unreachable (all scrape targets dead) -> critical.
        MonitoringRuleDTO(
            metric_key='app.up', resource_key='default', operator='==',
            trigger_threshold=0.0, recovery_threshold=0.0, severity='critical',
        ),
        # Aggregate error rate above 10% with meaningful traffic -> warning.
        MonitoringRuleDTO(
            metric_key='app.http.error_rate', resource_key='default', operator='>',
            trigger_threshold=0.10, recovery_threshold=0.05, severity='warning',
            conditions={'all': [
                {'metric_key': 'app.http.rps', 'resource_key': 'default', 'operator': '>', 'threshold': 1.0},
                {'metric_key': 'app.http.error_rate', 'resource_key': 'default', 'operator': '>', 'threshold': 0.10},
            ]},
        ),
        # p95 latency above 800ms -> warning.
        MonitoringRuleDTO(
            metric_key='app.http.p95_ms', resource_key='default', operator='>',
            trigger_threshold=800.0, recovery_threshold=500.0, severity='warning', detection_mode='hybrid',
        ),
        # p99 latency above 2s -> critical.
        MonitoringRuleDTO(
            metric_key='app.http.p99_ms', resource_key='default', operator='>',
            trigger_threshold=2000.0, recovery_threshold=1000.0, severity='critical',
        ),
        # Availability below 95% -> warning.
        MonitoringRuleDTO(
            metric_key='app.http.availability', resource_key='default', operator='<',
            trigger_threshold=95.0, recovery_threshold=99.0, severity='warning',
        ),
    ]
    for route in routes:
        rk = f'route:{route}'
        rules.append(
            MonitoringRuleDTO(
                metric_key='app.http.error_rate', resource_key=rk, operator='>',
                trigger_threshold=0.20, recovery_threshold=0.10, severity='warning',
            )
        )
        rules.append(
            MonitoringRuleDTO(
                metric_key='app.http.availability', resource_key=rk, operator='<',
                trigger_threshold=95.0, recovery_threshold=99.0, severity='warning',
            )
        )
    return rules


async def discover(
    url: str,
    *,
    auth_type: str = 'none',
    token: str | None = None,
    route_label: str | None = None,
    scrape_timeout_ms: int = 5000,
) -> dict[str, Any]:
    """Scrape a /metrics endpoint and return discovery results.

    Never raises on a bad target: the caller surfaces ``scrape_ok=False`` to the
    UI so the user can fix the URL/auth instead of getting a 500.
    """
    headers: dict[str, str] = {}
    if auth_type == 'bearer' and token:
        headers['Authorization'] = f'Bearer {token}'
    elif auth_type == 'basic' and token:
        import base64

        headers['Authorization'] = 'Basic ' + base64.b64encode(token.encode()).decode()
    try:
        async with httpx.AsyncClient(timeout=scrape_timeout_ms / 1000.0) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            text = resp.text
    except Exception as exc:  # noqa: BLE001 — surfaced to UI as scrape_ok=False
        return {
            'scrape_ok': False,
            'error': str(exc),
            'framework': 'generic',
            'framework_label': FRAMEWORK_PROFILES['generic'].label,
            'available_metrics': [],
            'discovered_routes': [],
            'suggested_source': None,
            'suggested_rules': [],
            'snippet': FRAMEWORK_PROFILES['generic'].snippet,
        }

    parsed = parse_metrics_text(text)
    profile = detect_framework(parsed['metric_names'])
    rl = route_label or profile.route_label or 'handler'
    return {
        'scrape_ok': True,
        'error': None,
        'framework': profile.key,
        'framework_label': profile.label,
        'available_metrics': sorted(parsed['metric_names']),
        'discovered_routes': parsed['routes'],
        'suggested_source': build_suggested_source(
            profile, url, auth_type=auth_type, token=token,
            route_label=rl, scrape_timeout_ms=scrape_timeout_ms,
        ).model_dump(mode='json'),
        'suggested_rules': [
            r.model_dump(mode='json') for r in build_suggested_rules(profile, parsed['routes'])
        ],
        'snippet': profile.snippet,
    }
