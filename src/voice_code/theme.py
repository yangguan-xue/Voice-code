"""主题配色 — 纯黑底 + 红色点缀（Claude Code 风格）。"""

# ── 背景色 ──────────────────────────────────────
BG_PRIMARY = "#000000"        # 主背景（纯黑）
BG_SECONDARY = "#0a0a0a"      # 次背景
BG_SURFACE = "#000000"        # 面板背景
BG_CODE = "#000000"           # 代码块背景

# ── 边框 ────────────────────────────────────────
BORDER_PRIMARY = "#222222"    # 主边框（深灰）
BORDER_SECONDARY = "#333333"  # 次边框

# ── 文字 ────────────────────────────────────────
TEXT_PRIMARY = "#e0e0e0"      # 主文字
TEXT_SECONDARY = "#999999"    # 次要文字
TEXT_DIM = "#666666"          # 弱化文字
TEXT_BRIGHT = "#ffffff"       # 高亮文字

# ── 语义色（红色系）──────────────────────────────
ACCENT_RED = "#ff3333"       # 主红
ACCENT_DIM_RED = "#cc2222"   # 暗红
ACCENT_BRIGHT_RED = "#ff6666" # 亮红
ACCENT_GREEN = "#33cc33"     # 成功（少量绿色点缀）
ACCENT_YELLOW = "#ffaa00"    # 警告
ACCENT_BLUE = "#4488ff"      # 信息
ACCENT_PURPLE = "#cc44ff"    # 思考中
ACCENT_PEACH = "#ff6633"     # 工具调用
ACCENT_TEAL = "#22cccc"      # 次要强调

# ── 工具颜色 ────────────────────────────────────
TOOL_COLORS = {
    "bash": f"bold {ACCENT_RED}",
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
