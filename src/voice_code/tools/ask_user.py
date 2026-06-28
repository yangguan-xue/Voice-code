"""AskUserQuestion tool — agent 反问用户"""

from __future__ import annotations

import logging

from langchain_core.tools import tool

logger = logging.getLogger(__name__)


@tool
def ask_user_question(question: str) -> str:
    """Ask the user a question when you need clarification.

    Use this tool when:
    - Requirements are ambiguous
    - Multiple approaches are possible and you need guidance
    - You need the user to make a decision before proceeding

    Args:
        question: The question to ask the user. Be specific and concise.
    """
    try:
        import builtins
        answer = builtins.input(f"\n[AGENT] {question}\n> ")
        if answer.strip():
            return f"User response: {answer.strip()}"
        return "User did not provide a response."
    except (EOFError, OSError):
        logger.warning("Non-interactive mode, cannot ask user")
        return (
            "Unable to ask user in non-interactive mode. "
            "Make your best judgment and proceed."
        )


ask_user_question.metadata = {
    "is_readonly": True,
    "is_concurrency_safe": False,
}
