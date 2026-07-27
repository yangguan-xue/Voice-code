from __future__ import annotations

import importlib.metadata
import subprocess
import sys
import tomllib
from pathlib import Path

import yaml

from voice_code import __version__
from voice_code.cli import parse_args
from voice_code.integrations import mcp
from voice_code.tools import web_fetch as web_fetch_module

ROOT = Path(__file__).resolve().parents[1]


def test_package_version_comes_from_project_metadata() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]

    assert __version__ == project["version"]
    assert importlib.metadata.version("voice-code") == project["version"]


def test_primary_cli_exposes_version_flag() -> None:
    args = parse_args(["--version"])

    assert args.show_version is True


def test_user_agents_and_mcp_client_info_use_package_version() -> None:
    assert web_fetch_module.USER_AGENT == f"voice-code/{__version__}"
    assert mcp.client_info() == {"name": "voice-code", "version": __version__}


def test_ci_workflow_contains_round_nine_gates() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    jobs = workflow["jobs"]

    expected_jobs = {
        "lint",
        "types",
        "tests",
        "coverage",
        "secret-scan",
        "license-scan",
        "build",
        "smoke",
    }
    assert expected_jobs.issubset(jobs)
    assert jobs["lint"]["strategy"]["matrix"]["os"] == ["ubuntu-latest", "macos-latest"]
    assert "secret-scan" in jobs["build"]["needs"]
    assert "license-scan" in jobs["build"]["needs"]
    assert "build" in jobs["smoke"]["needs"]


def test_ci_generates_sbom_checksums_and_signature() -> None:
    workflow_text = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "detect-secrets" in workflow_text or "gitleaks" in workflow_text
    assert "pip-licenses" in workflow_text
    assert "syft" in workflow_text or "cyclonedx-py" in workflow_text
    assert "checksum.txt" in workflow_text
    assert "sigstore" in workflow_text or "cosign" in workflow_text


def test_smoke_script_exercises_migrations_and_cli_help() -> None:
    script = ROOT / ".github/scripts/ci_smoke.py"
    text = script.read_text(encoding="utf-8")

    assert "migrate_v3_to_v4" in text
    assert "voice_code" in text
    assert "--plain" in text
    assert "--help" in text
    subprocess.run([sys.executable, str(script), "--migration-only"], cwd=ROOT, check=True)


def test_rollback_runbook_documents_previous_stable_install() -> None:
    text = (ROOT / "docs/rollback.md").read_text(encoding="utf-8")

    assert "pip install voice-code==X.Y.Z" in text
    assert "checksum.txt" in text
    assert "reasoning --plain --help" in text
