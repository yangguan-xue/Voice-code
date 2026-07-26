from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from voice_code.web_demo.sandbox import SandboxBoundaryError, SandboxManager


@pytest.mark.asyncio
async def test_sandbox_rejects_path_and_symlink_escape(tmp_path: Path) -> None:
    template = tmp_path / "template"
    template.mkdir()
    (template / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("do not read\n", encoding="utf-8")

    manager = SandboxManager(tmp_path / "sandboxes", template_repo=template)
    sandbox = await manager.create("demo_escape")

    with pytest.raises(SandboxBoundaryError):
        sandbox.resolve_path("../outside/secret.txt")

    (sandbox.path / "linked").symlink_to(outside, target_is_directory=True)
    with pytest.raises(SandboxBoundaryError):
        sandbox.resolve_path("linked/secret.txt")


@pytest.mark.asyncio
async def test_sandbox_diff_uses_relative_paths_only(tmp_path: Path) -> None:
    template = tmp_path / "template"
    template.mkdir()
    (template / "app.py").write_text("VALUE = 1\n", encoding="utf-8")

    manager = SandboxManager(tmp_path / "sandboxes", template_repo=template)
    sandbox = await manager.create("demo_diff")
    (sandbox.path / "app.py").write_text("VALUE = 2\n", encoding="utf-8")

    diff = await manager.diff(sandbox)

    assert [item.path for item in diff.changed_files] == ["app.py"]
    assert diff.changed_files[0].status == "modified"
    assert str(sandbox.path) not in diff.patch


def test_sandbox_manager_rejects_symlink_root(tmp_path: Path) -> None:
    real_root = tmp_path / "real-sandboxes"
    real_root.mkdir()
    symlink_root = tmp_path / "linked-sandboxes"
    symlink_root.symlink_to(real_root, target_is_directory=True)

    with pytest.raises(SandboxBoundaryError, match="symlink"):
        SandboxManager(symlink_root)


def test_sandbox_manager_rejects_dangerous_root() -> None:
    with pytest.raises(SandboxBoundaryError):
        SandboxManager(Path("/"))


@pytest.mark.asyncio
async def test_sandbox_reset_restores_template(tmp_path: Path) -> None:
    template = tmp_path / "template"
    template.mkdir()
    (template / "app.py").write_text("VALUE = 1\n", encoding="utf-8")

    manager = SandboxManager(tmp_path / "sandboxes", template_repo=template)
    sandbox = await manager.create("demo_reset")
    (sandbox.path / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    (sandbox.path / "new.txt").write_text("remove me\n", encoding="utf-8")

    await manager.reset(sandbox)

    assert (sandbox.path / "app.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert not (sandbox.path / "new.txt").exists()

    await manager.discard(sandbox)
    shutil.rmtree(manager.root)


@pytest.mark.asyncio
async def test_sandbox_diff_ignores_runtime_noise(tmp_path: Path) -> None:
    template = tmp_path / "template"
    template.mkdir()
    (template / "app.py").write_text("VALUE = 1\n", encoding="utf-8")

    manager = SandboxManager(tmp_path / "sandboxes", template_repo=template)
    sandbox = await manager.create("demo_noise")
    (sandbox.path / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    (sandbox.path / "__pycache__").mkdir()
    (sandbox.path / "__pycache__" / "app.cpython-312.pyc").write_bytes(b"pyc")
    (sandbox.path / ".reasoning").mkdir()
    (sandbox.path / ".reasoning" / "state.json").write_text("{}", encoding="utf-8")

    diff = await manager.diff(sandbox)

    assert [item.path for item in diff.changed_files] == ["app.py"]
    assert "__pycache__" not in diff.patch
    assert ".reasoning" not in diff.patch


@pytest.mark.asyncio
async def test_sandbox_diff_includes_patch_for_added_files(tmp_path: Path) -> None:
    template = tmp_path / "template"
    template.mkdir()
    (template / "app.py").write_text("VALUE = 1\n", encoding="utf-8")

    manager = SandboxManager(tmp_path / "sandboxes", template_repo=template)
    sandbox = await manager.create("demo_added_patch")
    (sandbox.path / "linux-sandbox.md").write_text("# Linux sandbox\n", encoding="utf-8")

    diff = await manager.diff(sandbox)

    assert [item.path for item in diff.changed_files] == ["linux-sandbox.md"]
    assert diff.patch_available is True
    assert "diff --git a/linux-sandbox.md b/linux-sandbox.md" in diff.patch
    assert "new file mode 100644" in diff.patch
    assert "+# Linux sandbox" in diff.patch
