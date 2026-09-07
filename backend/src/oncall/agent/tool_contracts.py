"""Stable read-only tool surface exposed to the language model.

The language model receives the complete JSON argument contract on every decision
turn. Project/incident scope is injected by the runtime and is intentionally not
part of tool arguments, preventing the model from escaping its current project.
"""
from __future__ import annotations

from typing import Any

TOOL_SPECS: dict[str, dict[str, Any]] = {
    "query_current_metrics": {
        "description": "读取当前远程 Python 项目的服务器、健康检查、Prometheus 与可选 GPU 指标。只读。",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "query_metric_history": {
        "description": "查询某个监测指标的历史采样趋势，用于判断突发、持续或恢复。只读。",
        "parameters": {
            "type": "object",
            "properties": {
                "metric": {"type": "string", "description": "signal key，例如 host.cpu.percent"},
                "resource_key": {"type": "string", "description": "可选的具体目标资源标识；不填查询项目级聚合"},
                "hours": {"type": "integer", "minimum": 1, "maximum": 168, "default": 1},
            },
            "required": ["metric"],
            "additionalProperties": False,
        },
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
    """Small dependency-free validator for the stable top-level JSON tool schemas.

    The public schemas are intentionally simple (object + string/integer properties),
    so a full jsonschema runtime is unnecessary here. Runtime scope fields are not
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
