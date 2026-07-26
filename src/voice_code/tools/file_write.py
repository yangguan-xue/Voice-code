"""FileWrite tool — create or overwrite files"""

from __future__ import annotations

from langchain_core.tools import tool

from voice_code.audit import record_audit_event
from voice_code.security import WorkspaceBoundaryError, resolve_workspace_path
from voice_code.tools.cache import mark_as_read, was_read


@tool
def write(file_path: str, content: str) -> str:
    """Write a file to the local filesystem.

    Creates a new file or overwrites an existing one. Parent directories
    are created automatically if they don't exist.

    IMPORTANT: You MUST read the file with the Read tool first before
    writing to it, unless it is a new file.

    Args:
        file_path: Absolute path to write the file to.
        content: Content to write to the file.

    Returns:
        Confirmation message indicating success or error.
    """
    try:
        path = resolve_workspace_path(file_path)
    except WorkspaceBoundaryError as exc:
        return f"<tool_use_error>Error: {exc}</tool_use_error>"

    # Check read-before-write for existing files
    if path.exists():
        if not was_read(file_path):
            return (
                "<tool_use_error>Error: File has not been read yet. "
                "Read it first before writing to it.</tool_use_error>"
            )

    # Create parent directories
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return f"<tool_use_error>Error creating parent directory: {e}</tool_use_error>"

    # Write
    is_new = not path.exists()
    try:
        path.write_text(content, encoding="utf-8")
    except OSError as e:
        return f"<tool_use_error>Error writing file: {e}</tool_use_error>"

    record_audit_event(
        event_type="bash.write",
        actor="agent",
        resource_id=f"file:{path.name}",
        outcome="created" if is_new else "updated",
        rule="file_write_tool",
        approval_result="recorded",
    )
    mark_as_read(file_path)

    action = "created" if is_new else "updated"
    return f"The file {file_path} has been {action} successfully."


write.metadata = {
    "is_readonly": False,
    "is_concurrency_safe": False,
}
