"""P1 verification: exercise the real PrometheusIntegration end-to-end.

Run from backend/ with:  PYTHONPATH=src .venv_test/Scripts/python.exe ../scripts/verify_p1_prometheus.py
(uses the minimal verification venv; does NOT require torch/langgraph/Milvus)
"""
from __future__ import annotations

import asyncio
import threading
import time
from uuid import uuid4

import uvicorn
from fastapi import FastAPI, HTTPException, Response

try:
    from prometheus_fastapi_instrumentator import Instrumentator
except Exception as exc:  # pragma: no cover
    Instrumentator = None
    _INST_ERR = exc

from oncall.integrations.prometheus import PrometheusIntegration


# --------------------------------------------------------------------------- #
# Synthetic, controllable metrics server (emulates instrumentator text format)
# --------------------------------------------------------------------------- #
METRICS_TEXT = [""]


def build_text(routes: dict) -> str:
    lines = []
    for handler, info in routes.items():
        lines.append("# TYPE http_requests_total counter")
        lines.append(f'http_requests_total{{handler="{handler}",method="GET",status="200"}} {info["ok"]}')
        if info["err5xx"]:
            lines.append(f'http_requests_total{{handler="{handler}",method="GET",status="500"}} {info["err5xx"]}')
        lines.append("# TYPE http_request_duration_seconds histogram")
        for le, c in info["buckets"]:
            lines.append(f'http_request_duration_seconds_bucket{{handler="{handler}",method="GET",status="200",le="{le}"}} {c}')
        lines.append(f'http_request_duration_seconds_count{{handler="{handler}",method="GET",status="200"}} {info["ok"]}')
        lines.append(f'http_request_duration_seconds_sum{{handler="{handler}",method="GET",status="200"}} {info["ok"] * 0.02:.3f}')
        if info["err5xx"]:
            lines.append("# TYPE http_request_duration_seconds histogram")
            lines.append(f'http_request_duration_seconds_bucket{{handler="{handler}",method="GET",status="500",le="0.01"}} {int(info["err5xx"] * 0.6)}')
            lines.append(f'http_request_duration_seconds_bucket{{handler="{handler}",method="GET",status="500",le="0.05"}} {info["err5xx"]}')
            lines.append(f'http_request_duration_seconds_bucket{{handler="{handler}",method="GET",status="500",le="+Inf"}} {info["err5xx"]}')
            lines.append(f'http_request_duration_seconds_count{{handler="{handler}",method="GET",status="500"}} {info["err5xx"]}')
            lines.append(f'http_request_duration_seconds_sum{{handler="{handler}",method="GET",status="500"}} {info["err5xx"] * 0.03:.3f}')
    return "\n".join(lines) + "\n"


V0 = build_text({
    "/health": {"ok": 95, "err5xx": 5, "buckets": [("0.01", 60), ("0.05", 90), ("0.1", 95), ("+Inf", 95)]},
    "/slow": {"ok": 20, "err5xx": 0, "buckets": [("0.01", 10), ("0.05", 18), ("0.1", 20), ("+Inf", 20)]},
})
V1 = build_text({
    "/health": {"ok": 185, "err5xx": 15, "buckets": [("0.01", 150), ("0.05", 180), ("0.1", 185), ("+Inf", 185)]},
    "/slow": {"ok": 120, "err5xx": 0, "buckets": [("0.01", 110), ("0.05", 118), ("0.1", 120), ("+Inf", 120)]},
})
V2 = build_text({  # counter reset: lower totals than previous scrape
    "/health": {"ok": 50, "err5xx": 0, "buckets": [("0.01", 30), ("0.05", 45), ("0.1", 50), ("+Inf", 50)]},
    "/slow": {"ok": 50, "err5xx": 0, "buckets": [("0.01", 30), ("0.05", 45), ("0.1", 50), ("+Inf", 50)]},
})

syn_app = FastAPI()
@syn_app.get("/metrics")
def metrics():
    return Response(METRICS_TEXT[0], media_type="text/plain; version=0.0.4; charset=utf-8")


# --------------------------------------------------------------------------- #
# Real instrumentator server (framework compatibility test)
# --------------------------------------------------------------------------- #
real_app = FastAPI()
if Instrumentator is not None:
    Instrumentator().instrument(real_app).expose(real_app)
@real_app.get("/health")
def health():
    return {"ok": True}
@real_app.get("/boom")
def boom():
    raise HTTPException(status_code=500, detail="boom")
@real_app.get("/slow")
def slow():
    time.sleep(0.02)
    return {"ok": True}


def run_server(app, port):
    cfg = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    srv = uvicorn.Server(cfg)
    threading.Thread(target=srv.run, daemon=True).start()
    return srv


class FakeCursor:
    def __init__(self, v):
        self.last_value = v


class FakeSession:
    """In-memory stand-in for the async DB session used by the integration."""
    def __init__(self):
        self.store = {}
    async def get(self, model, key):
        rk, mk = key[1], key[2]
        if (rk, mk) in self.store:
            return FakeCursor(self.store[(rk, mk)])
        return None
    def add(self, obj):
        self.store[(obj.resource_key, obj.metric_key)] = obj.last_value


RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, cond, detail))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))


