import pytest
from oncall.agent.model_gateway import MockProvider

@pytest.mark.asyncio
async def test_mock_chat_uses_rag_first():
    d=await MockProvider().decide({'mode':'chat','user_message':'PostgreSQL 备份怎么做','called_tools':[],'project_id':None})
    assert d.action=='tool' and d.tool_name=='search_knowledge'
