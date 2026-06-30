"""Standalone memory maintenance commands: reindex, paths, audit."""

from __future__ import annotations

import argparse

from voice_code.memory.paths import (
    get_memory_root,
    get_user_memory_dir,
)
from voice_code.memory.service import MemoryService


def print_paths() -> None:
    print(f"Memory root: {get_memory_root()}")
    print(f"User memory dir: {get_user_memory_dir()}")


def run_reindex(project_root: str | None = None) -> None:
    service = MemoryService(project_root=project_root)
    service.reindex()
    print("Index rebuilt.")


def run_audit(project_root: str | None = None) -> None:
    service = MemoryService(project_root=project_root)
    issues = service.audit()
    if not issues:
        print("No issues found.")
        return
    for issue in issues:
        print(f"[{issue['type']}] {issue['message']}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="reasoning-memory")
    parser.add_argument("command", choices=["paths", "reindex", "audit"])
    parser.add_argument("--project-root", default=None, help="Project root path")
    args = parser.parse_args()

    if args.command == "paths":
        print_paths()
    elif args.command == "reindex":
        run_reindex(args.project_root)
    elif args.command == "audit":
        run_audit(args.project_root)


if __name__ == "__main__":
    main()
