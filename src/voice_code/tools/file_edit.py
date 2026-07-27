"""FileEdit tool — find-and-replace editing"""

from __future__ import annotations

import logging

from langchain_core.tools import tool

from voice_code.audit import record_audit_event
from voice_code.security import WorkspaceBoundaryError, resolve_workspace_path
from voice_code.tools.cache import mark_as_read, was_read

logger = logging.getLogger(__name__)

_MAX_OUTPUT_CHARS = 50_000
"""Maximum number of characters allowed in the tool's return message before truncation.

Prevents excessively large responses from overwhelming the LLM context window
when the edit confirmation or error message contains very long strings.
"""


def _truncate_output(text: str) -> str:
    if len(text) > _MAX_OUTPUT_CHARS:
        logger.info("FileEdit output truncated: %d -> %d chars", len(text), _MAX_OUTPUT_CHARS)
        return text[:_MAX_OUTPUT_CHARS] + "\n... (content truncated)"
    return text


@tool
def edit(
    file_path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> str:
    """Perform exact string replacements in files.

    Replaces old_string with new_string in the specified file.
    The edit will FAIL if old_string is not found in the file,
    or if it is found multiple times without replace_all=True.

    IMPORTANT: You MUST read the file with the Read tool first before editing it.

    Args:
        file_path: Absolute path to the file to modify.
        old_string: The text to replace.
        new_string: The text to replace it with (must be different from old_string).
        replace_all: Replace all occurrences (default: False, replace first only).

    Returns:
        Confirmation message indicating success or error.
    """
    try:
        path = resolve_workspace_path(file_path)
    except WorkspaceBoundaryError as exc:
        return _truncate_output(f"<tool_use_error>Error: {exc}</tool_use_error>")

    # No-op check
    if old_string == new_string:
        return _truncate_output(
            "<tool_use_error>Error: No changes to make — old_string"
            " and new_string are identical.</tool_use_error>"
        )

    # Handle create-if-not-exists
    if not path.exists():
        if old_string == "":
            # Create new file
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(new_string, encoding="utf-8")
                record_audit_event(
                    event_type="bash.write",
                    actor="agent",
                    resource_id=f"file:{path.name}",
                    outcome="created",
                    rule="file_edit_tool",
                    approval_result="recorded",
                )
                mark_as_read(file_path)
                return _truncate_output(f"The file {file_path} has been created successfully.")
            except OSError as e:
                return _truncate_output(
                    f"<tool_use_error>Error creating file: {e}</tool_use_error>"
                )
        else:
            return _truncate_output(
                f"<tool_use_error>Error: File not found: {file_path}</tool_use_error>"
            )

    # Read-before-write check for existing files
    if not was_read(file_path):
        return _truncate_output(
            "<tool_use_error>Error: File has not been read yet. "
            "Read it first before editing it.</tool_use_error>"
        )

    # Read current content
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as e:
        return _truncate_output(f"<tool_use_error>Error reading file: {e}</tool_use_error>")

    # Find old_string
    count = content.count(old_string)
    if count == 0:
        return _truncate_output(
            "<tool_use_error>Error: old_string not found in file. "
            "The text you provided does not exist in the file. "
            "Use the Read tool to verify the exact content.</tool_use_error>"
        )

    if count > 1 and not replace_all:
        return _truncate_output(
            f"<tool_use_error>Error: Found {count} occurrences of old_string "
            "but replace_all is False. Set replace_all=True to replace all, "
            "or provide more surrounding context to make old_string unique."
            "</tool_use_error>"
        )

    # Apply edit
    if replace_all:
        new_content = content.replace(old_string, new_string)
    else:
        new_content = content.replace(old_string, new_string, 1)

    try:
        path.write_text(new_content, encoding="utf-8")
    except OSError as e:
        return _truncate_output(f"<tool_use_error>Error writing file: {e}</tool_use_error>")

    record_audit_event(
        event_type="bash.write",
        actor="agent",
        resource_id=f"file:{path.name}",
        outcome="updated",
        rule="file_edit_tool",
        approval_result="recorded",
    )
    mark_as_read(file_path)

    replaced = "All occurrences were" if replace_all else "The file has been"
    return _truncate_output(f"{replaced} replaced successfully in {file_path}.")


edit.metadata = {
    "is_readonly": False,
    "is_concurrency_safe": False,
}
