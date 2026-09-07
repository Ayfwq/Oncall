from __future__ import annotations

from uuid import uuid4

import pytest
from oncall.application.dtos import MetricsSourceDTO
from oncall.integrations.prometheus import PrometheusIntegration


class WindowedPrometheus(PrometheusIntegration):
    def __init__(self, observations):
        super().__init__(None, uuid4(), [MetricsSourceDTO(name='app', url='http://example.test/metrics')])
        self.observations = iter(observations)
        self.cursors = {}

    async def _scrape_source(self, src):
        return next(self.observations)

    async def _get_cursor(self, resource_key, metric_key):
        return self.cursors.get((resource_key, metric_key))

    async def _put_cursor(self, resource_key, metric_key, value):
        self.cursors[(resource_key, metric_key)] = value


def observation(total, errors, buckets):
    return {
        'ok': True,
        'routes': {'/quote': {'total': total, 'err5xx': errors, 'buckets': buckets}},
        'process': {},
        'meta': {'name': 'app', 'url': 'http://example.test/metrics', 'ok': True, 'routes': 1, 'process_metrics': False},
    }


@pytest.mark.asyncio
async def test_api_availability_and_latency_use_current_scrape_window(monkeypatch):
    integration = WindowedPrometheus([
        observation(100, 50, {0.1: 50, 1.0: 100, float('inf'): 100}),
        observation(200, 50, {0.1: 150, 1.0: 200, float('inf'): 200}),
    ])
    times = iter((1000.0, 1010.0))
    monkeypatch.setattr('oncall.integrations.prometheus.time.time', lambda: next(times))

    first = await integration.collect()
    second = await integration.collect()

    assert first.signals['app.http.rps'] == 0.0
    assert second.signals['app.http.rps'] == pytest.approx(10.0)
    assert second.signals['app.http.error_rate'] == 0.0
    assert second.signals['app.http.availability'] == 100.0
    assert second.signals['app.http.p95_ms'] == pytest.approx(100.0)

