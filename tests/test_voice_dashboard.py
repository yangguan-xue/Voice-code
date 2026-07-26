"""Voice mode dashboard model and Textual page tests."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest
from textual.widgets import Static

from voice_code.agent.types import AgentEvent, EventType
from voice_code.voice.dashboard import (
    VoiceDashboard,
    VoiceDelegationItem,
    VoiceModeApp,
)
from voice_code.voice.orchestrator import VoiceOrchestrator
from voice_code.voice.timing import TurnTiming
from voice_code.voice.types import CommandDecision, CommandKind, VoiceState
from voice_code.voice_cli import parse_args


def test_dashboard_collects_voice_turn_information() -> None:
    dashboard = VoiceDashboard(profile="step", backend="Step Fun")

    dashboard.start()
    dashboard.set_state(VoiceState.LISTENING)
    dashboard.update(VoiceState.LISTENING, -18.5)
    dashboard.set_transcript("帮我检查当前项目")
    dashboard.append_agent_text("正在分析")
    dashboard.append_agent_text("，请稍候。")
    dashboard.set_notice("已收到语音指令")
    dashboard.set_delegations(
        [
            VoiceDelegationItem(
                task_id="task-12345678",
                title="后台整理文档",
                status="running",
            )
        ]
    )
    dashboard.set_timing(
        TurnTiming(
            stt_wall_seconds=0.4,
            agent_wall_seconds=1.2,
            tts_wall_seconds=0.3,
        )
    )

    snapshot = dashboard.snapshot()

    assert snapshot.running is True
    assert snapshot.state is VoiceState.LISTENING
    assert snapshot.rms_db == -18.5
    assert snapshot.transcript == "帮我检查当前项目"
    assert snapshot.agent_output == "正在分析，请稍候。"
    assert snapshot.notice == "已收到语音指令"
    assert snapshot.delegations[0].task_id == "task-12345678"
    assert snapshot.timing is not None
    assert snapshot.timing.total_wall_seconds == pytest.approx(1.9)


def test_dashboard_snapshot_is_detached_from_future_updates() -> None:
    dashboard = VoiceDashboard()
    first = dashboard.snapshot()

    dashboard.set_transcript("新的语音")

    assert first.transcript == ""
    assert dashboard.snapshot().transcript == "新的语音"


def test_voice_cli_uses_full_screen_dashboard_by_default() -> None:
    assert parse_args([]).plain_display is False
    assert parse_args(["--plain-display"]).plain_display is True


@pytest.mark.asyncio
async def test_voice_mode_app_renders_dashboard_snapshot() -> None:
    dashboard = VoiceDashboard(profile="step", backend="Step Fun")
    dashboard.set_state(VoiceState.WORKING)
    dashboard.update(VoiceState.WORKING, -12.0)
    dashboard.set_transcript("实现语音模式页面")
    dashboard.append_agent_text("正在修改三个模块。")
    dashboard.set_delegations(
        [VoiceDelegationItem(task_id="task-abcd", title="检查文档", status="completed")]
    )
    dashboard.set_timing(TurnTiming(stt_wall_seconds=0.2, agent_wall_seconds=1.1))

    app = VoiceModeApp(dashboard, refresh_interval=0.01)
    async with app.run_test(size=(110, 36)) as pilot:
        await pilot.pause()

        assert "执行中" in str(app.query_one("#voice-state", Static).render())
        assert "实现语音模式页面" in str(app.query_one("#transcript-body", Static).render())
        assert "正在修改三个模块" in str(app.query_one("#agent-body", Static).render())
        assert "检查文档" in str(app.query_one("#delegation-body", Static).render())
        assert "1.30s" in str(app.query_one("#timing-body", Static).render())
        assert "-12.0 dB" in str(app.query_one("#audio-meter", Static).render())


@pytest.mark.asyncio
async def test_voice_mode_app_refreshes_after_dashboard_changes() -> None:
    dashboard = VoiceDashboard()
    app = VoiceModeApp(dashboard, refresh_interval=0.01)

    async with app.run_test(size=(90, 30)) as pilot:
        dashboard.set_state(VoiceState.SPEAKING)
        dashboard.set_transcript("读出执行结果")
        await pilot.pause()

        assert "播报中" in str(app.query_one("#voice-state", Static).render())
        assert "读出执行结果" in str(app.query_one("#transcript-body", Static).render())


@pytest.mark.asyncio
async def test_voice_mode_keyboard_controls_call_orchestrator() -> None:
    class Controls:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def pause_or_resume(self) -> None:
            self.calls.append("pause")

        async def interrupt(self) -> None:
            self.calls.append("interrupt")

    dashboard = VoiceDashboard()
    controls = Controls()
    app = VoiceModeApp(dashboard, controls=controls, refresh_interval=0.01)

    async with app.run_test(size=(90, 30)) as pilot:
        await pilot.press("space", "escape")
        await pilot.pause()

        assert controls.calls == ["pause", "interrupt"]


class _Bridge:
    def on_event(self, callback) -> None:
        self.callback = callback

    def interrupt(self) -> None:
        self.interrupted = True


class _Recorder:
    def on_segment(self, callback) -> None:
        self.segment_callback = callback

    def on_raw_frame(self, callback) -> None:
        self.frame_callback = callback

    def open_mic(self) -> None:
        return None

    def close_mic(self) -> None:
        return None

    def drain_queue(self) -> None:
        return None

    def start(self) -> None:
        return None


class _IgnoreClassifier:
    async def classify(self, text: str) -> CommandDecision:
        return CommandDecision(kind=CommandKind.IGNORE, text=text)


@pytest.mark.asyncio
async def test_orchestrator_publishes_events_to_dashboard() -> None:
    async def transcribe(_: bytes) -> str:
        return "检查项目架构"

    dashboard = VoiceDashboard()
    bridge = _Bridge()
    orchestrator = VoiceOrchestrator(
        stt_client=cast(Any, SimpleNamespace(transcribe_audio=transcribe)),
        tts_client=cast(Any, SimpleNamespace()),
        agent_bridge=cast(Any, bridge),
        classifier=cast(Any, _IgnoreClassifier()),
        segment_recorder=cast(Any, _Recorder()),
        audio_player=cast(Any, SimpleNamespace()),
        display=dashboard,
        console_output=False,
    )
    orchestrator._loop = asyncio.get_running_loop()

    await orchestrator._enter_listening()
    await orchestrator.handle_speech_segment(b"RIFF" + b"\x00" * 64)
    await orchestrator._on_agent_event(
        AgentEvent(type=EventType.TEXT, turn=1, content="架构分析中")
    )
    orchestrator._timing._current.agent_wall_seconds = 1.4
    orchestrator._finish_turn()

    snapshot = dashboard.snapshot()
    assert snapshot.state is VoiceState.LISTENING
    assert snapshot.transcript == "检查项目架构"
    assert snapshot.agent_output == "架构分析中"
    assert snapshot.timing is not None
    assert snapshot.timing.agent_wall_seconds == 1.4
