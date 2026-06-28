"""语码 · Voice Code — 终端状态显示 + 实时音频动画。"""

from __future__ import annotations

import shutil
import sys
import threading
import time

from voice_code.voice.types import VoiceState

# ── ANSI ──────────────────────────────────────────

_HIDE = "\x1b[?25l"
_SHOW = "\x1b[?25h"
_SAVE = "\x1b[s"
_RESTORE = "\x1b[u"
_EL = "\x1b[K"          # erase line
_CUP = "\x1b[{};{}H"    # cursor position (1-based)

# color palette
_RESET = "\x1b[0m"
_BOLD = "\x1b[1m"
_DIM = "\x1b[2m"
_WHITE = "\x1b[38;2;220;220;220m"
_GREEN = "\x1b[38;2;80;200;120m"
_YELLOW = "\x1b[38;2;240;200;60m"
_RED = "\x1b[38;2;240;100;80m"
_BLUE = "\x1b[38;2;80;160;240m"
_CYAN = "\x1b[38;2;80;220;200m"
_GRAY = "\x1b[38;2;120;120;120m"
_ORANGE = "\x1b[38;2;240;160;60m"
_PURPLE = "\x1b[38;2;180;120;240m"

# equalizer chars (8 levels)
_EQ = " ▁▂▃▄▅▆▇█"

# ── Helpers ────────────────────────────────────────


def _db_to_bar(db: float, peak: float = 0.0, width: int = 20) -> str:
    """将 dB (-60~0) 映射为彩色均衡条."""
    ratio = max(0.0, min(1.0, (db + 60.0) / 60.0))
    filled = int(ratio * width)
    bar_chars = []
    for i in range(width):
        if i < filled:
            level = (i + 1) / width
            if level > 0.7:
                c = _RED
            elif level > 0.4:
                c = _ORANGE
            else:
                c = _GREEN
            bar_chars.append(f"{c}▰{_RESET}")
        else:
            bar_chars.append(f"{_GRAY}─{_RESET}")

    # peak marker
    peak_pos = int(max(0.0, min(1.0, (peak + 60.0) / 60.0)) * (width - 1))
    bar_chars[peak_pos] = f"{_BOLD}{_WHITE}▼{_RESET}"

    return "".join(bar_chars)


def _rms_to_eq_level(rms_db: float, bar_count: int = 10) -> list[int]:
    """将 RMS dB (-60~0) 映射到 8 级均衡条高度列表."""
    ratio = max(0.0, min(1.0, (rms_db + 60.0) / 60.0))
    levels: list[int] = []
    for i in range(bar_count):
        # 模拟频谱：中心高两边低
        center = (bar_count - 1) / 2
        dist = abs(i - center) / center if center > 0 else 0
        height = ratio * (1.0 - dist * 0.5)
        height = max(0.0, min(1.0, height))
        levels.append(min(7, int(height * 7)))
    return levels


def _spectrum_line(levels: list[int], color: str) -> str:
    """绘制频谱一行."""
    bars = []
    for level in levels:
        char = _EQ[level]
        bars.append(f"{color}{char}{_RESET}" if level > 0 else f"{_GRAY}{_EQ[0]}{_RESET}")
    return "".join(bars)


def _waveform(rms_db: float, width: int = 30) -> str:
    """绘制一行动态示波器波形."""
    ratio = max(0.0, min(1.0, (rms_db + 60.0) / 60.0))
    amp = int(ratio * 5)
    mid = width // 2
    chars = []
    for i in range(width):
        dist = abs(i - mid) / mid if mid > 0 else 0
        h = int(max(0, amp - int(dist * amp * 1.5)))
        char = " ▁▂▃▄▅▆▇█"[h]
        if ratio > 0.7:
            c = _RED
        elif ratio > 0.4:
            c = _ORANGE
        else:
            c = _GREEN
        chars.append(f"{c}{char}{_RESET}")
    return "".join(chars)


# ── Mapping ─────────────────────────────────────────

_STATE_ICON = {
    VoiceState.SLEEPING: "😴",
    VoiceState.LISTENING: "🎤",
    VoiceState.WORKING: "⚙️",
    VoiceState.SPEAKING: "🔊",
}

_STATE_COLOR = {
    VoiceState.SLEEPING: _BLUE,
    VoiceState.LISTENING: _GREEN,
    VoiceState.WORKING: _YELLOW,
    VoiceState.SPEAKING: _PURPLE,
}

