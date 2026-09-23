from oncall.collector import app as collector


class FakeContainer:
    def __init__(self, name, image, project, service, host_port=None, lines=None):
        self.id = f"id-{name}"
        self.name = name
        self.status = "running"
        self._lines = lines or []
        self.attrs = {
            "Config": {
                "Image": image,
                "Labels": {
                    "com.docker.compose.project": project,
                    "com.docker.compose.service": service,
                },
            },
            "NetworkSettings": {
                "Ports": {"8000/tcp": [{"HostPort": str(host_port)}]} if host_port else {},
            },
        }

    def logs(self, **kwargs):
        return ("\n".join(self._lines) + "\n").encode()


class FakeContainers:
    def __init__(self, rows):
        self._rows = rows

    def list(self):
        return self._rows


class FakeDocker:
    def __init__(self, rows):
        self.containers = FakeContainers(rows)

    def close(self):
        pass


def _containers():
    return [
        FakeContainer(
            "tradingagents-tradingagents-1",
            "tradingagents-api:latest",
            "tradingagents",
            "tradingagents",
            host_port=5000,
            lines=["2026-09-18T10:00:00Z INFO ready", "2026-09-18T10:01:00Z ERROR quote failed"],
        ),
        FakeContainer(
            "tradingagents-worker-1",
            "tradingagents-worker:latest",
            "tradingagents",
            "worker",
            host_port=5001,
            lines=["2026-09-18T10:01:00Z INFO worker ready"],
        ),
        FakeContainer(
            "tradingagents-postgres-1",
            "postgres:16",
            "tradingagents",
            "postgres",
            host_port=5432,
            lines=["database system is ready"],
        ),
    ]


def test_external_https_port_can_miss_but_manual_compose_scope_finds_container(monkeypatch):
    monkeypatch.setattr(collector, "_docker_client", lambda: FakeDocker(_containers()))

    automatic = collector._logs_sync(
        collector.LogQuery(target_urls=["https://trading.hellowq.icu/metrics"])
    )
    manual = collector._logs_sync(
        collector.LogQuery(
            target_urls=["https://trading.hellowq.icu/metrics"],
            compose_project="tradingagents",
            services=["tradingagents"],
        )
    )

    assert automatic["ok"] is False
    assert automatic["error"] == "没有根据 Metrics 端口匹配到业务容器"
    assert manual["ok"] is True
    assert manual["containers"] == ["tradingagents-tradingagents-1"]
    assert manual["error_count"] == 1


def test_manual_compose_scope_does_not_call_port_discovery(monkeypatch):
    monkeypatch.setattr(collector, "_docker_client", lambda: FakeDocker(_containers()))

    def forbidden(_urls):
        raise AssertionError("manual Compose matching must not use URL-port discovery")

    monkeypatch.setattr(collector, "_discover_sync", forbidden)
    result = collector._logs_sync(
        collector.LogQuery(compose_project="tradingagents", services=["worker"])
    )

    assert result["ok"] is True
    assert result["containers"] == ["tradingagents-worker-1"]
