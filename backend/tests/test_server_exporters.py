import pytest
from oncall.integrations.server_exporters import ServerExportersIntegration
from prometheus_client.parser import text_string_to_metric_families


def parse(text: str):
    return [sample for family in text_string_to_metric_families(text) for sample in family.samples]


def test_node_exporter_metrics_are_normalized():
    samples = parse(
        """
# TYPE node_cpu_seconds_total counter
node_cpu_seconds_total{cpu="0",mode="idle"} 80
node_cpu_seconds_total{cpu="0",mode="user"} 20
# TYPE node_memory_MemTotal_bytes gauge
node_memory_MemTotal_bytes 1000
node_memory_MemAvailable_bytes 250
node_filesystem_size_bytes{mountpoint="/",fstype="ext4"} 1000
node_filesystem_avail_bytes{mountpoint="/",fstype="ext4"} 100
node_load1 1.5
node_disk_read_bytes_total{device="sda"} 1200
node_disk_written_bytes_total{device="sda"} 800
node_network_receive_bytes_total{device="eth0"} 500
node_network_transmit_bytes_total{device="eth0"} 300
"""
    )
    signals, resources = ServerExportersIntegration._node_signals(samples)
    assert signals['host.exporter.up'] == 1
    assert signals['host.cpu.percent'] == pytest.approx(20)
    assert signals['host.memory.percent'] == 75
    assert signals['host.disk.usage_percent'] == 90
    assert resources['counters']['net_rx_bytes'] == 500


def test_dcgm_metrics_are_optional_and_normalized():
    samples = parse(
        """
DCGM_FI_DEV_GPU_UTIL{gpu="0",UUID="GPU-a",modelName="A10"} 75
DCGM_FI_DEV_FB_USED{gpu="0",UUID="GPU-a",modelName="A10"} 900
DCGM_FI_DEV_FB_FREE{gpu="0",UUID="GPU-a",modelName="A10"} 100
DCGM_FI_DEV_GPU_TEMP{gpu="0",UUID="GPU-a",modelName="A10"} 70
DCGM_FI_DEV_POWER_USAGE{gpu="0",UUID="GPU-a",modelName="A10"} 120
"""
    )
    signals, resources, meta = ServerExportersIntegration._gpu_signals(samples)
    assert signals['host.gpu.available'] == 1
    assert signals['host.gpu.memory_percent'] == 90
    assert signals['host.gpu.temperature_celsius'] == 70
    assert resources['gpu:GPU-a']['host.gpu.power_watts'] == 120
    assert meta['devices'][0]['name'] == 'A10'


def test_wrong_prometheus_endpoint_is_rejected():
    with pytest.raises(ValueError, match='Node Exporter'):
        ServerExportersIntegration._node_signals(parse('some_other_metric 1'))
    with pytest.raises(ValueError, match='DCGM'):
        ServerExportersIntegration._gpu_signals(parse('some_other_metric 1'))


@pytest.mark.asyncio
async def test_exporter_scrape_never_uses_environment_proxy(monkeypatch):
    options = {}

    class Response:
        text = 'node_cpu_seconds_total{cpu="0",mode="idle"} 1'
        content = text.encode()

        def raise_for_status(self):
            return None

    class Client:
        def __init__(self, **kwargs):
            options.update(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url):
            return Response()

    monkeypatch.setattr('oncall.integrations.server_exporters.httpx.AsyncClient', Client)
    await ServerExportersIntegration(None)._scrape('http://127.0.0.1:9100/metrics')
    assert options['trust_env'] is False
