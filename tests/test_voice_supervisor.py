from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from voice_code.voice.command_assistant import CommandAssistant
from voice_code.voice.orchestrator import VoiceOrchestrator
from voice_code.voice.types import (
    SUPERVISOR_FAILED_TEXT,
    CommandDecision,
    CommandKind,
    SupervisorAction,
)


def _dummy_wav_bytes() -> bytes:
    return b"RIFF" + (b"\x00" * 128)


class _FakeModel:
    def __init__(self, responses: list[str]) -> None:
        self._responses = responses

    async def ainvoke(self, messages: list[object], **_: object) -> AIMessage:
        return AIMessage(content=self._responses.pop(0))


class _FakeBindableModel:
    def __init__(self, responses: list[AIMessage]) -> None:
        self._responses = responses
        self.bound_tools: list[object] = []

    def bind_tools(self, tools: list[object]) -> _FakeBindableModel:
        self.bound_tools = tools
        return self

    async def ainvoke(self, messages: list[object], **_: object) -> AIMessage:
        return self._responses.pop(0)


class _FakeBridge:
    def __init__(self, results: list[str]) -> None:
        self.calls: list[str] = []
        self._results = results

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def run_turn(self, text: str) -> str:
        self.calls.append(text)
        return self._results.pop(0)

    async def summarize_for_speech(self, text: str) -> str:
        return text

    def interrupt(self) -> None:
        return None


class _FakeTtsClient:
    def __init__(self) -> None:
        self.texts: list[str] = []

    async def synthesize_text(self, text: str, seed: int | None = None) -> bytes:
        self.texts.append(text)
        return _dummy_wav_bytes()

    async def health_check(self) -> bool:
        return True


class _FakePlayer:
    def stop(self) -> None:
        return None

    def play_wav_bytes(self, audio: bytes) -> None:
        return None


class _FakeRecorder:
    def on_segment(self, callback) -> None:
        self._on_segment = callback

    def on_raw_frame(self, callback) -> None:
        self._on_raw_frame = callback

    def open_mic(self) -> None:
        return None

    def close_mic(self) -> None:
        return None

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None

    def drain_queue(self) -> None:
        return None


class _FakeClassifier:
    def __init__(self, decision: CommandDecision) -> None:
        self._decision = decision

    async def classify(self, text: str) -> CommandDecision:
        return self._decision


class _FakeCommandAssistant:
    def __init__(self, initial, reviews: list) -> None:
        self.initial = initial
        self.reviews = reviews
        self.user_calls: list[str] = []
        self.review_calls: list[tuple[str, str, str, int]] = []
        self.context_updates: list[str] = []

    async def decide_user_turn(self, text: str):
        self.user_calls.append(text)
        return self.initial

    async def review_agent_result(
        self,
        *,
        user_text: str,
        task: str,
        agent_result: str,
        dispatch_count: int,
    ):
        self.review_calls.append((user_text, task, agent_result, dispatch_count))
        return self.reviews.pop(0)

    def apply_context_update(self, text: str) -> None:
        self.context_updates.append(text)


async def _transcribe_project_structure(_: bytes) -> str:
    return "查看项目结构"


async def _transcribe_status(_: bytes) -> str:
    return "状态"


@pytest.mark.asyncio
async def test_command_assistant_parses_dispatch_json() -> None:
    model = _FakeModel(
        [
            """```json
{"action":"dispatch","task":"查看项目结构","reason":"这是编程任务","context_update":"用户要查看项目结构"}
```"""
        ]
    )
    assistant = CommandAssistant(model=model)

    action = await assistant.decide_user_turn("查看项目结构")

    assert action.action == SupervisorAction.DISPATCH
    assert action.task == "查看项目结构"
    assert action.context_update == "用户要查看项目结构"


@pytest.mark.asyncio
async def test_command_assistant_invalid_json_falls_back_to_fail() -> None:
    assistant = CommandAssistant(model=_FakeModel(["not json at all"]))

    action = await assistant.decide_user_turn("帮我看下")

    assert action.action == SupervisorAction.FAIL
    assert action.message


@pytest.mark.asyncio
async def test_command_assistant_can_use_tools_before_returning_json() -> None:
    calls: list[str] = []

    @tool
    def todo_write(tasks: str) -> str:
        """Update a lightweight todo list for tests."""
        calls.append(tasks)
        return f"Tasks updated: {tasks}"

    model = _FakeBindableModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "call_1",
                        "name": "todo_write",
                        "args": {"tasks": "- [in_progress] 查看项目结构"},
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(
                content=(
                    '{"action":"dispatch","task":"查看项目结构","reason":"需要查看代码库结构",'
                    '"context_update":"已经建好任务清单"}'
                )
            ),
        ]
    )
    assistant = CommandAssistant(model=model, tools=[todo_write])

    action = await assistant.decide_user_turn("查看项目结构")

    assert calls == ["- [in_progress] 查看项目结构"]
    assert action.action == SupervisorAction.DISPATCH
    assert action.task == "查看项目结构"


