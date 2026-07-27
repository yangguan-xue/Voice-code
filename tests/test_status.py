from voice_code.status import (
    default_status_doc_path,
    format_status_area_lines,
    format_status_gap_lines,
    format_status_overview_lines,
    render_status_markdown,
)


def test_status_overview_lines_include_track_evidence_and_next_step():
    lines = format_status_overview_lines()
    text = "\n".join(lines)

    assert "Engineering Progress Dashboard" in text
    assert "tui-premium-ui [complete]" in text
    assert "permission-engine-v2 [complete]" in text
    assert "telemetry-health [complete]" in text
    assert "task-supervisor-and-dlq [complete]" in text
    assert "security-and-data-governance [complete]" in text
    assert "ci-cd-and-runbook [complete]" in text
    assert "specs: 38, 42" in text
    assert "next: add rule-create presets and richer bash risk explanations" in text


def test_status_area_lines_group_tracks_by_maturity():
    lines = format_status_area_lines()
    text = "\n".join(lines)

    assert "Mature Areas" in text
    assert "Partial Areas" in text
    assert "Fragile Areas" in text
    assert "tui-premium-ui" in text
    assert "telemetry-health" in text
    assert "task-supervisor-and-dlq" in text
    assert "security-and-data-governance" in text
    assert "ci-cd-and-runbook" in text
    assert "rule authoring UX" in text
    assert "external gateway stability" in text


def test_status_gap_lines_include_biggest_gaps_and_priorities():
    lines = format_status_gap_lines()
    text = "\n".join(lines)

    assert "Biggest Gaps" in text
    assert "Recommended Priorities" in text
    assert "agent-loop-runtime" in text
    assert "1. external gateway stability" in text
    assert "RAG `automatic`" in text or "rag" in text.lower()


def test_render_status_markdown_includes_dashboard_sections():
    markdown = render_status_markdown(updated_at="2026-07-20")

    assert "# Current Progress Status" in markdown
    assert "更新时间：2026-07-20" in markdown
    assert "<!-- Generated from new/src/voice_code/status.py -->" in markdown
    assert "| `permission-engine-v2` | `complete` |" in markdown
    assert "## Recommended Priorities" in markdown
    assert "## Notes For Agents" in markdown


def test_default_status_doc_path_points_to_docs_file():
    assert default_status_doc_path().as_posix().endswith("/docs/current-progress-status.md")
