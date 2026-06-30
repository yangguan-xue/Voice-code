from __future__ import annotations

import logging

from voice_code.memory.index import search_index
from voice_code.memory.select import rerank_candidates
from voice_code.memory.store import get_entry

logger = logging.getLogger(__name__)

_SYNONYM_MAP: dict[str, list[str]] = {
    "发版": ["发布", "上线", "release", "发版本"],
    "图形界面": ["tui", "GUI", "界面", "图形"],
    "启动": ["开启", "运行", "启动"],
    "安装": ["添加", "引入", "依赖", "装"],
    "删掉": ["删除", "移除", "清空", "清理", "清除"],
    "搜索": ["查找", "寻找", "搜", "查"],
    "检查": ["检测", "校验", "verify", "审核"],
    "配置": ["设置", "config", "设定", "参数"],
    "日志": ["log", "logging", "记录"],
    "对话": ["会话", "session", "聊天", "历史"],
    "记录": ["保存", "存储", "存放", "持久化"],
    "地址": ["位置", "路径", "入口", "URL"],
    "文件夹": ["目录", "路径", "dir", "folder"],
    "命令": ["指令", "命令", "命令行"],
    "测试": ["test", "pytest", "单元测试", "集成测试"],
    "库": ["依赖", "包", "package", "模块"],
    "代码": ["代码", "源码", "source", "程序"],
    "项目": ["工程", "项目", "repo", "仓库"],
    "改": ["修改", "编辑", "变更", "改动", "修"],
}


def _expand_query(query: str) -> str:
    expanded = query
    for word, synonyms in _SYNONYM_MAP.items():
        if word in query:
            for syn in synonyms:
                if syn not in expanded:
                    expanded += " " + syn
    return expanded


def retrieve_memories(
    query: str,
    project_root: str | None = None,
    limit: int = 5,
) -> list[dict]:
    expanded = _expand_query(query)
    search_query = expanded if expanded != query else query
    if search_query != query:
        logger.info("retrieve_memories: query expanded: '%s' -> '%s'", query, search_query)
    logger.info("retrieve: query='%s' proot=%s lim=%d", search_query, project_root, limit)
    candidates = []
    if project_root:
        project_results = search_index(search_query, "project", project_root, limit=limit * 5)
        logger.debug("retrieve: project scope %d candidates", len(project_results))
        for r in project_results:
            candidates.append({**r, "_scope": "project"})
    user_results = search_index(search_query, "user", project_root=None, limit=limit * 5)
    logger.debug("retrieve_memories: user scope returned %d candidates", len(user_results))
    for r in user_results:
        candidates.append({**r, "_scope": "user"})

    logger.debug("retrieve: total %d candidates → top_k=%d", len(candidates), limit)
    selected = rerank_candidates(query, candidates, top_k=limit)

    enriched = []
    for s in selected:
        scope = s.get("_scope", "project")
        entry = get_entry(s["id"], scope, project_root if scope == "project" else None)
        if entry is not None:
            enriched.append({
                "id": entry.id,
                "name": entry.name,
                "type": entry.type.value,
                "scope": entry.scope.value,
                "description": entry.description,
                "content": entry.content,
                "tags": entry.tags,
                "source_kind": entry.source.kind,
            })
    logger.info(
        "retrieve_memories: returning %d results: names=%s",
        len(enriched),
        [e["name"] for e in enriched],
    )
    return enriched


def retrieve_memories_for_scope(
    query: str,
    scope: str,
    project_root: str | None = None,
    limit: int = 5,
) -> list[dict]:
    logger.info("retrieve_memories_for_scope query='%s' scope=%s limit=%d", query, scope, limit)
    results = search_index(query, scope, project_root, limit=limit * 2)
    logger.debug("retrieve_for_scope: %d results → top_k=%d", len(results), limit)
    selected = rerank_candidates(query, results, top_k=limit)

    enriched = []
    for s in selected:
        entry = get_entry(s["id"], scope, project_root if scope == "project" else None)
        if entry is not None:
            enriched.append({
                "id": entry.id,
                "name": entry.name,
                "type": entry.type.value,
                "scope": entry.scope.value,
                "description": entry.description,
                "content": entry.content,
                "tags": entry.tags,
                "source_kind": entry.source.kind,
            })
    logger.info(
        "retrieve_memories_for_scope: returning %d results: names=%s",
        len(enriched),
        [e["name"] for e in enriched],
    )
    return enriched
