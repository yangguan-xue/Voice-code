"""Agent 桥接层 — 将文本指令送入 reasoning agent 并收集回复。"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable, Coroutine

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from voice_code.agent.abort import AbortSignal
from voice_code.agent.loop import agent_loop
from voice_code.agent.types import AgentEvent, EventType
from voice_code.permissions import PermissionContext
from voice_code.runtime import RuntimeBootstrap, bootstrap_runtime
from voice_code.voice.types import SUMMARY_FALLBACK_CHARS, SUMMARY_MAX_CHARS

logger = logging.getLogger(__name__)


class AgentBridge:
    """将文本指令送入当前 reasoning agent 并收集最终 assistant 文本。

    不依赖 TUI 输入框，直接调用 runtime:
      - bootstrap_runtime()
      - agent_loop()

    维护会话连续性：
      - 每轮结束后从 transcript 回读消息作为下一轮的 resume_messages
      - 传递 transcript_writer 给 agent_loop() 以正确记录
    """

    def __init__(self, profile: str | None = None, summary_model: ChatOpenAI | None = None) -> None:
        self._profile = profile
        self._summary_model = summary_model
        self._runtime: RuntimeBootstrap | None = None
        self._abort_signal = AbortSignal()
        self._perm_ctx = PermissionContext(mode="bypassPermissions")
        self._resume_messages: list[BaseMessage] | None = None
        self._turn_lock = asyncio.Lock()
        self._event_callback: Callable[[AgentEvent], Coroutine[None, None, None]] | None = None
        logger.info("AgentBridge initialized: profile=%s", profile)

    def on_event(self, callback: Callable[[AgentEvent], Coroutine[None, None, None]]) -> None:
        """注册事件回调 — 每轮 agent 事件的流式输出。"""
        self._event_callback = callback

    async def start(self) -> None:
        """初始化 runtime。"""
        self._runtime = await bootstrap_runtime(profile=self._profile)
        self._resume_messages = None
        logger.info("AgentBridge runtime ready: %s", self._runtime.session_id)

    async def run_turn(self, text: str) -> str:
        """执行一轮 agent 任务，返回最终 assistant 文本。

        维护会话连续性：每轮结束后回读 transcript，
        下一轮自动带上历史消息作为 resume_messages。

        并发安全：asyncio.Lock 保证同时只有一轮在执行。

        Raises:
            RuntimeError: agent 执行失败或无 runtime。
        """
        if not self._runtime:
            raise RuntimeError("AgentBridge not started")

        if not text or not text.strip():
            return ""

        async with self._turn_lock:
            self._abort_signal.clear()

            result = await self._execute_turn(text)

            # 回读 transcript，保留会话连续性
            if self._runtime.transcript_writer is not None:
                try:
                    self._resume_messages = self._runtime.transcript_writer.read_all_messages()
                except Exception:
                    logger.exception("AgentBridge: failed to read transcript messages")

            return result

    async def _execute_turn(self, text: str) -> str:
        """执行单轮 agent_loop()，收集最终 assistant 文本。"""
        if not self._runtime:
            raise RuntimeError("AgentBridge not started")

        final_text_parts: list[str] = []
        reasoning_parts: list[str] = []
        has_error = False
        error_msg = ""
        finish_reason = "unknown"
        event_counts: dict[str, int] = {}

        try:
            async with asyncio.timeout(300):
                async for event in agent_loop(
                    user_input=text.strip(),
                    tools=self._runtime.tools,
                    system_prompt=self._runtime.prompt,
                    model=self._runtime.model,
                    permission_context=self._perm_ctx,
                    abort_signal=self._abort_signal,
                    resume_messages=self._resume_messages,
                    transcript_writer=self._runtime.transcript_writer,
                    fallback_model=self._runtime.fallback_model,
                ):
                    etype = event.type.name
                    event_counts[etype] = event_counts.get(etype, 0) + 1

                    # 流式回调（供 orchestrator 打印到终端）
                    if self._event_callback is not None:
                        try:
                            await self._event_callback(event)
                        except Exception:
                            logger.exception("AgentBridge: event callback error")

                    if event.type == EventType.TEXT:
                        final_text_parts.append(str(event.content))

                    elif event.type == EventType.REASONING:
                        reasoning_parts.append(str(event.content))

                    elif event.type == EventType.FINISH:
                        finish_reason = event.finish_reason or "completed"
                        if event.finish_reason in ("error", "interrupted"):
                            has_error = True
                            error_msg = event.content or event.finish_reason

                    elif event.type == EventType.ERROR:
                        # 工具层 / compaction / fallback 通知，不是致命错误
                        logger.info("AgentBridge: event ERROR: %s", str(event.content)[:100])

        except TimeoutError:
            logger.error("AgentBridge: agent turn timed out")
            raise RuntimeError("agent turn timed out") from None

        result = "".join(final_text_parts).strip()

        # V0 fallback: 如果模型只产出 REASONING（DeepSeek thinking mode），
        # 取全部 reasoning 作为播报文本
        if not result and reasoning_parts:
            result = "".join(reasoning_parts).strip()
            logger.info(
                "AgentBridge: no TEXT, falling back to REASONING (%d parts, %d chars)",
                len(reasoning_parts), len(result),
            )

        logger.info(
            "AgentBridge: events=%s, finish=%s, result (%d chars): %s",
            event_counts, finish_reason, len(result), result[:100],
        )
        if has_error:
            raise RuntimeError(f"agent turn failed: {error_msg}")
        return result

    def interrupt(self) -> None:
        """中断当前 agent 任务。"""
        logger.info("AgentBridge: interrupt triggered")
        self._abort_signal.trigger()

    def get_status(self) -> str:
        """返回当前状态描述。"""
        if self._runtime is None:
            return "not started"
        if self._abort_signal.is_triggered():
            return "interrupted"
        return "ready"

    @staticmethod
    def _strip_markdown(raw: str) -> str:
        """去除 markdown 代码块和行内代码，保留自然语言正文。"""
        s = re.sub(r'```[\s\S]*?```', '', raw)
        s = re.sub(r'`[^`\n]+`', '', s)
        s = re.sub(r'\n{3,}', '\n\n', s)
        return s.strip()

    @staticmethod
    def _strip_lowercase_english(text: str) -> str:
        """仅移除全小写英文单词（如 the、and、this），保留首字母大写的专有名词和缩写。"""
        # 保护首字母大写单词（专有名词）和大写缩写
        placeholders: dict[str, str] = {}
        def _protect(m: re.Match[str]) -> str:
            tok = m.group(0)
            ph = f"\x00PROTECT_{len(placeholders)}\x00"
            placeholders[ph] = tok
            return ph
        # 保护首字母大写的单词（Claude, OpenAI, Codex...）
        text = re.sub(r'\b[A-Z][a-z]+([A-Z][a-z]+)*\b', _protect, text)
        # 保护连续 2-5 个大写字母缩写
        text = re.sub(r'\b[A-Z]{2,5}\b', _protect, text)
        # 移除剩余的全小写英文单词
        text = re.sub(r'\b[a-z]+\b', '', text)
        # 恢复被保护的词
        for ph, tok in placeholders.items():
            text = text.replace(ph, tok)
        text = re.sub(r' +', ' ', text)
        return text.strip()

    async def summarize_for_speech(self, text: str) -> str:
        """将 agent 回复转为适合 TTS 播报的口语短句。

        短文本原样返回；长文本调用快速模型做口语转述。
        """
        text = text.strip()
        if not text:
            return text

        model = self._summary_model

        # 没有快速摘要模型 → strip markdown + 移除英文后截断
        if model is None:
            cleaned = self._strip_lowercase_english(self._strip_markdown(text))
            if len(cleaned) <= SUMMARY_FALLBACK_CHARS:
                return cleaned
            return cleaned[:SUMMARY_FALLBACK_CHARS]

        # 无实际内容的招呼/英文短语/自我介绍跳过，让 TTS 保持静默
        if not re.search(r'[\u4e00-\u9fff]', text):
            return ""
        if re.search(
            r'^(我是你的|我是.*(?:助手|Claude|AI)|你好[！!]+(?:我是|！))',
            text.strip(),
        ):
            return ""

        prompt = (
            f"这是大脑刚刚想出来的内容，说给哥哥听：\n\n{text[:2000]}"
        )
        try:
            response = await model.ainvoke(  # type: ignore[union-attr]
                [
                    SystemMessage(
                        content=(
                            "你就是小奕，是哥哥的编程助手。"
                            "你的大脑负责思考执行任务，你负责把大脑想的话说出来。"
                            "直接以第一人称对哥哥说话。"
                            "用自然的交流语言，不要照搬大脑的原文。"
                            f"不超过{SUMMARY_MAX_CHARS}字。"
                            "先告诉哥哥任务做完了没有，做成了什么。"
                            "不要列举具体文件名、路径、接口地址，只需要说数量（几个文件、几个接口）。"
                            "大量英文连续出现时，不要逐一念出，用中文概括含义即可。"
                            "去掉代码、符号、标记，严格输出纯文本。"
                            "保留英文专有名词和技术术语。"
                            "开头叫哥哥，后面加空格和逗号让播报停顿。"
                        )
                    ),
                    HumanMessage(content=prompt),
                ],
                max_tokens=SUMMARY_MAX_CHARS * 2,  # type: ignore[call-arg]
            )
            content = response.content
            if isinstance(content, list):
                spoken = "".join(
                    c.get("text", "") if isinstance(c, dict) else str(c)
                    for c in content
                )
            elif isinstance(content, str):
                spoken = content
            else:
                spoken = str(content)
            result = spoken.strip()
            if not result or len(result) > SUMMARY_MAX_CHARS * 2:
                cleaned = self._strip_lowercase_english(self._strip_markdown(text))
                return cleaned[:SUMMARY_FALLBACK_CHARS]
            # 轻量清理：符号、表情、多余空格
            result = re.sub(r'[\U0001F300-\U0001F9FF`*_~#\-/\\()（）【】\[\]{}]', '', result)
            result = re.sub(r' +', ' ', result).strip()
            logger.info("AgentBridge: summarized %d -> %d chars", len(text), len(result))
            return result
        except Exception:
            logger.exception("AgentBridge: speech summarization failed")
            cleaned = self._strip_lowercase_english(self._strip_markdown(text))
            return cleaned[:SUMMARY_FALLBACK_CHARS]

    async def shutdown(self) -> None:
        """关闭 bridge，写入 transcript。"""
        if self._runtime is not None:
            self._runtime.transcript_writer.close()
            logger.info("AgentBridge: shutdown complete")
