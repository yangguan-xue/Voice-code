from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from voice_code.memory.models import MemoryEntry
from voice_code.memory.paths import get_index_path_for_scope
from voice_code.memory.store import list_entries

logger = logging.getLogger(__name__)


def _get_connection(index_path: Path) -> sqlite3.Connection:
    index_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(index_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=OFF")
    return conn


_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
    id,
    name,
    description,
    content,
    tags,
    tokenize='unicode61'
);
"""


def build_index(scope: str, project_root: str | None = None) -> None:
    index_path = get_index_path_for_scope(scope, project_root)
    logger.info("build_index: scope=%s path=%s", scope, index_path)
    conn = _get_connection(index_path)
    try:
        conn.execute(_SCHEMA)
        conn.execute("DELETE FROM memory_fts")
        entries = list_entries(scope, project_root, include_archived=False)
        logger.info("build_index: indexing %d entries", len(entries))
        for entry in entries:
            tags_str = " ".join(entry.tags)
            sql = (
                "INSERT INTO memory_fts(id, name, description, content, tags) "
                "VALUES (?, ?, ?, ?, ?)"
            )
            conn.execute(sql,
                (entry.id, entry.name, entry.description, entry.content, tags_str),
            )
        conn.commit()
    finally:
        conn.close()


def rebuild_index(scope: str, project_root: str | None = None) -> None:
    index_path = get_index_path_for_scope(scope, project_root)
    if index_path.exists():
        index_path.unlink()
    build_index(scope, project_root)


def _sanitize_fts_query(text: str) -> str:
    sanitized = ""
    for ch in text:
        if ch.isalnum() or ch in (" ", "-", "_"):
            sanitized += ch
        elif ord(ch) > 0x2E80:
            sanitized += ch
        else:
            sanitized += " "
    terms = [t for t in sanitized.split() if len(t) > 0]
    return " OR ".join(f'"{t}"' for t in terms) if terms else ""


def _fallback_search(
    query: str,
    scope: str,
    project_root: str | None = None,
    limit: int = 10,
) -> list[dict[str, str]]:
    import re

    from voice_code.memory.store import list_entries
    entries = list_entries(scope, project_root, include_archived=False)

    terms = [t for t in re.split(r"[\s,，。？、！；：? !;:]+", query) if len(t) > 0]
    logger.debug("Fallback search query='%s' terms=%s", query, terms)
    if len(terms) < 1:
        logger.debug("Fallback search skipped: no terms")
        return []

    term_pattern = re.compile("|".join(re.escape(t) for t in terms))
    cjk_chars = {ch for ch in query if ord(ch) > 0x2E80}

    scored: list[tuple[float, dict[str, str]]] = []
    for e in entries:
        name = e.name or ""
        desc = e.description or ""
        content = e.content or ""
        tags_str = " ".join(e.tags)

        name_matches = len(term_pattern.findall(name))
        desc_matches = len(term_pattern.findall(desc))
        content_matches = len(term_pattern.findall(content))
        tag_matches = len(term_pattern.findall(tags_str))

        term_score = (
            name_matches * 20.0
            + desc_matches * 10.0
            + tag_matches * 5.0
            + content_matches * 2.0
        )
        if cjk_chars:
            combined_set = set(name + desc + content + tags_str)
            char_score = len(cjk_chars & combined_set) * 0.5
        else:
            char_score = 0.0

        weight = term_score + char_score
        if weight < 1.5:
            continue

        scored.append((float(-weight), {
            "id": e.id,
            "name": e.name,
            "description": e.description,
            "tags": tags_str,
            "rank": "0",
        }))
    scored.sort(key=lambda x: x[0])
    results = [s for _, s in scored[:limit]]
    logger.debug("Fallback search: %d candidates scored, %d returned", len(scored), len(results))
    return results


def search_index(
    query: str,
    scope: str,
    project_root: str | None = None,
    limit: int = 10,
) -> list[dict[str, str]]:
    index_path = get_index_path_for_scope(scope, project_root)
    if not index_path.exists():
        return []
    fts_query = _sanitize_fts_query(query)
    if not fts_query:
        logger.debug("FTS query empty after sanitize, falling back")
        return _fallback_search(query, scope, project_root, limit)
    conn = _get_connection(index_path)
    try:
        conn.execute(_SCHEMA)
        sql = (
            "SELECT id, name, description, tags, rank FROM memory_fts "
            "WHERE memory_fts MATCH ? "
            "ORDER BY rank "
            "LIMIT ?"
        )
        cursor = conn.execute(sql, (fts_query, limit))
        results: list[dict[str, str]] = []
        for row in cursor.fetchall():
            results.append({
                "id": row[0],
                "name": row[1],
                "description": row[2],
                "tags": row[3],
                "rank": str(row[4]),
            })
        if results:
            logger.debug(
                "FTS search returned %d results for '%s': names=%s ranks=%s",
                len(results), query,
                [r["name"] for r in results],
                [r["rank"] for r in results],
            )
            return results
        logger.debug("FTS search returned 0 results, falling back")
        return _fallback_search(query, scope, project_root, limit)
    except Exception:
        logger.warning("FTS search failed, falling back", exc_info=True)
        return _fallback_search(query, scope, project_root, limit)
    finally:
        conn.close()


def index_exists(scope: str, project_root: str | None = None) -> bool:
    index_path = get_index_path_for_scope(scope, project_root)
    return index_path.exists()


def add_to_index(entry: MemoryEntry, project_root: str | None = None) -> None:
    index_path = get_index_path_for_scope(entry.scope.value, project_root)
    conn = _get_connection(index_path)
    try:
        conn.execute(_SCHEMA)
        tags_str = " ".join(entry.tags)
        conn.execute(
            "INSERT INTO memory_fts(id, name, description, content, tags) VALUES (?, ?, ?, ?, ?)",
            (entry.id, entry.name, entry.description, entry.content, tags_str),
        )
        conn.commit()
    finally:
        conn.close()


def remove_from_index(entry_id: str, scope: str, project_root: str | None = None) -> None:
    index_path = get_index_path_for_scope(scope, project_root)
    if not index_path.exists():
        return
    conn = _get_connection(index_path)
    try:
        conn.execute(_SCHEMA)
        conn.execute("DELETE FROM memory_fts WHERE id = ?", (entry_id,))
        conn.commit()
    finally:
        conn.close()
