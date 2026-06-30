from __future__ import annotations

MEMORY_INSTRUCTIONS = """\
# Memory System

You have access to a persistent memory system that stores cross-session \
knowledge. Memories are categorized by type: user (personal preferences), \
feedback (corrections about how you work), project (project-specific facts), \
and reference (external pointers).

At the start of this session, the relevant memory index (MEMORY.md) has been \
loaded below. When the user asks a task-related question, relevant memories \
may also be injected as a <memories> section in the conversation.

You can use the following commands to manage memories:
- The user can save a memory with `/remember <text>`.
- The user can list or view memories with `/memory list` and `/memory show <id>`.
- The user can archive a memory with `/forget <id>`.
"""


def build_memory_content(
    user_memory_md: str = "",
    project_memory_md: str = "",
    selected_memories: list[dict] | None = None,
) -> str:
    parts: list[str] = []
    parts.append(MEMORY_INSTRUCTIONS)

    if user_memory_md:
        parts.append(f"## User Memories\n\n{user_memory_md}")
    if project_memory_md:
        parts.append(f"## Project Memories\n\n{project_memory_md}")

    if selected_memories:
        parts.append("## Relevant Memories for This Task")
        for mem in selected_memories:
            name = mem.get("name", "Untitled")
            desc = mem.get("description", "")
            content = mem.get("content", "")
            tags = mem.get("tags", [])
            source = mem.get("source_kind", "explicit")
            tag_str = f" [{', '.join(tags)}]" if tags else ""
            source_str = f" (source: {source})" if source != "explicit" else ""
            parts.append(f"- **{name}**{tag_str}{source_str}: {desc}")
            if content and content != desc:
                lines = content.split("\n")
                for line in lines[:5]:
                    parts.append(f"  - {line}")
                if len(lines) > 5:
                    parts.append(f"  - *[...] ({len(lines) - 5} more lines)*")

    return "\n\n".join(parts)