async def main():
    syn_srv = run_server(syn_app, 9091)
    real_srv = run_server(real_app, 9092)
    for srv in (syn_srv, real_srv):
        for _ in range(100):
            if srv.started:
                break
            await asyncio.sleep(0.05)

    from oncall.application.dtos import MetricsSourceDTO

    # ---- 1. first scrape: no baseline -> rps/error_rate zero, percentiles/availability real
    METRICS_TEXT[0] = V0
    integ = PrometheusIntegration(FakeSession(), uuid4(), [MetricsSourceDTO(name="demo", url="http://127.0.0.1:9091/metrics", route_label="handler")])
    r1 = await integ.collect()
    check("first scrape ok", r1.ok is True, str(r1.error))
    check("app.up == 1 on success", r1.signals.get("app.up") == 1.0, str(r1.signals.get("app.up")))
    rh = r1.resource_signals.get("route:/health", {})
    check("availability(/health) == 95.0", abs(rh.get("app.http.availability", -1) - 95.0) < 1e-6, str(rh.get("app.http.availability")))
    check("rps == 0 on first scrape (no baseline)", rh.get("app.http.rps") == 0.0, str(rh.get("app.http.rps")))
    check("error_rate == 0 on first scrape", rh.get("app.http.error_rate") == 0.0, str(rh.get("app.http.error_rate")))
    check("p95_ms > 0", rh.get("app.http.p95_ms", 0) > 0, str(rh.get("app.http.p95_ms")))
    check("p99_ms >= p95_ms", rh.get("app.http.p99_ms", 0) >= rh.get("app.http.p95_ms", 0), f"p95={rh.get('app.http.p95_ms')} p99={rh.get('app.http.p99_ms')}")

    # ---- 2. second scrape after +100 requests over ~1s -> rate computed
    await asyncio.sleep(1.0)
    METRICS_TEXT[0] = V1
    r2 = await integ.collect()
    rh2 = r2.resource_signals.get("route:/health", {})
    check("rps(/health) ~ 100", abs(rh2.get("app.http.rps", -1) - 100.0) < 40.0, str(rh2.get("app.http.rps")))
    check("error_rate(/health) ~ 0.10", abs(rh2.get("app.http.error_rate", -1) - 0.10) < 0.03, str(rh2.get("app.http.error_rate")))
    check("availability(/health) dropped to 92.5", abs(rh2.get("app.http.availability", -1) - 92.5) < 0.5, str(rh2.get("app.http.availability")))
    check("aggregate rps ~ 200", abs(r2.signals.get("app.http.rps", -1) - 200.0) < 80.0, str(r2.signals.get("app.http.rps")))
    check("aggregate error_rate ~ 0.05", abs(r2.signals.get("app.http.error_rate", -1) - 0.05) < 0.02, str(r2.signals.get("app.http.error_rate")))
    check("per-route /slow present", "route:/slow" in r2.resource_signals, str(list(r2.resource_signals)))

    # ---- 3. counter reset -> no negative spike
    METRICS_TEXT[0] = V2
    r3 = await integ.collect()
    rh3 = r3.resource_signals.get("route:/health", {})
    check("rps == 0 after counter reset", rh3.get("app.http.rps") == 0.0, str(rh3.get("app.http.rps")))
    check("error_rate == 0 after counter reset", rh3.get("app.http.error_rate") == 0.0, str(rh3.get("app.http.error_rate")))

    # ---- 4. unreachable source -> app down, ok False
    dead = PrometheusIntegration(FakeSession(), uuid4(), [MetricsSourceDTO(name="dead", url="http://127.0.0.1:9/metrics")])
    rd = await dead.collect()
    check("dead source -> ok False", rd.ok is False, str(rd.error))
    check("dead source -> app.up == 0", rd.signals.get("app.up") == 0.0, str(rd.signals.get("app.up")))

    # ---- 5. real FastAPI instrumentator compatibility
    if Instrumentator is not None:
        async with __import__("httpx").AsyncClient() as cli:
            for _ in range(6):
                await cli.get("http://127.0.0.1:9092/health")
            for _ in range(3):
                try:
                    await cli.get("http://127.0.0.1:9092/boom")
                except Exception:
                    pass
            for _ in range(4):
                await cli.get("http://127.0.0.1:9092/slow")
        real_integ = PrometheusIntegration(FakeSession(), uuid4(), [MetricsSourceDTO(name="real", url="http://127.0.0.1:9092/metrics", route_label="handler")])
        rr = await real_integ.collect()
        check("real instrumentator scrape ok", rr.ok is True, str(rr.error))
        check("real: route:/health found", "route:/health" in rr.resource_signals, str(list(rr.resource_signals)))
        check("real: route:/boom found", "route:/boom" in rr.resource_signals, str(list(rr.resource_signals)))
        check("real: route:/slow found", "route:/slow" in rr.resource_signals, str(list(rr.resource_signals)))
        boom_sig = rr.resource_signals.get("route:/boom", {})
        check("real: /boom availability ~ 0 (all 500)", boom_sig.get("app.http.availability", 100) < 1.0, str(boom_sig.get("app.http.availability")))
    else:
        check("real instrumentator test", False, f"instrumentator import failed: {_INST_ERR}")

    syn_srv.should_exit = True
    real_srv.should_exit = True

    failed = [n for n, c, _ in RESULTS if not c]
    print("\n=== SUMMARY ===")
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    if failed:
        print("FAILED:", failed)
        raise SystemExit(1)
    print("ALL P1 PROMETHEUS CHECKS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
