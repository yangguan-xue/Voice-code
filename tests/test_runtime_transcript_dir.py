"""bootstrap_runtime 的 transcript_dir 注入（T2）。

钉住：注入租户级 transcript 目录后，transcript 与 state 文件落在该目录；
不注入时行为与现状完全一致（~/.reasoning/transcripts）。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import voice_code.runtime as runtime_module
from voice_code.runtime import bootstrap_runtime


def _stub_heavy_dependencies(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """把模型/工具/记忆等重依赖打桩，只留 transcript 路径计算这条链路。"""
    fake_model = SimpleNamespace(model_name="stub-model")

    monkeypatch.setattr(runtime_module, "init_model", lambda **_: fake_model)
    monkeypatch.setattr(runtime_module, "resolve_fallback_profile", lambda _: None)
    monkeypatch.setattr(runtime_module, "set_active_profile", lambda _: None)
    monkeypatch.setattr(runtime_module, "get_all_tools", lambda: [])
    monkeypatch.setattr(runtime_module, "configure_workspace_root", lambda _: None)
    monkeypatch.setattr(runtime_module, "build_memory_rag_service", lambda: None)
    monkeypatch.setattr(runtime_module, "get_project_memory_key", lambda _: "stub-key")

    async def fake_load_mcp_tools(_cwd: str) -> list:
        return []

    async def fake_build_workspace_prompt(**_: object) -> str:
        return "stub prompt"

    monkeypatch.setattr(runtime_module, "load_mcp_tools", fake_load_mcp_tools)
    monkeypatch.setattr(runtime_module, "build_workspace_prompt", fake_build_workspace_prompt)


@pytest.mark.asyncio
async def test_transcript_dir_injection_places_files_under_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub_heavy_dependencies(monkeypatch, tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    tenant_dir = tmp_path / "data" / "tenants" / "alice" / "transcripts"

    runtime = await bootstrap_runtime(
        profile=None,
        session_id="conv-1",
        workspace=str(workspace),
        transcript_dir=tenant_dir,
    )
    runtime.transcript_writer.close()

    assert (tenant_dir / "conv-1.jsonl").exists()
    assert (tenant_dir / "conv-1.state.json").exists()


@pytest.mark.asyncio
async def test_default_transcript_dir_unchanged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub_heavy_dependencies(monkeypatch, tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    # 把全局 transcript 目录重定向到 tmp_path，验证默认路径逻辑不变
    global_dir = tmp_path / "global-transcripts"
    monkeypatch.setattr(
        "voice_code.session.manager.get_transcript_dir", lambda: global_dir
    )
    monkeypatch.setattr(
        "voice_code.session.state.get_session_state_path",
        lambda session_id: global_dir / f"{session_id}.state.json",
    )
    monkeypatch.setattr(
        runtime_module, "get_session_path", lambda session_id: global_dir / f"{session_id}.jsonl"
    )

    runtime = await bootstrap_runtime(
        profile=None,
        session_id="conv-2",
        workspace=str(workspace),
    )
    runtime.transcript_writer.close()

    assert (global_dir / "conv-2.jsonl").exists()
    assert runtime.transcript_writer.file_path == global_dir / "conv-2.jsonl"
