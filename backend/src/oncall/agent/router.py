from __future__ import annotations

from typing import Any


CASUAL_MARKERS = ('你好', '您好', '嗨', 'hello', 'hi', '你是谁', '什么模型', '你是什么')
REALTIME_MARKERS = ('当前', '现在', '最近', '实时', '状态', '日志', '指标', 'cpu', '内存', '磁盘', '进程', '容器', '健康', 'health', '接口')
OPS_MARKERS = ('docker', 'linux', 'postgres', '数据库', '备份', '部署', '安装', '故障', '异常', '日志', '监控', '服务', '容器', '进程', '网络', '磁盘', 'cpu', '内存', '告警', '排查', '运维', '恢复')
INCIDENT_MARKERS = ('告警', 'incident', '根因', '证据', '恢复了吗', '是否恢复', '这个问题', '影响', '为什么会这样', '当前状态')


def classify_intent(message: str, *, project_id: str | None, incident_id: str | None, mode: str = 'chat') -> dict[str, Any]:
    """Return an auditable first-pass route without calling tools or the LLM."""
    text = ' '.join(str(message or '').strip().lower().split())
    if incident_id and mode in ('investigate', 'deep'):
        return _route('incident_investigation', True, True, True, bool(project_id), True, '监控事件触发调查')
    if incident_id and mode in ('follow_up', 'chat') and (not any(marker in text for marker in OPS_MARKERS) or any(marker in text for marker in INCIDENT_MARKERS)):
        return _route('incident_followup', True, True, True, bool(project_id), True, '当前会话关联 Incident')
    if any(marker in text for marker in CASUAL_MARKERS) and not any(marker in text for marker in OPS_MARKERS):
        return _route('casual_chat', False, False, False, False, False, '问候或身份类问题')
    is_ops = any(marker in text for marker in OPS_MARKERS)
    realtime = any(marker in text for marker in REALTIME_MARKERS)
    if realtime:
        if not project_id:
            return _route('clarification', is_ops, False, False, True, True, '实时查询需要明确项目')
        return _route('project_query', True, True, False, True, True, '问题包含项目实时状态或诊断信号')
    if is_ops:
        return _route('ops_qa', True, True, False, False, False, '运维问题需要知识库增强')
    return _route('casual_chat', False, False, False, False, False, '未识别为运维问题')


def _route(intent: str, is_ops: bool, knowledge: bool, incident: bool, project: bool, realtime: bool, reason: str) -> dict[str, Any]:
    return {
        'intent': intent,
        'route_confidence': 0.9 if intent not in ('casual_chat', 'clarification') else 0.8,
        'route_reason': reason,
        'is_ops_related': is_ops,
        'requires_knowledge': knowledge,
        'requires_project': project,
        'requires_incident': incident,
        'requires_realtime': realtime,
        'clarification_question': '请先选择或绑定要查询的监控项目。' if intent == 'clarification' else None,
    }
