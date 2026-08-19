from oncall.agent.tool_contracts import ALLOWED_TOOLS, TOOL_SPECS, public_tool_specs


def test_every_tool_has_llm_visible_description_and_json_schema():
    assert set(TOOL_SPECS)==set(ALLOWED_TOOLS)
    for name,spec in TOOL_SPECS.items():
        assert spec['description'].strip(),name
        schema=spec['parameters']
        assert schema['type']=='object'
        # Runtime injects scope; the LLM is not allowed to choose it.
        assert 'project_id' not in schema.get('properties',{})
        assert 'incident_id' not in schema.get('properties',{})
        assert schema.get('additionalProperties') is False
    assert len(public_tool_specs())==8
