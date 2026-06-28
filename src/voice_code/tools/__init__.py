"""工具注册中心 — 统一导出所有可用工具"""

from __future__ import annotations

from langchain_core.tools import BaseTool

from voice_code.tools.ask_user import ask_user_question
from voice_code.tools.bash import bash
from voice_code.tools.file_edit import edit
from voice_code.tools.file_read import read
from voice_code.tools.file_write import write
from voice_code.tools.glob_search import glob
from voice_code.tools.grep_search import grep
from voice_code.tools.todo_write import todo_write
from voice_code.tools.web_fetch import web_fetch

# ---- 工具注册 ----

_ALL_TOOLS: list[BaseTool] = [
    bash,
    read,
    write,
    edit,
    glob,
    grep,
    todo_write,
    ask_user_question,
    web_fetch,
]


def get_all_tools() -> list[BaseTool]:
    """返回所有可用工具列表。"""
    return list(_ALL_TOOLS)


def find_tool_by_name(name: str, tools: list[BaseTool]) -> BaseTool | None:
    """按名称查找工具。"""
    for tool in tools:
        if tool.name == name:
            return tool
    return None
