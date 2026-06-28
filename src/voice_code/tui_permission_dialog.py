"""Permission dialog components for the Textual TUI."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass

from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.events import Key
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from voice_code.permissions import (
    PermissionBehavior,
    PermissionDecision,
    PermissionRequest,
)


@dataclass
class PendingPermissionRequest:
    """Thread-safe bridge between the agent thread and the Textual UI."""

    request: PermissionRequest
    event: threading.Event
    decision: PermissionDecision | None = None

    def resolve(self, decision: PermissionDecision) -> None:
        self.decision = decision
        self.event.set()


class PermissionDialog(ModalScreen[PermissionDecision]):
    """Tool permission approval dialog."""

    DEFAULT_CSS = """
    PermissionDialog {
        align: center middle;
        background: rgba(0, 0, 0, 0.72);
    }
    #permission-dialog {
        width: 92;
        max-width: 108;
        height: auto;
        background: #000000;
        border: round #ff3333;
        padding: 1 2 2 2;
    }
    #permission-title {
        text-style: bold;
        color: #ff3333;
        margin-bottom: 1;
    }
    #permission-meta {
        color: #999999;
        margin-bottom: 1;
    }
    #permission-body {
        margin-bottom: 1;
    }
    #permission-actions {
        align-horizontal: left;
        height: auto;
        margin-top: 1;
    }
    #permission-actions Button {
        margin-right: 1;
    }
    #permission-shortcuts {
        color: #666666;
        margin-top: 1;
    }
    """

    def __init__(self, request: PermissionRequest) -> None:
        super().__init__()
        self.request = request

    def compose(self) -> ComposeResult:
        title = Text()
        title.append("Permission Request", style="bold #ff3333")
        title.append(f"  {self.request.tool_name}", style="bold #4488ff")
        if self.request.is_destructive:
            title.append("  destructive", style="bold #ffffff on #ff3333")

        meta = Text()
        meta.append("这次操作需要明确审批。", style="bold #e0e0e0")
        if self.request.reason:
            meta.append("  ")
            meta.append(self.request.reason, style="#999999")
        source = _permission_source_label(self.request)
        if source:
            meta.append("\n")
            meta.append(source, style="bold #4488ff")

        yield Vertical(
            Static(title, id="permission-title"),
            Static(meta, id="permission-meta"),
            Static(self._render_body(), id="permission-body"),
            Horizontal(
                Button("Deny", id="deny", variant="error"),
                Button("Allow once", id="allow", variant="primary"),
                Button("Always allow", id="always", variant="success"),
                id="permission-actions",
            ),
            Static(
                "Esc / N 拒绝  ·  Enter / Y 允许一次  ·  A 本会话始终允许",
                id="permission-shortcuts",
            ),
            id="permission-dialog",
        )

    def on_mount(self) -> None:
        self.query_one("#allow", Button).focus()

    async def _on_key(self, event: Key) -> None:
        key = event.key.lower()
        if key in ("escape", "n"):
            event.stop()
            self._deny()
        elif key in ("y", "enter"):
            event.stop()
            self._allow_once()
        elif key == "a":
            event.stop()
            self._allow_session()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "deny":
            self._deny()
        elif event.button.id == "allow":
            self._allow_once()
        elif event.button.id == "always":
            self._allow_session()

    def _render_body(self) -> Panel:
        return Panel(
            Syntax(
                self._format_input(),
                "json",
                theme="monokai",
                word_wrap=True,
                background_color="#000000",
            ),
            title=" input ",
            title_align="left",
            border_style="#333333",
            padding=(0, 1),
        )

    def _format_input(self) -> str:
        if self.request.tool_name == "bash":
            command = self.request.tool_input.get("command", "")
            description = self.request.tool_input.get("description", "")
            return json.dumps(
                {
                    "command": command,
                    "description": description,
                },
                ensure_ascii=True,
                indent=2,
            )
        return json.dumps(self.request.tool_input, ensure_ascii=True, indent=2, default=str)

    def _deny(self) -> None:
        self.dismiss(
            PermissionDecision(
                behavior=PermissionBehavior.DENY,
                message="Permission denied by user.",
            )
        )

    def _allow_once(self) -> None:
        self.dismiss(PermissionDecision(behavior=PermissionBehavior.ALLOW))

    def _allow_session(self) -> None:
        self.dismiss(
            PermissionDecision(
                behavior=PermissionBehavior.ALLOW,
                message="Allowed for the rest of this session.",
            )
        )


def _permission_source_label(request: PermissionRequest) -> str:
    if request.task_id and request.agent_type:
        return f"来源: 子 agent {request.agent_type}  ·  task {request.task_id}"
    if request.task_id:
        return f"来源: task {request.task_id}"
    return ""
