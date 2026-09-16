"""Stable, read-only diagnostic tools exposed to the language model.

Project and incident scope are injected by the runtime. They are deliberately
absent from model-controlled arguments so a call cannot escape its scope.
"""

from __future__ import annotations

from typing import Any

METRIC_GROUPS = ["host", "service", "application", "process", "logs", "database", "gpu"]
DATABASE_CHECKS = [
    "availability",
    "connections",
    "long_transactions",
    "lock_waits",
    "blocking_chain",
    "deadlocks",
    "replication",
    "slow_queries",
    "cache_hit",
]
RUNTIME_CHECKS = ["status", "cpu", "memory", "restarts", "oom", "processes", "ports"]

TOOL_SPECS: dict[str, dict[str, Any]] = {
    "query_incident_context": {
        "description": "读取当前告警事件、触发信号、规则阈值和已有证据。诊断应优先调用。只读。",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "query_current_metrics": {
        "description": "读取最近一次指标快照；可筛选指标或指标组，必要时才强制实时采集。只读。",
        "parameters": {
            "type": "object",
            "properties": {
                "metrics": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                    "maxItems": 20,
                },
                "groups": {
                    "type": "array",
                    "items": {"type": "string", "enum": METRIC_GROUPS},
                    "maxItems": 7,
                },
                "fresh": {"type": "boolean", "default": False},
            },
            "additionalProperties": False,
        },
    },
    "query_metric_history": {
        "description": "批量查询指标历史并计算当前值、最小/最大/均值和趋势。只读。",
        "parameters": {
            "type": "object",
            "properties": {
                "metrics": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                    "minItems": 1,
                    "maxItems": 20,
                },
                "resource_key": {"type": "string", "minLength": 1},
                "minutes": {"type": "integer", "minimum": 5, "maximum": 10080, "default": 30},
                "include_samples": {"type": "boolean", "default": False},
            },
            "required": ["metrics"],
            "additionalProperties": False,
        },
    },
    "query_service_health": {
        "description": "主动探测一个或全部已配置 HTTP 服务，返回状态码和延迟。只读。",
        "parameters": {
            "type": "object",
            "properties": {
                "endpoint": {"type": "string", "minLength": 1},
                "include_body": {"type": "boolean", "default": False},
            },
            "additionalProperties": False,
        },
    },
    "search_logs": {
        "description": "搜索项目容器 stdout/stderr，并可按服务筛选、聚合错误特征。只读。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "level": {
                    "type": "string",
                    "enum": ["DEBUG", "INFO", "WARN", "WARNING", "ERROR", "CRITICAL", "FATAL"],
                },
                "services": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                    "maxItems": 20,
                },
                "since_minutes": {"type": "integer", "minimum": 1, "maximum": 1440, "default": 30},
                "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 200},
                "group_by_signature": {"type": "boolean", "default": True},
            },
            "additionalProperties": False,
        },
    },
    "query_database_health": {
        "description": "查询 PostgreSQL 可用性、连接、事务、锁、阻塞链、慢 SQL、复制和缓存命中率。只读。",
        "parameters": {
            "type": "object",
            "properties": {
                "checks": {
                    "type": "array",
                    "items": {"type": "string", "enum": DATABASE_CHECKS},
                    "maxItems": 9,
                },
                "slow_query_limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
            },
            "additionalProperties": False,
        },
    },
    "query_runtime_resources": {
        "description": "检查项目容器状态、CPU、内存、重启、OOM、进程和端口。只读。",
        "parameters": {
            "type": "object",
            "properties": {
                "services": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                    "maxItems": 20,
                },
                "checks": {
                    "type": "array",
                    "items": {"type": "string", "enum": RUNTIME_CHECKS},
                    "maxItems": 7,
                },
                "top_n": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
            },
            "additionalProperties": False,
        },
    },
    "search_knowledge": {
        "description": "检索 PulseOps 运维知识库/SOP；用于处置依据，不代表实时事实。只读。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
}

ALLOWED_TOOLS = frozenset(TOOL_SPECS)


def public_tool_specs() -> list[dict[str, Any]]:
    return [
        {"name": n, "description": s["description"], "parameters": s["parameters"]}
        for n, s in TOOL_SPECS.items()
    ]


def _validate_value(key: str, value: Any, spec: dict[str, Any]) -> str | None:
    type_ = spec.get("type")
    if type_ == "string":
        if not isinstance(value, str):
            return f"{key} must be a string"
        if len(value) < int(spec.get("minLength", 0)):
            return f"{key} is too short"
        if "enum" in spec and value not in spec["enum"]:
            return f"{key} must be one of: {', '.join(spec['enum'])}"
    elif type_ == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            return f"{key} must be an integer"
        if "minimum" in spec and value < spec["minimum"]:
            return f"{key} must be >= {spec['minimum']}"
        if "maximum" in spec and value > spec["maximum"]:
            return f"{key} must be <= {spec['maximum']}"
    elif type_ == "boolean":
        if not isinstance(value, bool):
            return f"{key} must be a boolean"
    elif type_ == "array":
        if not isinstance(value, list):
            return f"{key} must be an array"
        if len(value) < int(spec.get("minItems", 0)):
            return f"{key} has too few items"
        if "maxItems" in spec and len(value) > spec["maxItems"]:
            return f"{key} has too many items"
        if len({f"{type(v).__name__}:{v!r}" for v in value}) != len(value):
            return f"{key} contains duplicate items"
        for index, item in enumerate(value):
            error = _validate_value(f"{key}[{index}]", item, spec.get("items", {}))
            if error:
                return error
    return None


def validate_tool_args(name: str, args: Any) -> tuple[bool, str | None]:
    if name not in TOOL_SPECS:
        return False, "unknown tool"
    if not isinstance(args, dict):
        return False, "tool_args must be an object"
    schema = TOOL_SPECS[name]["parameters"]
    properties = schema.get("properties", {})
    missing = sorted(set(schema.get("required", [])) - set(args))
    if missing:
        return False, f"missing required arguments: {', '.join(missing)}"
    if schema.get("additionalProperties") is False:
        extra = sorted(set(args) - set(properties))
        if extra:
            return False, f"unexpected arguments: {', '.join(extra)}"
    for key, value in args.items():
        error = _validate_value(key, value, properties[key])
        if error:
            return False, error
    return True, None
