"""Thread-safe voice presentation model and full-screen Textual dashboard."""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from typing import Protocol

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Static

from voice_code.voice.timing import TurnTiming
from voice_code.voice.types import VoiceState


@dataclass(frozen=True)
class VoiceDelegationItem:
    """Compact background-task row shown by the voice dashboard."""

    task_id: str
    title: str
    status: str


@dataclass(frozen=True)
class VoiceDashboardSnapshot:
    """Immutable view of the current voice session."""

    running: bool = False
    state: VoiceState = VoiceState.SLEEPING
    rms_db: float = -60.0
    peak_db: float = -60.0
    transcript: str = ""
    agent_output: str = ""
    notice: str = "等待唤醒词"
    timing: TurnTiming | None = None
    delegations: tuple[VoiceDelegationItem, ...] = ()
    profile: str = ""
    backend: str = ""


class VoiceControls(Protocol):
    """Keyboard actions exposed by the voice orchestrator."""

    async def pause_or_resume(self) -> None: ...

    async def interrupt(self) -> None: ...


class VoiceDashboard:
    """Collect voice events in a lock-protected presentation snapshot."""

    def __init__(self, *, profile: str = "", backend: str = "") -> None:
        self._lock = threading.RLock()
        self._snapshot = VoiceDashboardSnapshot(profile=profile, backend=backend)

    def start(self) -> None:
        self._mutate(running=True)

    def stop(self) -> None:
        self._mutate(running=False)

    def set_state(self, state: VoiceState) -> None:
        self._mutate(state=state)

    def update(self, state: VoiceState, rms_db: float) -> None:
        with self._lock:
            peak_db = max(self._snapshot.peak_db - 0.25, rms_db)
            self._snapshot = replace(
                self._snapshot,
                state=state,
                rms_db=max(-60.0, min(0.0, rms_db)),
                peak_db=max(-60.0, min(0.0, peak_db)),
            )

    def set_transcript(self, text: str) -> None:
        self._mutate(transcript=text.strip())

    def clear_agent_output(self) -> None:
        self._mutate(agent_output="")

    def set_agent_output(self, text: str) -> None:
        self._mutate(agent_output=text.strip())

    def append_agent_text(self, text: str) -> None:
        if not text:
            return
        with self._lock:
            output = (self._snapshot.agent_output + text)[-12_000:]
            self._snapshot = replace(self._snapshot, agent_output=output)

    def set_notice(self, text: str) -> None:
        self._mutate(notice=text.strip())

    def set_timing(self, timing: TurnTiming) -> None:
        self._mutate(timing=replace(timing))

    def set_delegations(self, items: list[VoiceDelegationItem]) -> None:
        self._mutate(delegations=tuple(items[-5:]))

    def snapshot(self) -> VoiceDashboardSnapshot:
        with self._lock:
            timing = replace(self._snapshot.timing) if self._snapshot.timing is not None else None
            return replace(
                self._snapshot,
                timing=timing,
                delegations=tuple(self._snapshot.delegations),
            )

    def _mutate(self, **changes: object) -> None:
        with self._lock:
            self._snapshot = replace(self._snapshot, **changes)


_STATE_VIEW: dict[VoiceState, tuple[str, str, str]] = {
    VoiceState.SLEEPING: ("○", "休眠中", "说出唤醒词开始"),
    VoiceState.LISTENING: ("●", "聆听中", "请直接说出你的开发任务"),
    VoiceState.WORKING: ("◆", "执行中", "Agent 正在处理刚才的任务"),
    VoiceState.SPEAKING: ("◖", "播报中", "正在播放执行结果"),
    VoiceState.PAUSED: ("Ⅱ", "已暂停", "按空格继续聆听"),
}

_TASK_STATUS = {
    "accepted": "待执行",
    "running": "执行中",
    "completed": "已完成",
    "failed": "失败",
    "cancelled": "已取消",
    "accepted_into_workspace": "已合入",
    "discarded": "已放弃",
}


