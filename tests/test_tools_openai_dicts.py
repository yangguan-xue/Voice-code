"""_tools_to_openai_dicts 的 schema 清洗测试。"""

from __future__ import annotations

from pydantic import BaseModel

from voice_code.agent.loop import _inline_schema_refs, _tools_to_openai_dicts


class _NestedPolicy(BaseModel):
    allow_network: bool = False


class _Args(BaseModel):
    command: str
    policy: _NestedPolicy | None = None


class _FakeTool:
    name = "bash"
    description = "run a command"
    args_schema = _Args


def test_tools_dicts_inline_ref_no_dangling_pointer():
    tools = _tools_to_openai_dicts([_FakeTool()])
    assert len(tools) == 1
    params = tools[0]["function"]["parameters"]
    import json

    blob = json.dumps(params)
    assert "$ref" not in blob
    assert "$defs" not in blob
    # 子模型字段被内联进来
    policy = params["properties"]["policy"]
    assert "allow_network" in blob
    assert isinstance(policy, dict)


def test_inline_schema_refs_keeps_sibling_fields():
    node = {"anyOf": [{"$ref": "#/$defs/X", "default": None}], "title": "Policy"}
    defs = {"X": {"type": "object", "properties": {"a": {"type": "string"}}}}
    out = _inline_schema_refs(node, defs)
    assert out["title"] == "Policy"
    assert out["anyOf"][0]["type"] == "object"
    assert out["anyOf"][0]["default"] is None


def test_tool_without_args_schema_gets_empty_object_params():
    class _Plain:
        name = "write"
        description = "w"

    tools = _tools_to_openai_dicts([_Plain()])
    params = tools[0]["function"]["parameters"]
    assert params["properties"] == {}
