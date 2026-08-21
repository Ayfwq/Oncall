import pytest
from oncall.agent.model_gateway import MockProvider


@pytest.mark.asyncio
async def test_mock_chat_uses_rag_first():
    d=await MockProvider().decide({'mode':'chat','user_message':'PostgreSQL 备份怎么做','called_tools':[],'project_id':None})
    assert d.action=='tool' and d.tool_name=='search_knowledge'

@pytest.mark.asyncio
async def test_mock_chat_without_project_is_general_qa():
    d=await MockProvider().decide({
        'mode':'chat',
        'user_message':'PostgreSQL 备份怎么做',
        'called_tools':['search_knowledge'],
        'project_id':None,
        'evidence':[{'summary':'备份应定期验证恢复'}],
    })
    assert d.action=='final'
    assert '通用运维问答模式' in d.answer
    assert '绑定该会话' not in d.answer
