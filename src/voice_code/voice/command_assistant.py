"""命令助手：负责用户输入路由、编程结果审核和轻量上下文维护。"""

from __future__ import annotations

import json
import logging
import re
from collections import deque
from collections.abc import Mapping, Sequence

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool, tool

from voice_code.tools.todo_write import todo_write
from voice_code.voice.types import (
    SUPERVISOR_CONTEXT_UPDATE_CHARS,
    SUPERVISOR_FAILED_TEXT,
    SUPERVISOR_MEMORY_LIMIT,
    SupervisorAction,
    SupervisorDecision,
)

logger = logging.getLogger(__name__)
_MAX_TOOL_ROUNDS = 3


@tool
def ask_user_question(question: str) -> str:
    """为语音模式生成一句简短、明确的补问内容。"""
    cleaned = question.strip()
    if not cleaned:
        return "请补充更多信息。"
    return cleaned

_USER_SYSTEM_PROMPT = """你是“小奕”的前台大脑，负责决定当前用户输入应该如何路由。

你可以在必要时调用两个轻量工具：
- todo_write: 维护当前任务清单，只用于内部追踪
- ask_user_question: 把补问措辞打磨成一句适合直接对用户说的话

输出格式规则（违反会导致系统崩溃）：
- 只输出一段纯 JSON，不要 Markdown 代码块或反引号
- 不要任何解释，不要任何多余文字
- JSON 必须合法，不要尾随逗号

动作定义：
- reply: 纯聊天或简单直接回复，不调用编程助手
- dispatch: 需要调用编程助手执行具体任务
- ask_user: 缺少关键信息，需要向用户补问一句话
- fail: 当前无法可靠处理，礼貌收口

输出字段：
- action: 必填，reply / dispatch / ask_user / fail
- message: 给用户的直接回复；reply / ask_user / fail 必填
- task: 给编程助手的明确任务；dispatch 必填
- reason: 简短原因，供日志调试
- context_update: 200 字以内，写给内部记忆的轻量摘要
"""

_REVIEW_SYSTEM_PROMPT = """你是“小奕”的前台大脑，正在审核后台编程助手的执行结果。

你可以在必要时调用两个轻量工具：
- todo_write: 更新当前任务状态
- ask_user_question: 把补问措辞打磨成一句适合直接对用户说的话

输出格式规则（违反会导致系统崩溃）：
- 只输出一段纯 JSON，不要 Markdown 代码块或反引号
- 不要任何解释，不要任何多余文字
- JSON 必须合法，不要尾随逗号

动作定义：
- continue: 结果还不够，需要给后台一个更明确的下一步任务
- finish: 结果已足够，对用户给出最终答复
- ask_user: 缺少关键信息，需要向用户补问一句话
- fail: 连续尝试后仍不可靠，礼貌收口

输出字段：
- action: 必填，continue / finish / ask_user / fail
- message: 给用户的最终回复或补问；finish / ask_user / fail 必填
- task: 下一步派给编程助手的明确任务；continue 必填
- reason: 简短原因，供日志调试
- context_update: 200 字以内，写给内部记忆的轻量摘要

连续尝试太多、没有明显进展、或结果已经足够时，不要继续派单。
"""

_CODE_TASK_HINTS = (
    "代码",
    "文件",
    "项目",
    "目录",
    "结构",
    "修改",
    "修复",
    "重构",
    "运行",
    "测试",
    "查看",
    "搜索",
    "grep",
    "glob",
    "命令",
    "bug",
    "报错",
    "实现",
)


