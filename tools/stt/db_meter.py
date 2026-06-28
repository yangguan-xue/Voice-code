"""实时分贝计 — 显示当前麦克风音量，帮助调试 VAD min_db 阈值。"""

from __future__ import annotations

import math
import struct
import sys
import time

import sounddevice as sd

SAMPLE_RATE = 16000
FRAME_MS = 30
FRAME_SAMPLES = int(SAMPLE_RATE * FRAME_MS / 1000)

# Bar 宽度
BAR_WIDTH = 50
# dB 范围映射
DB_MIN = -60
DB_MAX = 0

# 颜色 ANSI
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"
RED = "\x1b[31m"
RESET = "\x1b[0m"
BOLD = "\x1b[1m"
HIDE_CURSOR = "\x1b[?25l"
SHOW_CURSOR = "\x1b[?25h"
CLEAR_LINE = "\x1b[2K"


def rms_db(frame: bytes) -> float:
    n = len(frame) // 2
    if n == 0:
        return -100.0
    samples = struct.unpack(f"<{n}h", frame)
    sum_sq = sum(float(s) * float(s) for s in samples)
    rms = math.sqrt(sum_sq / n)
    if rms < 1.0:
        return -100.0
    return 20.0 * math.log10(rms / 32768.0)


def bar(level: float, width: int = BAR_WIDTH) -> str:
    """画音量震荡条。"""
    ratio = (level - DB_MIN) / (DB_MAX - DB_MIN)
    ratio = max(0.0, min(1.0, ratio))
    filled = int(ratio * width)
    empty = width - filled

    if filled > width * 0.7:
        color = RED
    elif filled > width * 0.4:
        color = YELLOW
    else:
        color = GREEN

    return f"{color}█{'█' * (filled - 1) if filled > 0 else ''}{RESET}{'░' * empty}"


def peak_cursor(level: float, width: int = BAR_WIDTH) -> str:
    """峰值指示器，显示当前 dB 在条上的位置。"""
    ratio = (level - DB_MIN) / (DB_MAX - DB_MIN)
    ratio = max(0.0, min(1.0, ratio))
    pos = int(ratio * width)
    pos = max(0, min(width - 1, pos))
    line = [" "] * width
    line[pos] = "▼"
    return "".join(line)


def main() -> None:
    print(f"{BOLD}实时分贝计{RESET}")
    print(f"  采样率: {SAMPLE_RATE} Hz | 帧长: {FRAME_MS}ms | 范围: {DB_MIN} ~ {DB_MAX} dB")
    print(f"  绿={GREEN}安静{RESET}  黄={YELLOW}中等{RESET}  红={RED}大声{RESET}")
    print(f"  {GREEN}█{RESET} < -30dB    {YELLOW}█{RESET} -30~-10dB    {RED}█{RESET} > -10dB")
    print("  Ctrl+C 退出\n")
    print(f"{BOLD}  dB    震荡条{RESET}")

    peak_hold = -100.0
    peak_decay = 0.0

    try:
        stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="int16",
            blocksize=FRAME_SAMPLES,
        )
        stream.start()

        print(HIDE_CURSOR, end="", flush=True)

        while True:
            frame, overflowed = stream.read(FRAME_SAMPLES)
            if overflowed:
                continue

            db = rms_db(frame.tobytes())

            # 峰值保持 & 衰减
            if db > peak_hold:
                peak_hold = db
                peak_decay = time.monotonic() + 2.0
            elif time.monotonic() > peak_decay:
                peak_hold -= 0.5
                if peak_hold < -100:
                    peak_hold = -100.0

            # 显示
            db_str = f"{db:+6.1f}" if db > -100 else "  -∞  "
            peak_str = f"峰值: {peak_hold:+5.1f}" if peak_hold > -100 else "峰值:  -∞  "

            sys.stdout.write(
                f"\r{CLEAR_LINE}{db_str} dB  [{bar(db)}]  {peak_str}"
            )
            sys.stdout.flush()

    except KeyboardInterrupt:
        pass
    finally:
        print(f"\n{SHOW_CURSOR}", end="", flush=True)
        try:
            stream.stop()
            stream.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
