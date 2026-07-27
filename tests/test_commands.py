from __future__ import annotations

from voice_code.commands import format_help_lines, parse_command


def test_parse_subagent_task_commands():
    tasks = parse_command("/tasks")
    assert tasks.name == "tasks"

    task = parse_command("/task abc123")
    assert task.name == "task"
    assert task.args["task_id"] == "abc123"

    close = parse_command("/task-close")
    assert close.name == "task_close"

    stop = parse_command("/task-stop abc123")
    assert stop.name == "task_stop"
    assert stop.args["task_id"] == "abc123"

    stop_all = parse_command("/task-stop-all")
    assert stop_all.name == "task_stop_all"

    copy = parse_command("/task-copy")
    assert copy.name == "task_copy"

    path = parse_command("/task-path")
    assert path.name == "task_path"


def test_parse_permission_management_commands():
    perm = parse_command("/perm")
    assert perm.name == "perm"

    perm_rules = parse_command("/perm-rules")
    assert perm_rules.name == "perm_rules"

    perm_clear = parse_command("/perm-clear workspace")
    assert perm_clear.name == "perm_clear"
    assert perm_clear.args["scope"] == "workspace"

    perm_add = parse_command(
        "/perm-add workspace ask "
        'tool_names=write path_patterns="/repo/docs/*" '
        'reason_message="docs edits require approval"'
    )
    assert perm_add.name == "perm_add"
    assert perm_add.args["scope"] == "workspace"
    assert perm_add.args["behavior"] == "ask"
    assert perm_add.args["updates"] == {
        "tool_names": "write",
        "path_patterns": "/repo/docs/*",
        "reason_message": "docs edits require approval",
    }

    perm_rule = parse_command("/perm-rule workspace 2")
    assert perm_rule.name == "perm_rule"
    assert perm_rule.args["scope"] == "workspace"
    assert perm_rule.args["index"] == 2

    perm_delete = parse_command("/perm-delete 3")
    assert perm_delete.name == "perm_delete"
    assert perm_delete.args["scope"] == "session"
    assert perm_delete.args["index"] == 3

    perm_edit = parse_command("/perm-edit workspace 1 deny custom reason")
    assert perm_edit.name == "perm_edit"
    assert perm_edit.args["scope"] == "workspace"
    assert perm_edit.args["index"] == 1
    assert perm_edit.args["behavior"] == "deny"
    assert perm_edit.args["reason_message"] == "custom reason"
    assert perm_edit.args["updates"] == {}

    perm_edit_fields = parse_command(
        "/perm-edit session 2 "
        'tool_names=write,edit path_patterns="/repo/docs/*,/repo/specs/*" '
        "background=true"
    )
    assert perm_edit_fields.name == "perm_edit"
    assert perm_edit_fields.args["scope"] == "session"
    assert perm_edit_fields.args["index"] == 2
    assert perm_edit_fields.args["behavior"] == ""
    assert perm_edit_fields.args["updates"] == {
        "tool_names": "write,edit",
        "path_patterns": "/repo/docs/*,/repo/specs/*",
        "background": "true",
    }


def test_parse_status_commands():
    status = parse_command("/status")
    assert status.name == "status"

    status_areas = parse_command("/status-areas")
    assert status_areas.name == "status_areas"

    status_gaps = parse_command("/status-gaps")
    assert status_gaps.name == "status_gaps"


def test_parse_goal_resume_command():
    resume = parse_command("/goal-resume goal-123")

    assert resume.name == "goal_resume"
    assert resume.args == {"goal_id": "goal-123"}


def test_format_help_lines_groups_commands_for_readability():
    lines = format_help_lines(include_tui=True)

    assert lines[0] == "Commands"
    assert any("General" in line for line in lines)
    assert any(
        "/perm-add [session|workspace] <allow|deny|ask> field=value..." in line
        for line in lines
    )
    assert any("TUI only" in line for line in lines)
    assert "- /goal-resume <id>" in lines
