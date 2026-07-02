"""主题配色 — 纯黑底 + 灰阶层次 + 克制暖红点缀。"""

# ── 背景色 ──────────────────────────────────────
BG_PRIMARY = "#000000"        # 主背景
BG_SECONDARY = "#050505"      # 次背景
BG_SURFACE = "#0b0b0b"        # 主消息面
BG_ELEVATED = "#111111"       # 轻微抬升层
BG_CODE = "#030303"           # 代码块背景

# ── 边框 ────────────────────────────────────────
BORDER_PRIMARY = "#1c1c1c"    # 主边框（低对比）
BORDER_SECONDARY = "#2a2a2a"  # 次边框

# ── 文字 ────────────────────────────────────────
TEXT_PRIMARY = "#ebe7de"      # 主文字
TEXT_SECONDARY = "#a59f95"    # 次要文字
TEXT_DIM = "#6f6961"          # 弱化文字
TEXT_BRIGHT = "#fffdf8"       # 高亮文字

# ── 语义色（克制点缀）────────────────────────────
ACCENT_RED = "#df6a5c"        # 主红
ACCENT_DIM_RED = "#b55448"    # 暗红
ACCENT_BRIGHT_RED = "#f18d7f" # 亮红
ACCENT_GREEN = "#7bc285"      # 成功
ACCENT_YELLOW = "#c8a15a"     # 警告
ACCENT_BLUE = "#7b97c7"       # 信息
ACCENT_PURPLE = "#9d8abf"     # 思考中
ACCENT_PEACH = "#c88a67"      # 工具调用
ACCENT_TEAL = "#6fa7a1"       # 次要强调

# ── 工具颜色 ────────────────────────────────────
TOOL_COLORS = {
    "bash": f"bold {ACCENT_PEACH}",
    "read": f"bold {ACCENT_BLUE}",
    "write": f"bold {ACCENT_GREEN}",
    "edit": f"bold {ACCENT_YELLOW}",
    "glob": f"bold {ACCENT_TEAL}",
    "grep": f"bold {ACCENT_PURPLE}",
    "todo_write": f"bold {ACCENT_DIM_RED}",
    "ask_user_question": f"bold {ACCENT_BLUE}",
    "web_fetch": f"bold {ACCENT_GREEN}",
}

# ── 特殊 ────────────────────────────────────────
CODE_THEME = "monokai"
PERMISSION_BORDER = ACCENT_RED
PERMISSION_TITLE = ACCENT_RED
