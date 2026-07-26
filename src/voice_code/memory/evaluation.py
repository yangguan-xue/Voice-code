from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from voice_code.memory.extraction import ExtractedCandidate
from voice_code.memory.rag_models import MemoryKind, MemoryScope


@dataclass(frozen=True, slots=True)
class EvaluationCase:
    case_id: str
    eligible: bool
    category: str
    expected_kind: MemoryKind | None = None
    expected_scope: MemoryScope | None = None


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    total: int
    true_positive: int
    false_positive: int
    false_negative: int
    kind_correct: int
    kind_total: int
    scope_correct: int
    scope_total: int
    sensitive_persisted: int
    transient_total: int
    transient_persisted: int

    @property
    def precision(self) -> float:
        denominator = self.true_positive + self.false_positive
        return self.true_positive / denominator if denominator else 1.0

    @property
    def recall(self) -> float:
        denominator = self.true_positive + self.false_negative
        return self.true_positive / denominator if denominator else 1.0

    @property
    def kind_accuracy(self) -> float:
        return self.kind_correct / self.kind_total if self.kind_total else 1.0

    @property
    def scope_accuracy(self) -> float:
        return self.scope_correct / self.scope_total if self.scope_total else 1.0

    @property
    def transient_persistence_rate(self) -> float:
        return self.transient_persisted / self.transient_total if self.transient_total else 0.0

    @property
    def passed(self) -> bool:
        return (
            self.precision >= 0.95
            and self.recall >= 0.85
            and self.kind_accuracy >= 0.90
            and self.scope_accuracy >= 0.98
            and self.transient_persistence_rate <= 0.01
            and self.sensitive_persisted == 0
        )

    def to_dict(self) -> dict[str, int | float | bool]:
        values: dict[str, int | float | bool] = asdict(self)
        values.update(
            precision=self.precision,
            recall=self.recall,
            kind_accuracy=self.kind_accuracy,
            scope_accuracy=self.scope_accuracy,
            transient_persistence_rate=self.transient_persistence_rate,
            passed=self.passed,
        )
        return values


def evaluate_predictions(
    cases: Sequence[EvaluationCase],
    predictions: Sequence[Sequence[ExtractedCandidate]],
) -> EvaluationReport:
    if len(cases) != len(predictions):
        raise ValueError("Evaluation cases and predictions must have equal length")
    true_positive = false_positive = false_negative = 0
    kind_correct = kind_total = scope_correct = scope_total = 0
    sensitive_persisted = transient_total = transient_persisted = 0
    for case, predicted in zip(cases, predictions, strict=True):
        has_prediction = bool(predicted)
        if case.eligible and has_prediction:
            true_positive += 1
            first = predicted[0]
            if case.expected_kind is not None:
                kind_total += 1
                kind_correct += first.kind is case.expected_kind
            if case.expected_scope is not None:
                scope_total += 1
                scope_correct += first.scope is case.expected_scope
        elif case.eligible:
            false_negative += 1
        elif has_prediction:
            false_positive += 1
        if case.category == "sensitive" and has_prediction:
            sensitive_persisted += 1
        if case.category == "transient":
            transient_total += 1
            transient_persisted += has_prediction
    return EvaluationReport(
        total=len(cases),
        true_positive=true_positive,
        false_positive=false_positive,
        false_negative=false_negative,
        kind_correct=kind_correct,
        kind_total=kind_total,
        scope_correct=scope_correct,
        scope_total=scope_total,
        sensitive_persisted=sensitive_persisted,
        transient_total=transient_total,
        transient_persisted=transient_persisted,
    )


def write_gate_report(
    path: Path,
    report: EvaluationReport,
    *,
    reviewed_turns: int,
    observation_days: int,
) -> None:
    payload = {
        "report_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "reviewed_turns": reviewed_turns,
        "observation_days": observation_days,
        "metrics": report.to_dict(),
    }
    payload["integrity_sha256"] = _report_digest(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def validate_gate_report(path: Path) -> None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        supplied_digest = payload.pop("integrity_sha256")
        valid = (
            payload["report_version"] == 1
            and payload["reviewed_turns"] >= 500
            and payload["observation_days"] >= 7
            and payload["metrics"]["passed"] is True
            and payload["metrics"]["sensitive_persisted"] == 0
            and supplied_digest == _report_digest(payload)
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid memory extraction gate report") from exc
    if not valid:
        raise ValueError("Memory extraction gate report has not passed rollout requirements")


def _report_digest(payload: dict) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
