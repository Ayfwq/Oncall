"""Stable V1 read-only tool surface exposed to the language model.

The language model receives the complete JSON argument contract on every decision
turn. Project/incident scope is injected by the runtime and is intentionally not
part of tool arguments, preventing the model from escaping its current project.
"""
from __future__ import annotations

from typing import Any

TOOL_SPECS: dict[str, dict[str, Any]] = {
    "query_host_metrics": {
        "description": "读取当前宿主机 CPU、内存、磁盘、网络与 IO 摘要。只读。",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "query_metric_history": {
        "description": "查询某个监测指标的历史采样趋势，用于判断突发、持续或恢复。只读。",
        "parameters": {
            "type": "object",
            "properties": {
                "metric": {"type": "string", "description": "signal key，例如 host.cpu.percent"},
                "hours": {"type": "integer", "minimum": 1, "maximum": 168, "default": 1},
            },
            "required": ["metric"],
            "additionalProperties": False,
        },
    },
    "query_processes": {
        "description": "读取被监控项目相关进程及 CPU/内存/PID/父进程/命令行等诊断信息。只读。",
        "parameters": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 30}},
            "additionalProperties": False,
        },
    },
    "query_logs": {
        "description": "从项目配置的日志源按关键词/级别读取最近日志明细。只读。",
        "parameters": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "default": ""},
                "level": {"type": "string", "description": "error/warning/info/debug 或空字符串", "default": ""},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100},
            },
            "additionalProperties": False,
        },
    },
    "query_containers": {
        "description": "读取项目配置的 Docker 容器状态、health、CPU/内存、重启次数。只读。",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "query_database": {
        "description": "读取项目 PostgreSQL 健康、连接、长/慢查询、锁和死锁信息。只读。",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "query_service_health": {
        "description": "主动请求项目配置的 HTTP health/service endpoint，返回状态码和延迟。只读。",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "search_knowledge": {
        "description": "检索 Oncall 运维知识库/SOP。用于操作手册、排障步骤和处置依据，不代表实时事实。",
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
    """Return a stable serializable list suitable for the LLM prompt/audit UI."""
    return [
        {"name": name, "description": spec["description"], "parameters": spec["parameters"]}
        for name, spec in TOOL_SPECS.items()
    ]


def validate_tool_args(name: str, args: Any) -> tuple[bool, str | None]:
    """Small dependency-free validator for the stable V1 top-level JSON tool schemas.

    The public schemas are intentionally simple (object + string/integer properties),
    so a full jsonschema runtime is unnecessary in V1. Runtime scope fields are not
    accepted from the model.
    """
    if name not in TOOL_SPECS:
        return False, 'unknown tool'
    if not isinstance(args, dict):
        return False, 'tool_args must be an object'
    schema = TOOL_SPECS[name]['parameters']
    properties = schema.get('properties', {})
    required = set(schema.get('required', []))
    missing = [key for key in required if key not in args]
    if missing:
        return False, f"missing required arguments: {', '.join(sorted(missing))}"
    if schema.get('additionalProperties') is False:
        extra = [key for key in args if key not in properties]
        if extra:
            return False, f"unexpected arguments: {', '.join(sorted(extra))}"
    for key, value in args.items():
        spec = properties.get(key)
        if not spec:
            continue
        type_ = spec.get('type')
        if type_ == 'string':
            if not isinstance(value, str):
                return False, f'{key} must be a string'
            if len(value) < int(spec.get('minLength', 0)):
                return False, f'{key} is too short'
        elif type_ == 'integer':
            if isinstance(value, bool) or not isinstance(value, int):
                return False, f'{key} must be an integer'
            if 'minimum' in spec and value < spec['minimum']:
                return False, f"{key} must be >= {spec['minimum']}"
            if 'maximum' in spec and value > spec['maximum']:
                return False, f"{key} must be <= {spec['maximum']}"
    return True, None
