from __future__ import annotations

import ast
from pathlib import Path

from oncall.application.incident_service import _group_key


def _routes() -> set[tuple[str, str]]:
    path = Path(__file__).parents[1] / "src/oncall/api/main.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    actual: set[tuple[str, str]] = set()
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                continue
            if not isinstance(decorator.func.value, ast.Name) or decorator.func.value.id != "app":
                continue
            if decorator.func.attr.lower() not in {"get", "post", "put", "patch", "delete"}:
                continue
            if decorator.args and isinstance(decorator.args[0], ast.Constant):
                actual.add((decorator.func.attr.upper(), decorator.args[0].value))
    return actual


def test_alertmanager_is_the_external_alert_ingress():
    routes = _routes()

    assert ("POST", "/api/webhooks/alertmanager") in routes
    assert ("GET", "/api/prometheus/projects/{pid}/database-metrics") in routes
    assert ("GET", "/api/prometheus/projects/{pid}/application-metrics/{source_id}") in routes
    assert ("GET", "/api/projects/{pid}/rules/defaults") not in routes


def test_old_local_rule_engine_is_not_part_of_the_source_tree():
    root = Path(__file__).parents[1] / "src/oncall"

    assert not (root / "monitoring/engine.py").exists()
    assert not (root / "monitoring/detector.py").exists()
    assert not (root / "workers/monitor_worker.py").exists()


def test_alertmanager_group_key_ignores_instance_without_explicit_group_key():
    payload = {}
    first = _group_key(
        payload,
        {
            "project_id": "project-1",
            "alertname": "DiskHigh",
            "category": "host",
            "instance": "disk-a",
        },
    )
    second = _group_key(
        payload,
        {
            "project_id": "project-1",
            "alertname": "DiskHigh",
            "category": "host",
            "instance": "disk-b",
        },
    )

    assert first == second
