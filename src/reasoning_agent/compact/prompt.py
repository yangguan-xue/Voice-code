"""Compact prompt templates."""

from __future__ import annotations

_COMPACT_PROMPT = """\
You are a helpful AI assistant tasked with summarizing conversations.

CRITICAL: Respond with text ONLY. Do NOT call any tools. Do NOT output any tool calls.

Your task is to create a detailed summary of the conversation so far, \
paying close attention to the user's explicit requests and your previous \
actions. This summary should thoroughly capture technical details, code \
patterns, and architectural decisions that are essential for continuing \
development work without losing context.

Before providing your final summary, wrap your analysis in <analysis> tags \
to organize your thoughts and ensure you've covered all necessary points.

Your summary MUST include these sections:
1. Primary Request and Intent
2. Key Technical Concepts
3. Files and Code Sections (with file paths)
4. Errors and Fixes
5. Problem Solving
6. All User Messages
7. Pending Tasks
8. Current Work
9. Optional Next Steps

Wrap your final summary in <summary> tags. Example structure:

<analysis>
[your analysis here]
</analysis>

<summary>
1. Primary Request and Intent:
...

2. Key Technical Concepts:
...
</summary>

The conversation to summarize is below."""


def get_compact_prompt() -> str:
    return _COMPACT_PROMPT


def format_compact_summary(raw_response: str) -> str:
    """解析 LLM 返回的压缩摘要。

    提取 <summary>...</summary> 内容，去掉 <analysis> 部分。
    """
    # 找 <summary> 标签
    start = raw_response.find("<summary>")
    end = raw_response.find("</summary>")

    if start != -1 and end != -1:
        content = raw_response[start + len("<summary>"):end].strip()
    else:
        # 没有标签，取整个响应
        content = raw_response.strip()

    return f"Summary of the conversation so far:\n\n{content}"
