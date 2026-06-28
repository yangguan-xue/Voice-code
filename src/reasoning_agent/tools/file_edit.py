"""FileEdit tool — find-and-replace editing"""

from __future__ import annotations

from pathlib import Path

from langchain_core.tools import tool

from reasoning_agent.tools.cache import mark_as_read, was_read


def _resolve(file_path: str) -> Path:
    return Path(file_path).expanduser().resolve()


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
    path = _resolve(file_path)

    # No-op check
    if old_string == new_string:
        return (
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
                mark_as_read(file_path)
                return f"The file {file_path} has been created successfully."
            except OSError as e:
                return f"<tool_use_error>Error creating file: {e}</tool_use_error>"
        else:
            return f"<tool_use_error>Error: File not found: {file_path}</tool_use_error>"

    # Read-before-write check for existing files
    if not was_read(file_path):
        return (
            "<tool_use_error>Error: File has not been read yet. "
            "Read it first before editing it.</tool_use_error>"
        )

    # Read current content
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as e:
        return f"<tool_use_error>Error reading file: {e}</tool_use_error>"

    # Find old_string
    count = content.count(old_string)
    if count == 0:
        return (
            "<tool_use_error>Error: old_string not found in file. "
            "The text you provided does not exist in the file. "
            "Use the Read tool to verify the exact content.</tool_use_error>"
        )

    if count > 1 and not replace_all:
        return (
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
        return f"<tool_use_error>Error writing file: {e}</tool_use_error>"

    mark_as_read(file_path)

    replaced = "All occurrences were" if replace_all else "The file has been"
    return f"{replaced} replaced successfully in {file_path}."


edit.metadata = {
    "is_readonly": False,
    "is_concurrency_safe": False,
}
