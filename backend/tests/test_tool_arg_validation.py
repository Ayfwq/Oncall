from oncall.agent.tool_contracts import validate_tool_args


def test_tool_args_reject_scope_escape_and_bad_types():
    ok, _ = validate_tool_args('query_logs', {'project_id': 'escape'})
    assert not ok
    ok, _ = validate_tool_args('query_metric_history', {'metric': 'host.cpu.percent', 'hours': 0})
    assert not ok
    ok, _ = validate_tool_args('query_processes', {'limit': '30'})
    assert not ok


def test_tool_args_accept_contract_defaults_and_required_values():
    assert validate_tool_args('query_host_metrics', {}) == (True, None)
    assert validate_tool_args('query_logs', {'keyword': 'error', 'limit': 50}) == (True, None)
    assert validate_tool_args('search_knowledge', {'query': 'CPU 高 处理方案', 'top_k': 5}) == (True, None)
