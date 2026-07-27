from __future__ import annotations

from types import SimpleNamespace

from voice_code.desktop.metadata import collect_desktop_metadata


def test_collect_desktop_metadata_returns_lightweight_bootstrap(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "voice_code.desktop.metadata.list_session_summaries",
        lambda limit: [
            SimpleNamespace(
                id="session-1",
                title="历史任务",
                is_current=False,
                project_path=str(tmp_path),
                project_label=tmp_path.name,
            )
        ],
    )
    monkeypatch.setattr(
        "voice_code.desktop.metadata.group_session_summaries",
        lambda sessions: [
            SimpleNamespace(
                key=str(tmp_path),
                label=tmp_path.name,
                project_path=str(tmp_path),
                sessions=sessions,
            )
        ],
    )
    monkeypatch.setattr(
        "voice_code.desktop.metadata._git_context",
        lambda workspace: {
            "branch": "main",
            "branches": ["main"],
            "isDirty": False,
        },
    )
    monkeypatch.setattr(
        "voice_code.desktop.metadata._model_profiles",
        lambda: ([{"id": "fast", "label": "fast", "modelName": "model-fast"}], "fast"),
    )

    result = collect_desktop_metadata(str(tmp_path))

    assert result["sessionId"] == ""
    assert result["workspacePath"] == str(tmp_path.resolve())
    assert result["branch"] == "main"
    assert result["activeProfile"] == "fast"
    assert result["sessionGroups"][0]["sessions"] == [
        {
            "id": "session-1",
            "title": "历史任务",
            "isActive": False,
            "isEmpty": False,
        }
    ]
