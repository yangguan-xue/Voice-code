from __future__ import annotations

from dataclasses import dataclass

from voice_code.memory.models import MemoryType
from voice_code.memory.paths import get_entries_dir_for_scope, get_project_memory_key
from voice_code.memory.rag_models import MemoryKind, MemoryScope
from voice_code.memory.repository import MemoryRepository
from voice_code.memory.store import list_entries


@dataclass(frozen=True, slots=True)
class MigrationResult:
    discovered: int
    imported: int
    duplicates: int
    invalid: int


def migrate_v1(
    repository: MemoryRepository,
    *,
    project_root: str | None,
    user_id: str = "local",
    dry_run: bool = True,
) -> MigrationResult:
    legacy_entries = list_entries("user")
    if project_root:
        legacy_entries.extend(list_entries("project", project_root))
    imported = 0
    duplicates = 0
    invalid = 0
    project_key = get_project_memory_key(project_root) if project_root else None
    for entry in legacy_entries:
        if dry_run:
            continue
        try:
            result = repository.import_legacy(
                memory_id=entry.id,
                user_id=user_id,
                project_key=project_key if entry.scope.value == "project" else None,
                scope=MemoryScope(entry.scope.value),
                kind=_map_kind(entry.type),
                content=entry.content or entry.description,
                source_session_id=entry.source.session_id,
                legacy_source_path=str(
                    get_entries_dir_for_scope(entry.scope.value, project_root)
                    / entry.file_name
                ),
                created_at=entry.created_at,
                updated_at=entry.updated_at,
            )
        except (TypeError, ValueError):
            invalid += 1
            continue
        if result.created:
            imported += 1
        else:
            duplicates += 1
    return MigrationResult(
        discovered=len(legacy_entries),
        imported=imported,
        duplicates=duplicates,
        invalid=invalid,
    )


def _map_kind(memory_type: MemoryType) -> MemoryKind:
    return {
        MemoryType.USER: MemoryKind.PREFERENCE,
        MemoryType.FEEDBACK: MemoryKind.FEEDBACK,
        MemoryType.PROJECT: MemoryKind.PROJECT_FACT,
        MemoryType.REFERENCE: MemoryKind.REFERENCE,
        MemoryType.CONFIG: MemoryKind.PROJECT_FACT,
    }[memory_type]