_STATE_LABEL = {
    VoiceState.SLEEPING: "休眠",
    VoiceState.LISTENING: "聆听",
    VoiceState.WORKING: "执行",
    VoiceState.SPEAKING: "播报",
}


# ── Display ─────────────────────────────────────────


class VoiceDisplay:
    """终端底部状态面板 — 异步安全，后台线程刷新。"""

    TITLE = f"{_BOLD}{_CYAN}语码{_RESET} {_GRAY}·{_RESET} {_WHITE}Voice Code{_RESET}"

    def __init__(self) -> None:
        self._state: VoiceState = VoiceState.SLEEPING
        self._rms_db: float = -60.0
        self._peak_db: float = -60.0
        self._peak_time: float = 0.0
        self._lock = threading.Lock()
        self._enabled = False
        self._eq_history: list[list[int]] = []

    def start(self) -> None:
        """启用显示。"""
        self._enabled = True
        sys.stdout.write(_HIDE)
        sys.stdout.flush()

    def stop(self) -> None:
        """关闭显示，恢复光标。"""
        self._enabled = False
        self._clear_panel()
        sys.stdout.write(_SHOW)
        sys.stdout.flush()

    def update(self, state: VoiceState, rms_db: float) -> None:
        """更新状态和音频电平（从任意线程调用）。"""
        now = time.monotonic()
        with self._lock:
            self._state = state
            self._rms_db = rms_db
            if rms_db > self._peak_db:
                self._peak_db = rms_db
                self._peak_time = now + 2.0
            elif now > self._peak_time:
                self._peak_db -= 0.5
                if self._peak_db < -60.0:
                    self._peak_db = -60.0

            levels = _rms_to_eq_level(rms_db)
            self._eq_history.append(levels)
            if len(self._eq_history) > 60:
                self._eq_history.pop(0)

        if self._enabled:
            self._render()

    def _render(self) -> None:
        """绘制 5 行底部面板。"""
        with self._lock:
            state = self._state
            rms = self._rms_db
            peak = self._peak_db
            eq_hist = self._eq_history[-20:] if self._eq_history else []

        term_w = shutil.get_terminal_size().columns
        if term_w < 60:
            return

        rows, _ = shutil.get_terminal_size()
        top = rows - 5

        icon = _STATE_ICON.get(state, "❓")
        color = _STATE_COLOR.get(state, _WHITE)
        label = _STATE_LABEL.get(state, "")

        db_str = f"{rms:+5.1f} dB" if rms > -60 else "  -∞  "
        peak_str = f"{peak:+5.1f}" if peak > -60 else " -∞ "

        # ── line 1: title + state + dB ──
        state_tag = f"{icon} {color}{label}{_RESET}"
        line1 = (
            f"{self.TITLE}"
            f"  {_GRAY}│{_RESET}  {state_tag}"
            f"  {_GRAY}│{_RESET}  {db_str}"
        )

        # ── line 2: 均衡条 ──
        bar = _db_to_bar(rms, peak, term_w - 4)
        line2 = f"  {bar}"

        # ── line 3: 动态波形 + 峰值 ──
        wave_w = min(term_w - 20, 40)
        wave = _waveform(rms, wave_w)
        line3 = f"  {wave}  {_DIM}峰值{_RESET} {_BOLD}{peak_str}{_RESET} dB"

        # ── line 4: 频谱柱状图（来自历史） ──
        if eq_hist:
            last = eq_hist[-1]
            spec = _spectrum_line(last, color)
            line4 = f"  {spec}"
        else:
            line4 = ""

        # ── line 5: 分隔线 ──
        sep = _GRAY + "─" * (term_w - 2) + _RESET
        line5 = f" {sep}"

        lines = [line1, line2, line3, line4, line5]
        out = ""
        for i, txt in enumerate(lines):
            y = top + i + 1
            out += f"{_CUP.format(y, 1)}{_EL}{txt}"
        sys.stdout.write(out)
        sys.stdout.flush()

    def _clear_panel(self) -> None:
        """清除底部面板区域。"""
        try:
            rows, _ = shutil.get_terminal_size()
            top = rows - 5
            for i in range(5):
                y = top + i + 1
                sys.stdout.write(f"{_CUP.format(y, 1)}{_EL}")
            sys.stdout.flush()
        except Exception:
            pass