class VoiceModeApp(App[None]):
    """Dedicated terminal page for a live voice coding session."""

    TITLE = "语码 · Voice Code"
    BINDINGS = [
        ("q", "quit", "退出"),
        ("space", "pause_or_resume", "暂停 / 继续"),
        ("escape", "interrupt", "中断当前任务"),
    ]
    CSS = """
    Screen {
        background: #000000;
        color: #ebe7de;
        padding: 1 2;
    }

    #voice-topbar {
        height: 3;
        width: 100%;
        border-bottom: solid #242220;
    }

    #voice-title {
        width: 1fr;
        height: 2;
        color: #ebe7de;
        text-style: bold;
        content-align: left middle;
    }

    #voice-runtime {
        width: auto;
        min-width: 28;
        height: 2;
        color: #817b72;
        content-align: right middle;
    }

    #hero {
        height: 9;
        margin-top: 1;
        padding: 0 2;
        border: round #4b1d1d;
        background: #0b0808;
    }

    #voice-state {
        height: 2;
        color: #f16d64;
        text-style: bold;
        content-align: center middle;
    }

    #voice-hint {
        height: 1;
        color: #a59f95;
        content-align: center middle;
    }

    #audio-meter {
        height: 2;
        margin-top: 1;
        color: #e49564;
        content-align: center middle;
    }

    #voice-notice {
        height: 1;
        color: #817b72;
        content-align: center middle;
    }

    #content-grid {
        height: 1fr;
        margin-top: 1;
    }

    #primary-column {
        width: 2fr;
        margin-right: 1;
    }

    #secondary-column {
        width: 1fr;
        min-width: 32;
    }

    .panel {
        height: 1fr;
        padding: 1 2;
        border: solid #242220;
        background: #070707;
    }

    #transcript-panel {
        height: 7;
        margin-bottom: 1;
    }

    #delegation-panel {
        margin-bottom: 1;
    }

    .panel-title {
        height: 1;
        color: #817b72;
        text-style: bold;
    }

    .panel-body {
        height: 1fr;
        color: #d7d2c8;
        margin-top: 1;
        overflow: hidden auto;
        scrollbar-background: #070707;
        scrollbar-color: #242220;
    }

    #agent-body {
        color: #ebe7de;
    }

    #voice-footer {
        height: 2;
        margin-top: 1;
        border-top: solid #1b1b1b;
        color: #817b72;
        content-align: center middle;
    }
    """

    def __init__(
        self,
        dashboard: VoiceDashboard,
        *,
        controls: VoiceControls | None = None,
        refresh_interval: float = 0.1,
    ) -> None:
        super().__init__()
        self._dashboard = dashboard
        self._controls = controls
        self._refresh_interval = refresh_interval
        self._last_snapshot: VoiceDashboardSnapshot | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="voice-topbar"):
            yield Static("语码  /  VOICE CODE", id="voice-title")
            yield Static("", id="voice-runtime")
        with Vertical(id="hero"):
            yield Static("", id="voice-state")
            yield Static("", id="voice-hint")
            yield Static("", id="audio-meter")
            yield Static("", id="voice-notice")
        with Horizontal(id="content-grid"):
            with Vertical(id="primary-column"):
                with Vertical(classes="panel", id="transcript-panel"):
                    yield Static("你刚才说", classes="panel-title")
                    yield Static("", id="transcript-body", classes="panel-body")
                with Vertical(classes="panel", id="agent-panel"):
                    yield Static("Agent 输出", classes="panel-title")
                    yield Static("", id="agent-body", classes="panel-body")
            with Vertical(id="secondary-column"):
                with Vertical(classes="panel", id="delegation-panel"):
                    yield Static("后台任务", classes="panel-title")
                    yield Static("", id="delegation-body", classes="panel-body")
                with Vertical(classes="panel", id="timing-panel"):
                    yield Static("本轮耗时", classes="panel-title")
                    yield Static("", id="timing-body", classes="panel-body")
        yield Static("Q 退出     SPACE 暂停 / 继续     ESC 中断当前任务", id="voice-footer")

    def on_mount(self) -> None:
        self._refresh()
        self.set_interval(self._refresh_interval, self._refresh)

    def _refresh(self) -> None:
        snapshot = self._dashboard.snapshot()
        if snapshot == self._last_snapshot:
            return
        self._last_snapshot = snapshot
        icon, label, hint = _STATE_VIEW[snapshot.state]
        self.query_one("#voice-state", Static).update(f"{icon}  {label}")
        self.query_one("#voice-hint", Static).update(hint)
        self.query_one("#audio-meter", Static).update(self._format_meter(snapshot))
        self.query_one("#voice-notice", Static).update(snapshot.notice or " ")
        runtime = "  ·  ".join(part for part in (snapshot.backend, snapshot.profile) if part)
        self.query_one("#voice-runtime", Static).update(runtime or "LOCAL SESSION")
        self.query_one("#transcript-body", Static).update(
            snapshot.transcript or "等待语音输入…"
        )
        self.query_one("#agent-body", Static).update(
            snapshot.agent_output or "Agent 输出会实时显示在这里。"
        )
        self.query_one("#delegation-body", Static).update(
            self._format_delegations(snapshot.delegations)
        )
        self.query_one("#timing-body", Static).update(self._format_timing(snapshot.timing))

    @staticmethod
    def _format_meter(snapshot: VoiceDashboardSnapshot) -> str:
        ratio = max(0.0, min(1.0, (snapshot.rms_db + 60.0) / 60.0))
        width = 36
        filled = round(width * ratio)
        meter = "━" * filled + "─" * (width - filled)
        level = "静音" if snapshot.rms_db <= -59.5 else f"{snapshot.rms_db:.1f} dB"
        return f"{meter}   {level}"

    @staticmethod
    def _format_delegations(items: tuple[VoiceDelegationItem, ...]) -> str:
        if not items:
            return "当前没有后台任务。"
        rows = []
        for item in reversed(items):
            status = _TASK_STATUS.get(item.status, item.status)
            rows.append(f"{status:<5}  {item.title}\n       {item.task_id[:12]}")
        return "\n\n".join(rows)

    @staticmethod
    def _format_timing(timing: TurnTiming | None) -> str:
        if timing is None:
            return "完成一轮语音任务后显示。"
        return (
            f"识别 {timing.stt_wall_seconds:.2f}s  ·  "
            f"分类 {timing.classify_wall_seconds:.2f}s\n"
            f"Agent {timing.agent_wall_seconds:.2f}s\n"
            f"合成 {timing.tts_wall_seconds:.2f}s  ·  "
            f"播放 {timing.playback_wall_seconds:.2f}s\n"
            f"合计 {timing.total_wall_seconds:.2f}s"
        )

    async def action_pause_or_resume(self) -> None:
        if self._controls is not None:
            await self._controls.pause_or_resume()

    async def action_interrupt(self) -> None:
        if self._controls is not None:
            await self._controls.interrupt()


__all__ = [
    "VoiceControls",
    "VoiceDashboard",
    "VoiceDashboardSnapshot",
    "VoiceDelegationItem",
    "VoiceModeApp",
]
