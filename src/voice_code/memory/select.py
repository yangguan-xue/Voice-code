from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)


_STOP_CHARS = {
    '的', '了', '在', '是', '我', '你', '他', '她', '它',
    '有', '不', '也', '就', '都', '而', '且', '但', '这',
    '那', '什', '么', '怎', '很', '能', '会', '要', '可',
    '以', '和', '与', '或', '对', '于', '被', '把', '让',
    '从', '到', '用', '给', '向', '跟', '比', '上', '下',
    '中', '里', '外', '前', '后', '没', '将', '已', '还',
    '又', '再', '才', '只', '如', '因', '所', '为', '之',
}


def _cjk_chars(text: str) -> set[str]:
    return {ch for ch in text if "\u4e00" <= ch <= "\u9fff" and ch not in _STOP_CHARS}


def _extract_terms(text: str) -> set[str]:
    terms = set()
    for m in re.finditer(r"\w+", text):
        terms.add(m.group().lower())
    for m in re.finditer(r"[\u4e00-\u9fff]+", text):
        terms.add(m.group())
    return terms


_MEMORY_SOURCE_WEIGHTS = {"project": 4.0, "feedback": 2.0, "reference": 1.5, "user": 0.0}


def _get_source_weight(candidate: dict) -> float:
    mem_type = (candidate.get("source_kind") or "").lower()
    return _MEMORY_SOURCE_WEIGHTS.get(mem_type, 0.0)


def rerank_candidates(
    query: str,
    candidates: list[dict],
    top_k: int = 5,
) -> list[dict]:
    query_terms = _extract_terms(query)
    query_cjk = _cjk_chars(query)

    logger.debug("rerank: query='%s' terms=%s cjk=%s candidates=%d",
                 query, query_terms, query_cjk, len(candidates))

    scored: list[tuple[float, dict]] = []
    for c in candidates:
        score = _compute_score(c, query_terms, query_cjk)
        scored.append((score, c))

    scored.sort(key=lambda x: x[0], reverse=True)
    result = [c for _, c in scored[:top_k]]
    logger.debug(
        "rerank: top %d scores=[%s] names=[%s]",
        top_k,
        ", ".join(f"{s:.1f}" for s, _ in scored[:top_k]),
        ", ".join(c.get("name", "?")[:30] for _, c in scored[:top_k]),
    )
    return result


def _compute_score(
    candidate: dict, query_terms: set[str], query_cjk: set[str],
) -> float:
    score = 10.0

    name = candidate.get("name") or ""
    desc = candidate.get("description") or ""
    content = candidate.get("content") or ""
    tags_str = candidate.get("tags") or ""

    # ── Field importance: name > tags > description >> content ──

    name_cjk = _cjk_chars(name)
    tags_cjk = _cjk_chars(tags_str)
    desc_cjk = _cjk_chars(desc)
    content_cjk = _cjk_chars(content)

    # Name char overlap (most important signal)
    score += len(query_cjk & name_cjk) * 2.0

    # Tags char overlap (curated keywords)
    score += len(query_cjk & tags_cjk) * 1.5

    # Description char overlap
    score += len(query_cjk & desc_cjk) * 0.8

    # Content char overlap (noisy, low weight)
    score += len(query_cjk & content_cjk) * 0.2

    # ── Term-level bonus (when query words align exactly) ──

    for term in query_terms:
        if term in name:
            score += 5.0
        elif term in tags_str:
            score += 4.0
        elif term in desc:
            score += 2.0
        elif term in content:
            score += 0.5

    # ── FTS rank: smaller signal ──
    try:
        rank = float(candidate.get("rank", "0"))
        score -= rank * 0.2
    except (ValueError, TypeError):
        pass

    # ── Memory type weight: project > feedback > reference > user ──
    mem_type = (candidate.get("type") or "").lower()
    if mem_type == "project":
        score += 3.0
    elif mem_type == "feedback":
        score += 2.0
    elif mem_type == "reference":
        score += 1.0

    # ── Penalize generic template memories ──
    if name.startswith("记住，"):
        score -= 2.0
    if name.startswith("注意，"):
        score -= 2.0

    return score