@pytest.mark.asyncio
async def test_orchestrator_uses_command_assistant_before_agent_bridge() -> None:
    initial = SimpleNamespace(
        action=SupervisorAction.DISPATCH,
        message="",
        task="查看项目结构",
        reason="需要查看文件",
        context_update="用户想看项目结构",
    )
    review = SimpleNamespace(
        action=SupervisorAction.FINISH,
        message="哥哥，我已经看完项目结构了",
        task="",
        reason="结果已足够",
        context_update="已经完成项目结构查看",
    )
    command_assistant = _FakeCommandAssistant(initial=initial, reviews=[review])
    bridge = _FakeBridge(results=["项目结构如下"])
    tts = _FakeTtsClient()
    orchestrator = VoiceOrchestrator(
        stt_client=SimpleNamespace(transcribe_audio=_transcribe_project_structure),
        tts_client=tts,
        agent_bridge=cast(Any, bridge),
        classifier=cast(
            Any,
            _FakeClassifier(
            CommandDecision(kind=CommandKind.AGENT_COMMAND, text="查看项目结构")
            ),
        ),
        wakeword=None,
        segment_recorder=cast(Any, _FakeRecorder()),
        audio_player=cast(Any, _FakePlayer()),
        command_assistant=cast(Any, command_assistant),
    )
    orchestrator._loop = asyncio.get_running_loop()
    await orchestrator._enter_listening()

    await orchestrator.handle_speech_segment(_dummy_wav_bytes())

    assert command_assistant.user_calls == ["查看项目结构"]
    assert bridge.calls == ["查看项目结构"]
    assert command_assistant.review_calls == [
        ("查看项目结构", "查看项目结构", "项目结构如下", 1)
    ]
    assert tts.texts[-1] == "哥哥，我已经看完项目结构了"


@pytest.mark.asyncio
async def test_orchestrator_continue_then_finish() -> None:
    initial = SimpleNamespace(
        action=SupervisorAction.DISPATCH,
        message="",
        task="先查看项目结构",
        reason="需要先了解目录",
        context_update="开始任务",
    )
    continue_review = SimpleNamespace(
        action=SupervisorAction.CONTINUE,
        message="",
        task="继续打开核心入口文件确认",
        reason="需要进一步确认入口",
        context_update="继续查看入口文件",
    )
    finish_review = SimpleNamespace(
        action=SupervisorAction.FINISH,
        message="哥哥，我确认了项目结构和入口文件。",
        task="",
        reason="信息已足够",
        context_update="任务完成",
    )
    command_assistant = _FakeCommandAssistant(
        initial=initial,
        reviews=[continue_review, finish_review],
    )
    bridge = _FakeBridge(results=["第一轮结果", "第二轮结果"])
    tts = _FakeTtsClient()
    orchestrator = VoiceOrchestrator(
        stt_client=SimpleNamespace(transcribe_audio=_transcribe_project_structure),
        tts_client=tts,
        agent_bridge=cast(Any, bridge),
        classifier=cast(
            Any,
            _FakeClassifier(
            CommandDecision(kind=CommandKind.AGENT_COMMAND, text="查看项目结构")
            ),
        ),
        wakeword=None,
        segment_recorder=cast(Any, _FakeRecorder()),
        audio_player=cast(Any, _FakePlayer()),
        command_assistant=cast(Any, command_assistant),
    )
    orchestrator._loop = asyncio.get_running_loop()
    await orchestrator._enter_listening()

    await orchestrator.handle_speech_segment(_dummy_wav_bytes())

    assert bridge.calls == ["先查看项目结构", "继续打开核心入口文件确认"]
    assert tts.texts[-1] == "哥哥，我确认了项目结构和入口文件。"


@pytest.mark.asyncio
async def test_orchestrator_dispatch_limit_fails_cleanly() -> None:
    initial = SimpleNamespace(
        action=SupervisorAction.DISPATCH,
        message="",
        task="任务一",
        reason="开始",
        context_update="开始任务",
    )
    continue_review = SimpleNamespace(
        action=SupervisorAction.CONTINUE,
        message="",
        task="继续任务",
        reason="还不够",
        context_update="继续任务",
    )
    command_assistant = _FakeCommandAssistant(
        initial=initial,
        reviews=[continue_review, continue_review, continue_review],
    )
    bridge = _FakeBridge(results=["结果1", "结果2", "结果3"])
    tts = _FakeTtsClient()
    orchestrator = VoiceOrchestrator(
        stt_client=SimpleNamespace(transcribe_audio=_transcribe_project_structure),
        tts_client=tts,
        agent_bridge=cast(Any, bridge),
        classifier=cast(
            Any,
            _FakeClassifier(
            CommandDecision(kind=CommandKind.AGENT_COMMAND, text="查看项目结构")
            ),
        ),
        wakeword=None,
        segment_recorder=cast(Any, _FakeRecorder()),
        audio_player=cast(Any, _FakePlayer()),
        command_assistant=cast(Any, command_assistant),
    )
    orchestrator._loop = asyncio.get_running_loop()
    await orchestrator._enter_listening()

    await orchestrator.handle_speech_segment(_dummy_wav_bytes())

    assert bridge.calls == ["任务一", "继续任务", "继续任务"]
    assert tts.texts[-1] == SUPERVISOR_FAILED_TEXT


@pytest.mark.asyncio
async def test_orchestrator_control_command_bypasses_command_assistant() -> None:
    command_assistant = _FakeCommandAssistant(initial=None, reviews=[])
    bridge = _FakeBridge(results=[])
    tts = _FakeTtsClient()
    orchestrator = VoiceOrchestrator(
        stt_client=SimpleNamespace(transcribe_audio=_transcribe_status),
        tts_client=tts,
        agent_bridge=cast(Any, bridge),
        classifier=cast(
            Any,
            _FakeClassifier(
            CommandDecision(
                kind=CommandKind.CONTROL_COMMAND,
                text="状态",
                control_name="report_status",
            )
            ),
        ),
        wakeword=None,
        segment_recorder=cast(Any, _FakeRecorder()),
        audio_player=cast(Any, _FakePlayer()),
        command_assistant=cast(Any, command_assistant),
    )
    orchestrator._loop = asyncio.get_running_loop()
    await orchestrator._enter_listening()

    await orchestrator.handle_speech_segment(_dummy_wav_bytes())

    assert command_assistant.user_calls == []
    assert bridge.calls == []
    assert tts.texts[-1] == "我在听"
