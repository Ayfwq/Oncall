from oncall.agent.tool_contracts import validate_tool_args


def test_tool_args_reject_scope_escape_and_bad_types():
    ok, _ = validate_tool_args('query_current_metrics', {'project_id': 'escape'})
    assert not ok
    ok, _ = validate_tool_args('query_metric_history', {'metrics': ['host.cpu.percent'], 'minutes': 0})
    assert not ok
    ok, _ = validate_tool_args('query_metric_history', {'metrics': [30]})
    assert not ok
    ok, _ = validate_tool_args('query_database_health', {'checks': ['not-a-check']})
    assert not ok
    ok, _ = validate_tool_args('query_runtime_resources', {'checks': ['cpu', 'cpu']})
    assert not ok


def test_tool_args_accept_contract_defaults_and_required_values():
    assert validate_tool_args('query_current_metrics', {}) == (True, None)
    assert validate_tool_args('query_service_health', {}) == (True, None)
    assert validate_tool_args('query_metric_history', {'metrics': ['host.cpu.percent'], 'minutes': 30}) == (True, None)
    assert validate_tool_args('query_runtime_resources', {'checks': ['cpu', 'memory'], 'top_n': 5}) == (True, None)
    assert validate_tool_args('search_knowledge', {'query': 'CPU 高 处理方案', 'top_k': 5}) == (True, None)
