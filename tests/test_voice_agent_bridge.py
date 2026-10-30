from __future__ import annotations

from types import SimpleNamespace

import pytest

from voice_code.agent.types import AgentEvent, EventType
from voice_code.voice import agent_bridge as agent_bridge_module
from voice_code.voice.agent_bridge import AgentBridge


@pytest.mark.asyncio
async def test_reasoning_only_agent_turn_is_not_used_as_speech_result(monkeypatch) -> None:
    async def reasoning_only_loop(**_kwargs):
        yield AgentEvent(type=EventType.REASONING, turn=1, content="内部思考")
        yield AgentEvent(type=EventType.FINISH, turn=1, finish_reason="completed")

    monkeypatch.setattr(agent_bridge_module, "agent_loop", reasoning_only_loop)
    bridge = AgentBridge()
    bridge._runtime = SimpleNamespace(
        tools=[],
        prompt="system",
        model=object(),
        fallback_model=None,
        memory_service=None,
        memory_project_key=None,
        transcript_writer=None,
    )

    result = await bridge._execute_turn("执行任务")

    assert result == ""
