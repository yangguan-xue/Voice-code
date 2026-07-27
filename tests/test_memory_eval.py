from __future__ import annotations

from voice_code.memory.candidates import ExtractionMode, SensitivityDecision
from voice_code.memory.config import MemoryRagConfig
from voice_code.memory.evaluation import (
    EvaluationCase,
    evaluate_predictions,
    write_gate_report,
)
from voice_code.memory.extraction import ExtractedCandidate
from voice_code.memory.rag_models import MemoryKind, MemoryScope


def _prediction() -> ExtractedCandidate:
    return ExtractedCandidate(
        kind=MemoryKind.PREFERENCE,
        scope=MemoryScope.USER,
        content="回答时先给结论。",
        confidence=0.97,
        evidence_text="以后回答先给结论",
        sensitivity=SensitivityDecision.SAFE,
    )


def test_evaluator_reports_raw_counts_and_quality_rates():
    cases = [
        EvaluationCase(
            case_id="stable-1",
            eligible=True,
            category="stable",
            expected_kind=MemoryKind.PREFERENCE,
            expected_scope=MemoryScope.USER,
        ),
        EvaluationCase(case_id="transient-1", eligible=False, category="transient"),
        EvaluationCase(case_id="sensitive-1", eligible=False, category="sensitive"),
    ]

    report = evaluate_predictions(cases, [[_prediction()], [], []])

    assert report.true_positive == 1
    assert report.false_positive == 0
    assert report.false_negative == 0
    assert report.precision == 1.0
    assert report.recall == 1.0
    assert report.sensitive_persisted == 0
    assert report.passed is True


def test_sensitive_prediction_is_a_hard_gate_failure():
    cases = [EvaluationCase(case_id="sensitive-1", eligible=False, category="sensitive")]

    report = evaluate_predictions(cases, [[_prediction()]])

    assert report.sensitive_persisted == 1
    assert report.passed is False


def test_automatic_mode_requires_mature_passing_gate_report(monkeypatch, tmp_path):
    monkeypatch.setenv("REASONING_MEMORY_EXTRACTION_MODE", "automatic")
    monkeypatch.setenv("MEMORY_EXTRACTION_BASE_URL", "https://extractor.example/v1")
    monkeypatch.setenv("MEMORY_EXTRACTION_API_KEY", "private-key")
    monkeypatch.setenv("MEMORY_EXTRACTION_MODEL", "extractor-model")
    monkeypatch.delenv("MEMORY_EXTRACTION_GATE_REPORT", raising=False)

    try:
        MemoryRagConfig.from_env()
    except ValueError as exc:
        assert "gate report" in str(exc)
    else:
        raise AssertionError("automatic mode started without a gate report")

    report = evaluate_predictions(
        [
            EvaluationCase(
                case_id="stable-1",
                eligible=True,
                category="stable",
                expected_kind=MemoryKind.PREFERENCE,
                expected_scope=MemoryScope.USER,
            )
        ],
        [[_prediction()]],
    )
    report_path = tmp_path / "gate.json"
    write_gate_report(
        report_path,
        report,
        reviewed_turns=500,
        observation_days=7,
    )
    monkeypatch.setenv("MEMORY_EXTRACTION_GATE_REPORT", str(report_path))

    config = MemoryRagConfig.from_env()

    assert config.extraction_mode is ExtractionMode.AUTOMATIC
