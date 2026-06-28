"""系统提示词测试"""

from __future__ import annotations

from langchain_core.tools import tool

from reasoning_agent.prompts import get_system_prompt


def test_basic_structure():
    """空参数 → 包含基本章节。"""
    prompt = get_system_prompt()
    assert "You are an interactive CLI tool" in prompt
    assert "# Tone and style" in prompt
    assert "# Doing tasks" in prompt
    assert "# Code style" in prompt
    assert "# Safety and actions" in prompt
    assert "# Tool usage policy" in prompt
    assert "# Environment" in prompt


def test_sections_in_order():
    """章节按正确顺序排列。"""
    prompt = get_system_prompt()
    pos_identity = prompt.find("You are an interactive CLI tool")
    pos_tone = prompt.find("# Tone and style")
    pos_tasks = prompt.find("# Doing tasks")
    pos_actions = prompt.find("# Safety and actions")
    pos_tools = prompt.find("# Tool usage policy")
    pos_env = prompt.find("# Environment")

    assert pos_identity < pos_tone < pos_tasks < pos_actions < pos_tools < pos_env


def test_with_tools():
    """有工具 → 提示词包含工具清单。"""
    @tool
    def echo(x: str) -> str:
        """Echo the input."""
        return f"echo: {x}"

    prompt = get_system_prompt(tools=[echo])
    assert "## echo" in prompt
    assert "Echo the input" in prompt


def test_with_environment():
    """有 cwd 和 model_name → 提示词包含环境信息。"""
    prompt = get_system_prompt(
        cwd="/tmp/test",
        model_name="gpt-4",
        language="Chinese",
    )
    assert "/tmp/test" in prompt
    assert "gpt-4" in prompt
    assert "Chinese" in prompt


def test_environment_questions_answered_without_tools():
    """模型/路径等环境问题应直接读 Environment 章节。"""
    prompt = get_system_prompt(model_name="deepseek-chat", cwd="/tmp/test")
    assert "If the user asks about your current model" in prompt
    assert "Do not use tools just to look up information" in prompt


def test_structure_queries_prefer_glob_and_grep():
    """项目结构和内容搜索应优先走专用工具。"""
    prompt = get_system_prompt()
    assert "File search: Use Glob (NOT find or ls)" in prompt
    assert "Content search: Use Grep (NOT grep or rg)" in prompt
    assert "treat that path as exact text, not a fuzzy hint" in prompt
    assert "do not silently drop characters" in prompt


def test_with_claude_md():
    """有 claude_md → 提示词包含项目指令。"""
    prompt = get_system_prompt(claude_md="Always use pandas.")
    assert "# Project Instructions (CLAUDE.md)" in prompt
    assert "Always use pandas." in prompt
    assert "These instructions OVERRIDE" in prompt


def test_with_git_status():
    """有 git_status → 提示词末尾包含 git 信息。"""
    prompt = get_system_prompt(git_status="On branch main, clean")
    assert "# Git Status" in prompt
    assert "On branch main, clean" in prompt
    assert prompt.rfind("# Git Status") > prompt.rfind("# Environment")


def test_empty_tools():
    """空工具列表 → 不包含工具清单。"""
    prompt = get_system_prompt(tools=[])
    assert "## " not in prompt  # no tool headings


def test_empty_claude_md():
    """空 claude_md → 不包含项目指令章节。"""
    prompt = get_system_prompt(claude_md="")
    assert "# Project Instructions" not in prompt


def test_empty_git_status():
    """空 git_status → 不包含 Git Status 章节。"""
    prompt = get_system_prompt(git_status="")
    assert "# Git Status" not in prompt
