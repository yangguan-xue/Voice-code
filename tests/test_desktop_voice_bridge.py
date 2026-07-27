from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import HumanMessage

from voice_code.agent.types import AgentEvent, EventType
from voice_code.desktop_voice.bridge import DesktopVoiceBridge
from voice_code.desktop_voice.protocol import VoiceBridgeEvent
from voice_code.goals.workspace import GoalWorkspaceManager


class _FakeAgentBridge:
    def __init__(self, result: str = "完成了。") -> None:
        self.result = result
        self.calls: list[str] = []
        self.context_calls: list[list[object] | None] = []
        self.interrupted = False
        self._callback = None

    def on_event(self, callback) -> None:
        self._callback = callback

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def run_turn(self, text: str, **kwargs) -> str:
        self.calls.append(text)
        self.context_calls.append(kwargs.get("context_messages"))
        assert self._callback is not None
        await self._callback(
            AgentEvent(type=EventType.REASONING, turn=1, content="内部思考不能显示。")
        )
        await self._callback(
            AgentEvent(
                type=EventType.TOOL_CALL,
                turn=1,
                tool_name="bash",
                tool_args={"command": "pwd"},
            )
        )
        await self._callback(AgentEvent(type=EventType.TEXT, turn=1, content="我来处理。"))
        return self.result

    async def summarize_for_speech(self, text: str) -> str:
        return text

    def interrupt(self) -> None:
        self.interrupted = True


class _FakeTtsClient:
    async def synthesize_text(self, text: str, seed: int | None = None, **kwargs) -> bytes:
        return b"RIFF" + (b"\x00" * 128)

    async def health_check(self) -> bool:
        return True


class _FakeAudioPlayer:
    def __init__(self) -> None:
        self.played = False
        self.stopped = False

    def play_wav_bytes(self, audio: bytes) -> None:
        self.played = True

    def stop(self) -> None:
        self.stopped = True


@pytest.mark.asyncio
async def test_desktop_voice_bridge_emits_only_voice_safe_turn_events() -> None:
    events: list[VoiceBridgeEvent] = []
    bridge = DesktopVoiceBridge(
        session_id="voice-session",
        agent_bridge=_FakeAgentBridge(),  # type: ignore[arg-type]
        emit=events.append,
    )

    result = await bridge.start_turn("看一下项目")

    assert result.finish_reason == "completed"
    assert [event.event for event in events] == [
        "voice.user.final",
        "voice.state",
        "voice.assistant.delta",
        "voice.assistant.final",
        "voice.state",
    ]
    rendered = " ".join(str(event.payload) for event in events)
    assert "bash" not in rendered
    assert "内部思考" not in rendered
    assert events[0].payload["text"] == "看一下项目"
    assert events[2].payload["text"] == "我来处理。"


@pytest.mark.asyncio
async def test_desktop_voice_bridge_passes_shared_context_to_agent() -> None:
    agent = _FakeAgentBridge()
    bridge = DesktopVoiceBridge(
        session_id="voice-session",
        agent_bridge=agent,  # type: ignore[arg-type]
    )

    await bridge.start_turn(
        "接着说",
        context_messages=[HumanMessage(content="主会话上下文")],
    )

    assert agent.calls == ["接着说"]
    assert agent.context_calls
    assert agent.context_calls[0][0].content == "主会话上下文"  # type: ignore[index,union-attr]


@pytest.mark.asyncio
async def test_desktop_voice_bridge_runs_parallel_turn_in_worktree(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "init")
    _git(repo, "config", "user.name", "Voice Test")
    _git(repo, "config", "user.email", "voice-test@localhost")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "initial")

    created_workspaces: list[str] = []

    async def create_parallel_agent(workspace_path: str):
        created_workspaces.append(workspace_path)
        return _FakeAgentBridge(result="并行完成。")

    bridge = DesktopVoiceBridge(
        session_id="voice-session",
        agent_bridge=_FakeAgentBridge(),  # type: ignore[arg-type]
        worktree_manager=GoalWorkspaceManager(repo),
        parallel_agent_factory=create_parallel_agent,  # type: ignore[arg-type]
    )

    result = await bridge.start_turn(
        "并行改文件",
        mode="parallelWorktree",
        worktree_task_id="voice-test",
    )

    assert result.mode == "parallelWorktree"
    assert result.worktree_task_id == "voice-test"
    assert created_workspaces == [result.worktree_path]
    assert result.worktree_path != str(repo)
    assert Path(result.worktree_path).relative_to(repo) == Path(
        ".reasoning/worktrees/voice-test"
    )


@pytest.mark.asyncio
async def test_desktop_voice_bridge_tts_is_muted_by_default_when_set() -> None:
    player = _FakeAudioPlayer()
    bridge = DesktopVoiceBridge(
        session_id="voice-session",
        agent_bridge=_FakeAgentBridge(),  # type: ignore[arg-type]
        tts_client=_FakeTtsClient(),  # type: ignore[arg-type]
        audio_player=player,  # type: ignore[arg-type]
    )
    bridge.set_tts_muted(True)

    await bridge.start_turn("播报测试")

    assert player.played is False


@pytest.mark.asyncio
async def test_desktop_voice_bridge_interrupts_active_turn() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    class BlockingAgentBridge(_FakeAgentBridge):
        async def run_turn(self, text: str) -> str:
            started.set()
            await release.wait()
            return "done"

    agent = BlockingAgentBridge()
    bridge = DesktopVoiceBridge(
        session_id="voice-session",
        agent_bridge=agent,  # type: ignore[arg-type]
    )
    task = asyncio.create_task(bridge.start_turn("长任务"))
    await asyncio.wait_for(started.wait(), timeout=1)

    assert bridge.interrupt_turn()
    release.set()
    await task

    assert agent.interrupted is True


def _event_payloads(events: list[VoiceBridgeEvent]) -> list[dict[str, Any]]:
    return [event.payload for event in events]


def _git(cwd, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
