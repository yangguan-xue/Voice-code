"""桌面端子 agent 后台任务生命周期回归测试。"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from voice_code.permissions import PermissionContext
from voice_code.subagents.planner import AgentToolRequest
from voice_code.subagents.runtime import SubagentRunResult
from voice_code.subagents.service import (
    _SERVICES,
    RuntimeInvocationContext,
    SubagentService,
    activate_runtime_context,
    get_or_create_service,
)
from voice_code.subagents.types import TaskStatus


@pytest.fixture(autouse=True)
def _clear_services():
    _SERVICES.clear()
    yield
    _SERVICES.clear()


class _LoopThread:
    """在独立线程里运行一个长生命周期事件循环，模拟桌面桥接的持久 loop。"""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._started = threading.Event()

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self._started.set()
        self.loop.run_forever()

    def __enter__(self) -> asyncio.AbstractEventLoop:
        self._thread.start()
        self._started.wait(timeout=5)
        return self.loop

    def __exit__(self, *exc) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(timeout=5)
        self.loop.close()


def test_bind_event_loop_does_not_steal_running_loop(tmp_path):
    with _LoopThread() as persistent:
        service = SubagentService(
            session_id="s1",
            event_loop=persistent,
            transcript_root=tmp_path,
        )
        with _LoopThread() as other:
            service.bind_event_loop(other)
            assert service._event_loop is persistent
        # other 已关闭，持久 loop 仍在运行时不换绑
        assert service._event_loop is persistent

    # 持久 loop 关闭后允许换绑
    stale = service._event_loop
    assert stale.is_closed()
    fresh = asyncio.new_event_loop()
    try:
        service.bind_event_loop(fresh)
        assert service._event_loop is fresh
    finally:
        fresh.close()


def test_background_subagent_survives_worker_loop_close(tmp_path):
    """模拟桌面端：turn 在 worker 线程 asyncio.run() 里结束并关闭临时 loop，
    后台子 agent 必须继续跑在持久 loop 上并产生完成通知。"""
    started = threading.Event()
    release = threading.Event()

    with _LoopThread() as persistent:
        service = get_or_create_service(
            "sess-bg",
            event_loop=persistent,
            transcript_root=tmp_path,
        )

        async def fake_run_single(request):
            started.set()
            await asyncio.get_running_loop().run_in_executor(None, release.wait)
            result = SubagentRunResult(
                task_id=request.task_id,
                status=TaskStatus.COMPLETED,
                summary="分析完成",
                transcript_path="",
            )
            service._record_notification(result, request.agent_type)
            return result

        service._run_single = fake_run_single  # type: ignore[method-assign]

        context = RuntimeInvocationContext(
            session_id="sess-bg",
            system_prompt="sys",
            model=None,
            fallback_model=None,
            permission_context=PermissionContext(),
            tools=[],
            event_loop=persistent,
            service=service,
        )

        def worker_turn() -> str:
            # 模拟桌面 runner：每轮 turn 一个临时 loop
            async def _turn() -> str:
                # agent_loop 每轮都会用当前 loop 调一次 get_or_create_service
                get_or_create_service("sess-bg", event_loop=asyncio.get_running_loop())

                # 真实路径里工具执行在独立线程，不在 loop 线程上
                def _invoke() -> str:
                    with activate_runtime_context(context):
                        return service.invoke_agent_tool(
                            AgentToolRequest(description="分析模块", prompt="读代码并总结")
                        )

                return await asyncio.get_running_loop().run_in_executor(None, _invoke)

            return asyncio.run(_turn())

        # turn 1：启动后台子 agent，随后 worker loop 关闭
        result_holder: list[str] = []
        worker = threading.Thread(target=lambda: result_holder.append(worker_turn()))
        worker.start()
        worker.join(timeout=10)

        assert result_holder and "background" in result_holder[0]
        assert started.wait(timeout=5), "后台任务未启动"

        # 关键断言：worker loop 已销毁，但后台任务仍然存活
        assert service._background_tasks, "后台任务被 worker loop 关闭时杀掉了"

        # 放行任务，等待完成通知
        release.set()
        deadline = time.time() + 5
        while time.time() < deadline and not service.export_notifications_state():
            time.sleep(0.05)

        notifications = service.drain_notifications_as_messages()
        assert len(notifications) == 1
        assert "completed" in notifications[0].content
        assert "分析完成" in notifications[0].content
