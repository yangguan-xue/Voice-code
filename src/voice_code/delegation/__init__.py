"""Low-interruption background delegation primitives."""

from voice_code.delegation.context import collect_delegation_context
from voice_code.delegation.service import DelegationService
from voice_code.delegation.types import DelegationBrief, DelegationTask, DelegationTaskStatus
from voice_code.delegation.worktree import WorktreeManager

__all__ = [
    "DelegationBrief",
    "DelegationService",
    "DelegationTask",
    "DelegationTaskStatus",
    "WorktreeManager",
    "collect_delegation_context",
]
