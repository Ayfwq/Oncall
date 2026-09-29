import pytest
from oncall.agent.graph import OncallGraphRuntime


@pytest.mark.asyncio
async def test_incident_report_says_embedding_is_unavailable():
    runtime = OncallGraphRuntime.__new__(OncallGraphRuntime)
    runtime.emit = None
    result = await runtime.finalize(
        {
            "mode": "investigate",
            "decision": {},
            "incident_context": {"severity": "warning", "summary": "CPU 告警"},
            "evidence": [],
            "knowledge_refs": [],
            "knowledge_status": "unavailable",
            "knowledge_error": "Embedding 模型不可用（HTTP 402），本次未检索知识库。",
        }
    )
    assert "Embedding 模型不可用（HTTP 402）" in result["final_response"]
    assert "本报告未引用知识库" in result["final_response"]
    assert result["diagnosis"]["knowledge_refs"] == []
