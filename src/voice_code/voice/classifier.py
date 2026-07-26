"""指令分类器 — 将 STT 文本分类为 agent_command / control_command / ignore。"""

from __future__ import annotations

import asyncio
import logging

from voice_code.voice.types import (
    CONTROL_KEYWORDS,
    CommandDecision,
    CommandKind,
)

logger = logging.getLogger(__name__)

# 精确控制短语（完整匹配才算控制命令）
_EXACT_CONTROL_PHRASES: set[str] = {"状态", "休眠", "继续", "暂停"}

_GATEKEEPER_PROMPT = """判断用户是否在对AI说话。只回复 yes 或 no。

yes: 提问、下指令、聊天、自言自语
no: 跟别人说话、无意义碎片/噪音、单个字/词

「{text}」"""


class CommandClassifier:
    """V0 指令分类器。

    三层策略：
    1. 空文本 → ignore
    2. 关键词匹配控制命令（停止/别读了/状态/休眠/继续）
    3. 轻量 LLM 门禁判断 agent_command / ignore（max_tokens=5, timeout=3s）
    """

    def __init__(self, model: object | None = None) -> None:
        self._model = model

    async def classify(self, text: str) -> CommandDecision:
        """分类 STT 文本。"""
        cleaned = text.strip()
        if not cleaned:
            return CommandDecision(kind=CommandKind.IGNORE, text=cleaned)

        # Layer 0: heuristic ignore for noise
        if self._is_noise(cleaned):
            logger.info("Classifier: heuristic ignore")
            return CommandDecision(kind=CommandKind.IGNORE, text=cleaned)

        # Layer 1: keyword matching for control commands
        # 精确短语 → 文本本身就是控制命令
        if cleaned in _EXACT_CONTROL_PHRASES:
            control_name = CONTROL_KEYWORDS[cleaned]
            logger.info("Classifier: exact control -> %s", control_name)
            return CommandDecision(
                kind=CommandKind.CONTROL_COMMAND,
                text=cleaned,
                control_name=control_name,
            )

        # 含关键词的短语 → 只匹配"停止"/"别读了"这类操作词
        for keyword, control_name in CONTROL_KEYWORDS.items():
            if keyword in cleaned and keyword not in _EXACT_CONTROL_PHRASES:
                logger.info("Classifier: matched control keyword -> %s", control_name)
                return CommandDecision(
                    kind=CommandKind.CONTROL_COMMAND,
                    text=cleaned,
                    control_name=control_name,
                )

        # Layer 2: LLM gatekeeper
        if self._model is not None:
            try:
                async with asyncio.timeout(5.0):
                    kind = await self._classify_with_llm(cleaned)
            except (TimeoutError, Exception):
                logger.info("Classifier: gatekeeper timeout/error, defaulting to agent_command")
                kind = CommandKind.AGENT_COMMAND
            return CommandDecision(kind=kind, text=cleaned)

        # Fallback: no model available
        logger.info("Classifier: no model, defaulting to agent_command")
        return CommandDecision(kind=CommandKind.AGENT_COMMAND, text=cleaned)

    @staticmethod
    def _is_noise(text: str) -> bool:
        cleaned = text.strip()
        if len(cleaned) <= 1:
            return True
        if all(c in "...,.。。。!！?？~～" for c in cleaned):
            return True
        noise_patterns = {
            "嗯", "哦", "啊", "哈", "呃", "哎",
            "嗯嗯", "哦哦", "啊啊", "哈哈",
            "啊对对对", "好吧", "行吧", "好的吧",
            "嗯嗯嗯", "哦哦哦",
        }
        if cleaned in noise_patterns:
            return True
        if all(c in ".,，。！!?？~～、…" for c in cleaned):
            return True
        return False

    async def _classify_with_llm(self, text: str) -> CommandKind:
        """门禁 LLM 判断用户是否在对 AI 说话。"""
        prompt = _GATEKEEPER_PROMPT.format(text=text)
        response = await self._model.ainvoke(  # type: ignore[union-attr]
            prompt,
            max_tokens=5,
        )
        result = ""
        if hasattr(response, "content"):
            content = response.content
            if isinstance(content, str):
                result = content.strip().lower()
            elif isinstance(content, list):
                result = " ".join(
                    c.get("text", "") if isinstance(c, dict) else str(c)
                    for c in content
                ).strip().lower()
            else:
                result = str(content).strip().lower()
        else:
            result = str(response).strip().lower()

        if "yes" in result:
            logger.info("Classifier: gatekeeper -> agent_command")
            return CommandKind.AGENT_COMMAND
        if "no" in result:
            logger.info("Classifier: gatekeeper -> ignore")
            return CommandKind.IGNORE

        # 模型未按要求输出，有文本则当作 agent_command
        logger.info("Classifier: invalid gatekeeper response, defaulting to agent_command")
        return CommandKind.AGENT_COMMAND