class CommandAssistant:
    """命令助手：输出结构化调度动作。"""

    def __init__(
        self,
        model: object | None = None,
        *,
        tools: Sequence[BaseTool] | None = None,
        memory_limit: int = SUPERVISOR_MEMORY_LIMIT,
        context_update_chars: int = SUPERVISOR_CONTEXT_UPDATE_CHARS,
    ) -> None:
        self._model = model
        self._memory_limit = memory_limit
        self._context_update_chars = context_update_chars
        self._memory: deque[str] = deque(maxlen=memory_limit)
        self._tools = list(tools) if tools is not None else [todo_write, ask_user_question]
        self._tool_map = {tool_.name: tool_ for tool_ in self._tools}
        self._tool_model = None
        if self._model is not None and hasattr(self._model, "bind_tools"):
            try:
                self._tool_model = self._model.bind_tools(self._tools)  # type: ignore[union-attr]
            except Exception:
                logger.exception("CommandAssistant: bind_tools failed, falling back to plain model")
                self._tool_model = None

    async def decide_user_turn(self, text: str) -> SupervisorDecision:
        cleaned = text.strip()
        if not cleaned:
            return self._fail("哥哥，我这次没听清你的具体要求。", "empty_user_text")

        if self._model is None:
            return self._heuristic_user_decision(cleaned)

        payload = {
            "memory": list(self._memory),
            "user_text": cleaned,
        }
        return await self._invoke_model(
            _USER_SYSTEM_PROMPT,
            payload,
            phase="user",
        )

    async def review_agent_result(
        self,
        *,
        user_text: str,
        task: str,
        agent_result: str,
        dispatch_count: int,
    ) -> SupervisorDecision:
        if self._model is None:
            return SupervisorDecision(
                action=SupervisorAction.FINISH,
                message=agent_result.strip() or "哥哥，我这轮已经处理完了。",
                reason="no_supervisor_model",
                context_update=f"任务完成：{task[:80]}",
            )

        payload = {
            "memory": list(self._memory),
            "user_text": user_text.strip(),
            "task": task.strip(),
            "agent_result": agent_result.strip(),
            "dispatch_count": dispatch_count,
        }
        return await self._invoke_model(
            _REVIEW_SYSTEM_PROMPT,
            payload,
            phase="review",
        )

    def apply_context_update(self, text: str) -> None:
        cleaned = text.strip()
        if not cleaned:
            return
        self._memory.append(cleaned[: self._context_update_chars])

    def get_memory(self) -> list[str]:
        return list(self._memory)

    async def _invoke_model(
        self,
        system_prompt: str,
        payload: Mapping[str, object],
        *,
        phase: str,
    ) -> SupervisorDecision:
        try:
            response = await self._invoke_with_optional_tools(system_prompt, payload)
        except Exception:
            logger.exception("CommandAssistant: model invocation failed during %s phase", phase)
            return self._fail(SUPERVISOR_FAILED_TEXT, f"{phase}_invoke_error")

        raw = self._extract_text(response)
        return self._parse_decision(raw, phase=phase)

    def _parse_decision(self, raw: str, *, phase: str) -> SupervisorDecision:
        candidate = self._extract_json(raw)
        if candidate is None:
            logger.warning("CommandAssistant: invalid JSON during %s phase: %s", phase, raw[:200])
            return self._fail(SUPERVISOR_FAILED_TEXT, f"{phase}_invalid_json")

        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            logger.warning(
                "CommandAssistant: JSON decode failed during %s phase: %s",
                phase,
                candidate[:200],
            )
            return self._fail(SUPERVISOR_FAILED_TEXT, f"{phase}_json_decode_error")

        action_text = str(data.get("action", "")).strip().lower()
        try:
            action = SupervisorAction(action_text)
        except ValueError:
            return self._fail(SUPERVISOR_FAILED_TEXT, f"{phase}_unknown_action")

        message = str(data.get("message", "")).strip()
        task = str(data.get("task", "")).strip()
        reason = str(data.get("reason", "")).strip()
        context_update = str(data.get("context_update", "")).strip()[
            : self._context_update_chars
        ]

        if (
            action in {
                SupervisorAction.REPLY,
                SupervisorAction.ASK_USER,
                SupervisorAction.FINISH,
                SupervisorAction.FAIL,
            }
            and not message
        ):
            return self._fail(SUPERVISOR_FAILED_TEXT, f"{phase}_missing_message")
        if action in {SupervisorAction.DISPATCH, SupervisorAction.CONTINUE} and not task:
            return self._fail(SUPERVISOR_FAILED_TEXT, f"{phase}_missing_task")
        if phase == "user" and action in {SupervisorAction.CONTINUE, SupervisorAction.FINISH}:
            return self._fail(SUPERVISOR_FAILED_TEXT, "user_phase_illegal_action")
        if phase == "review" and action in {SupervisorAction.REPLY, SupervisorAction.DISPATCH}:
            return self._fail(SUPERVISOR_FAILED_TEXT, "review_phase_illegal_action")

        return SupervisorDecision(
            action=action,
            message=message,
            task=task,
            reason=reason,
            context_update=context_update,
        )

    def _heuristic_user_decision(self, text: str) -> SupervisorDecision:
        if any(hint in text for hint in _CODE_TASK_HINTS):
            return SupervisorDecision(
                action=SupervisorAction.DISPATCH,
                task=text,
                reason="heuristic_code_task",
                context_update=f"用户请求：{text[:80]}",
            )
        return SupervisorDecision(
            action=SupervisorAction.REPLY,
            message=f"哥哥，{text}",
            reason="heuristic_reply",
            context_update=f"用户闲聊：{text[:80]}",
        )

    @staticmethod
    def _extract_text(response: object) -> str:
        content = getattr(response, "content", response)
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, Sequence):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    parts.append(str(item.get("text", "")))
                else:
                    parts.append(str(item))
            return "".join(parts).strip()
        return str(content).strip()

    async def _invoke_with_optional_tools(
        self,
        system_prompt: str,
        payload: Mapping[str, object],
    ) -> object:
        messages: list[object] = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
        ]
        runner = self._tool_model or self._model

        for _ in range(_MAX_TOOL_ROUNDS + 1):
            response = await runner.ainvoke(  # type: ignore[union-attr]
                messages,
                max_tokens=300,
            )
            messages.append(response)
            tool_calls = getattr(response, "tool_calls", []) or []
            if not tool_calls:
                return response

            for raw_call in tool_calls:
                name, args, tool_call_id = self._parse_tool_call(raw_call)
                result = await self._execute_tool(name, args)
                messages.append(ToolMessage(content=result, tool_call_id=tool_call_id))

        raise RuntimeError("command assistant exceeded tool round limit")

    async def _execute_tool(self, name: str, args: dict[str, object],) -> str:
        tool_ = self._tool_map.get(name)
        if tool_ is None:
            return f"Unknown tool: {name}"
        try:
            if hasattr(tool_, "ainvoke"):
                result = await tool_.ainvoke(args)  # type: ignore[misc]
            else:
                result = tool_.invoke(args)  # type: ignore[misc]
        except Exception:
            logger.exception("CommandAssistant: tool %s failed", name)
            return f"Tool {name} failed."
        return str(result)

    @staticmethod
    def _parse_tool_call(raw_call: object) -> tuple[str, dict[str, object], str]:
        if isinstance(raw_call, dict):
            name = str(raw_call.get("name", ""))
            args = raw_call.get("args", {})
            tool_call_id = str(raw_call.get("id", ""))
        else:
            name = str(getattr(raw_call, "name", "") or "")
            args = getattr(raw_call, "args", {}) or {}
            tool_call_id = str(getattr(raw_call, "id", "") or "")

        if isinstance(args, str):
            try:
                parsed_args = json.loads(args)
            except json.JSONDecodeError:
                parsed_args = {}
        elif isinstance(args, dict):
            parsed_args = args
        else:
            parsed_args = {}
        return name, parsed_args, tool_call_id

    @staticmethod
    def _extract_json(raw: str) -> str | None:
        text = raw.strip()
        if not text:
            return None

        # 1) 尝试从 markdown 代码块提取
        #    先找完整闭合的，再试试可能被截断的
        match = re.search(r"```(?:json)?\s*\n?(\{[\s\S]*?\})\s*\n?```", text)
        if match:
            return match.group(1).strip()
        # 尝试从代码块提取（可能被截断，没有闭合 ```）
        for pattern in (
            r"```(?:json)?\s*\n?(\{[\s\S]*?\})\s*\n?```",  # 完整闭合
            r"```(?:json)?\s*\n?(\{[\s\S]*?\})\s*",        # 闭合 JSON 后无 ```
            r"```(?:json)?\s*\n?(\{[\s\S]*\})",            # 尽可能匹配到最后一个 }
            r"```(?:json)?\s*\n?(\{[\s\S]*)",              # 无 } 也收下
        ):
            match = re.search(pattern, text)
            if match:
                candidate = match.group(1).strip()
                break
        else:
            # 2) 直接找最外层花括号
            start = text.find("{")
            end = text.rfind("}")
            if start == -1 or (end != -1 and end <= start):
                return None
            candidate = text[start:] if end == -1 else text[start:end + 1]

        import json as _json

        # 1) 直接用 json.loads 验证
        try:
            _json.loads(candidate)
            return candidate
        except _json.JSONDecodeError:
            pass

        # 2) 尝试修复常见问题
        fixed = re.sub(r",\s*}", "}", candidate)
        fixed = re.sub(r",\s*]", "]", fixed)
        fixed = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", fixed)
        fixed = re.sub(r"\r?\n", "", fixed)
        try:
            _json.loads(fixed)
            return fixed
        except _json.JSONDecodeError:
            pass

        # 3) 找到最深层闭合的 {} 区间（处理截断情况）
        depth = 0
        best_close = -1
        for i, ch in enumerate(candidate):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            if depth == 0 and ch == "}":
                best_close = i
        if best_close > 0:
            sub = candidate[:best_close + 1]
            try:
                _json.loads(sub)
                return sub
            except _json.JSONDecodeError:
                pass

        # 4) 尝试修复不完整 JSON：补全未闭合的引号和花括号
        repaired = candidate.rstrip()
        repaired = re.sub(r"\r?\n", "", repaired)
        in_string = False
        escaped = False
        for ch in repaired:
            if escaped:
                escaped = False
                continue
            if ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = not in_string
        if in_string:
            repaired += '"'
        if repaired.count("{") > repaired.count("}"):
            repaired += "}" * (repaired.count("{") - repaired.count("}"))
        if repaired != candidate:
            try:
                _json.loads(repaired)
                return repaired
            except _json.JSONDecodeError:
                pass

        # 5) 逐段截断：从末尾向前找最短合法 JSON
        for trim_pos in range(len(candidate), 0, -1):
            sub = candidate[:trim_pos]
            if sub.count("{") != sub.count("}"):
                continue
            try:
                _json.loads(sub)
                return sub
            except _json.JSONDecodeError:
                continue
        return None



        return None

    @staticmethod
    def _fail(message: str, reason: str) -> SupervisorDecision:
        _reasons: dict[str, str] = {
            "user_invoke_error": "我的大脑调用出错了",
            "user_phase_illegal_action": "这一步不能执行这个操作",
            "review_invoke_error": "审核时大脑调用出错了",
            "review_phase_illegal_action": "审核阶段不能执行这个操作",
            "invalid_json": "大脑返回了无法理解的指令",
            "json_decode_error": "大脑指令解析失败",
            "unknown_action": "大脑做出了不认识的决策",
            "missing_message": "大脑没有给出回复内容",
            "missing_task": "大脑没有说明要做什么",
            "dispatch_limit": "连续执行次数太多，先停一下",
            "review_limit": "反复修改次数太多，先停一下",
            "timeout": "用时太长，先停一下",
        }
        chinese = _reasons.get(reason, f"出了点问题（{reason}）")
        spoken = f"哥哥，{chinese}，麻烦你换个说法或者补一点信息。"
        return SupervisorDecision(
            action=SupervisorAction.FAIL,
            message=spoken,
            reason=reason,
        )
