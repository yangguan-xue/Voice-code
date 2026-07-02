from __future__ import annotations

from voice_code.commands import parse_command


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
