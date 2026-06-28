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
        background: rgba(0, 0, 0, 0.55);
    }
    #permission-dialog {
        width: 80%;
        max-width: 96;
        height: auto;
        background: #0d1117;
        border: round #e6b450;
        padding: 1 2;
    }
    #permission-title {
        text-style: bold;
        color: #ffcc66;
        margin-bottom: 1;
    }
    #permission-body {
        margin-bottom: 1;
    }
    #permission-actions {
        align-horizontal: right;
        height: auto;
    }
    #permission-actions Button {
        margin-left: 1;
    }
    """

    def __init__(self, request: PermissionRequest) -> None:
        super().__init__()
        self.request = request

    def compose(self) -> ComposeResult:
        title = Text()
        title.append("Permission request", style="bold #ffcc66")
        title.append(f"  {self.request.tool_name}", style="bold #8ab4f8")
        if self.request.is_destructive:
            title.append("  destructive", style="bold red")

        yield Vertical(
            Static(title, id="permission-title"),
            Static(self._render_body(), id="permission-body"),
            Horizontal(
                Button("Deny", id="deny", variant="error"),
                Button("Allow once", id="allow", variant="primary"),
                Button("Always allow", id="always", variant="success"),
                id="permission-actions",
            ),
            id="permission-dialog",
        )

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
        body = Text()
        if self.request.reason:
            body.append("Reason: ", style="bold")
            body.append(self.request.reason)
            body.append("\n\n")
        body.append("Input:\n", style="bold")
        return Panel(
            Syntax(
                self._format_input(),
                "json",
                theme="monokai",
                word_wrap=True,
                background_color="#11161d",
            ),
            border_style="#30363d",
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
