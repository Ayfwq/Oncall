"""Dependency-free route contract test.

We intentionally inspect the FastAPI module AST instead of importing the app here,
because the build sandbox does not contain asyncpg/LangGraph. Target-machine tests
exercise the live HTTP endpoints through scripts/e2e_smoke.py.
"""
from __future__ import annotations
import ast
from pathlib import Path

EXPECTED={
('GET','/api/health'),('POST','/api/auth/login'),('POST','/api/auth/logout'),('GET','/api/auth/me'),
('POST','/api/auth/password'),
('GET','/api/conversations'),('POST','/api/conversations'),('PATCH','/api/conversations/{cid}'),('DELETE','/api/conversations/{cid}'),('GET','/api/conversations/{cid}/messages'),('POST','/api/conversations/{cid}/messages:stream'),
('GET','/api/projects'),('POST','/api/projects'),('PUT','/api/projects/{pid}'),('GET','/api/projects/{pid}'),('DELETE','/api/projects/{pid}'),('POST','/api/projects/{pid}/test'),('GET','/api/projects/{pid}/snapshot'),
('GET','/api/incidents'),('GET','/api/incidents/{iid}'),('POST','/api/incidents/{iid}/investigate'),('POST','/api/incidents/{iid}/resolve'),('POST','/api/incidents/{iid}/conversation'),('GET','/api/incidents/{iid}/trace'),
('GET','/api/monitoring/metrics'),
('GET','/api/knowledge/documents'),('POST','/api/knowledge/documents'),('GET','/api/knowledge/jobs/{jid}'),('POST','/api/knowledge/documents/{did}/reindex'),('DELETE','/api/knowledge/documents/{did}'),
('POST','/api/dev/incidents/trigger'),('POST','/api/dev/incidents/{iid}/recover'),
('GET','/api/settings/readiness'),('GET','/api/settings/feishu'),('PUT','/api/settings/feishu'),('GET','/api/settings/tool-contracts'),
}


def test_fastapi_route_surface_matches_release_contract():
    path=Path(__file__).parents[1]/'src/oncall/api/main.py'
    tree=ast.parse(path.read_text(encoding='utf-8'))
    actual=set()
    for node in tree.body:
        if not isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):continue
        for dec in node.decorator_list:
            if not isinstance(dec,ast.Call) or not isinstance(dec.func,ast.Attribute):continue
            if not isinstance(dec.func.value,ast.Name) or dec.func.value.id!='app':continue
            if dec.func.attr.lower() not in {'get','post','put','patch','delete'}:continue
            if dec.args and isinstance(dec.args[0],ast.Constant):actual.add((dec.func.attr.upper(),dec.args[0].value))
    assert actual==EXPECTED
