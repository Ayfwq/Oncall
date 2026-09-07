from __future__ import annotations

import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from oncall.bootstrap.config import get_settings

pytestmark = pytest.mark.integration

API = 'http://127.0.0.1:9900'
NODE_METRICS = '''
# TYPE node_cpu_seconds_total counter
node_cpu_seconds_total{cpu="0",mode="idle"} 90
node_cpu_seconds_total{cpu="0",mode="user"} 10
# TYPE node_memory_MemTotal_bytes gauge
node_memory_MemTotal_bytes 1000
# TYPE node_memory_MemAvailable_bytes gauge
node_memory_MemAvailable_bytes 600
# TYPE node_filesystem_size_bytes gauge
node_filesystem_size_bytes{mountpoint="/"} 1000
# TYPE node_filesystem_avail_bytes gauge
node_filesystem_avail_bytes{mountpoint="/"} 700
# TYPE node_load1 gauge
node_load1 0.2
'''
APP_METRICS = '''
# TYPE http_requests_total counter
http_requests_total{handler="/quote",status="200"} 100
# TYPE http_request_duration_seconds histogram
http_request_duration_seconds_bucket{handler="/quote",le="0.1"} 80
http_request_duration_seconds_bucket{handler="/quote",le="1"} 100
http_request_duration_seconds_bucket{handler="/quote",le="+Inf"} 100
http_request_duration_seconds_sum{handler="/quote"} 12
http_request_duration_seconds_count{handler="/quote"} 100
# TYPE process_cpu_seconds_total counter
process_cpu_seconds_total 20
# TYPE process_resident_memory_bytes gauge
process_resident_memory_bytes 104857600
# TYPE process_virtual_memory_bytes gauge
process_virtual_memory_bytes 209715200
# TYPE process_open_fds gauge
process_open_fds 12
# TYPE process_start_time_seconds gauge
process_start_time_seconds 1700000000
'''


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/health':
            body = b'{"ok":true}'
            content_type = 'application/json'
        elif self.path == '/node/metrics':
            body = NODE_METRICS.encode()
            content_type = 'text/plain; version=0.0.4'
        elif self.path == '/app/metrics':
            body = APP_METRICS.encode()
            content_type = 'text/plain; version=0.0.4'
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


@pytest.fixture
def exporter_server():
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}'
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


async def test_server_first_remote_python_onboarding_end_to_end(exporter_server):
    settings = get_settings()
    async with httpx.AsyncClient(base_url=API, timeout=20, trust_env=False) as client:
        login = await client.post('/api/auth/login', json={'username': settings.admin_username, 'password': settings.admin_password})
        assert login.status_code == 200
        server_id = project_id = None
        try:
            created_server = await client.post('/api/servers', json={
                'name': f'e2e-server-{uuid.uuid4().hex[:8]}',
                'node_metrics_url': f'{exporter_server}/node/metrics',
                'gpu_metrics_url': None,
                'enabled': True,
            })
            assert created_server.status_code == 200, created_server.text
            server_id = created_server.json()['id']

            payload = {
                'name': f'e2e-python-{uuid.uuid4().hex[:8]}',
                'server_id': server_id,
                'health_url': f'{exporter_server}/health',
                'metrics_url': f'{exporter_server}/app/metrics',
                'poll_interval': 30,
                'enabled': False,
            }
            tested = await client.post('/api/projects/onboard/python/test', json=payload)
            assert tested.status_code == 200, tested.text
            result = tested.json()
            assert result['ok'] is True
            assert all(x['ok'] for x in result['checks'])
            assert result['capabilities']['host_metrics'] is True
            assert result['capabilities']['http_metrics'] is True
            assert result['capabilities']['process_metrics'] is True
            assert result['capabilities']['gpu_metrics'] is False

            created_project = await client.post('/api/projects/onboard/python', json=payload)
            assert created_project.status_code == 200, created_project.text
            project_id = created_project.json()['id']
            detail = (await client.get(f'/api/projects/{project_id}')).json()
            assert detail['server_id'] == server_id
            rule_keys = {x['metric_key'] for x in detail['rules']}
            assert {'host.cpu.percent', 'app.http.error_rate', 'process.target.rss_bytes_sum'} <= rule_keys
            assert 'host.gpu.temperature_celsius' not in rule_keys
        finally:
            if project_id:
                await client.delete(f'/api/projects/{project_id}')
            if server_id:
                await client.delete(f'/api/servers/{server_id}')
