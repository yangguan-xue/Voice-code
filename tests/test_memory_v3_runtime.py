from __future__ import annotations

import asyncio
import logging
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessageChunk

from voice_code.agent.loop import agent_loop
from voice_code.memory.candidates import ExtractionMode
from voice_code.memory.rag_service import MemoryRagService
from voice_code.telemetry.context import bind_telemetry_context


async def _collect(generator):
    return [event async for event in generator]


def _model_with_chunks(chunks):
    async def fake_astream(*_args, **_kwargs):
        for chunk in chunks:
            yield chunk

    model = MagicMock()
    model.astream = fake_astream
    return model


class FakeMemoryService:
    def __init__(self) -> None:
        self.enqueued: list[dict] = []

    async def get_memory_messages(self, *_args, **_kwargs):
        return []

    def enqueue_completed_turn(self, **kwargs):
        self.enqueued.append(kwargs)
        return True


@pytest.mark.asyncio
async def test_completed_main_turn_enqueues_extraction_once():
    memory = FakeMemoryService()
    events = await _collect(
        agent_loop(
            user_input="以后回答先给结论",
            tools=[],
            system_prompt="helpful",
            model=_model_with_chunks([AIMessageChunk(content="明白。")]),
            memory_service=memory,
            memory_user_id="local",
            memory_project_key="project-a",
            runtime_session_id="session-1",
        )
    )

    assert events[-1].finish_reason == "completed"
    assert len(memory.enqueued) == 1
    enqueued = memory.enqueued[0]
    assert enqueued.pop("turn_id").startswith("turn-1-")
    assert enqueued == {
        "user_input": "以后回答先给结论",
        "assistant_response": "明白。",
        "user_id": "local",
        "project_key": "project-a",
        "session_id": "session-1",
    }


@pytest.mark.asyncio
async def test_separate_tasks_in_same_session_get_distinct_extraction_turn_ids():
    memory = FakeMemoryService()
    for text in ("第一个任务", "第二个任务"):
        await _collect(
            agent_loop(
                user_input=text,
                tools=[],
                system_prompt="helpful",
                model=_model_with_chunks([AIMessageChunk(content="完成。")]),
                memory_service=memory,
                runtime_session_id="session-1",
            )
        )

    assert memory.enqueued[0]["turn_id"] != memory.enqueued[1]["turn_id"]


@pytest.mark.asyncio
async def test_error_turn_does_not_enqueue_extraction():
    memory = FakeMemoryService()
    events = await _collect(
        agent_loop(
            user_input="记住这个",
            tools=[],
            system_prompt="helpful",
            model=_model_with_chunks([]),
            memory_service=memory,
            runtime_session_id="session-1",
        )
    )

    assert events[-1].finish_reason == "error"
    assert memory.enqueued == []


@pytest.mark.asyncio
async def test_background_worker_failure_is_consumed_and_logged(caplog):
    class CrashingWorker:
        async def run_once(self, *, limit):
            raise RuntimeError("simulated worker failure")

    repository = MagicMock()
    repository.enqueue_extraction_job.return_value = True
    service = MemoryRagService(
        repository,
        extraction_mode=ExtractionMode.SHADOW,
        extraction_worker=CrashingWorker(),
    )

    with caplog.at_level(logging.WARNING):
        service.enqueue_completed_turn(
            user_input="以后回答简洁",
            assistant_response="明白",
            user_id="local",
            project_key=None,
            session_id="session-1",
            turn_id="turn-1",
        )
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    assert not service._extraction_tasks
    assert "memory.extraction.task_failed" in caplog.messages


@pytest.mark.asyncio
async def test_background_worker_uses_supervisor_metadata():
    class Worker:
        async def run_once(self, *, limit):
            return limit

    repository = MagicMock()
    repository.enqueue_extraction_job.return_value = True
    service = MemoryRagService(
        repository,
        extraction_mode=ExtractionMode.SHADOW,
        extraction_worker=Worker(),
    )

    with bind_telemetry_context(correlation_id="corr-rag-test"):
        created = service.enqueue_completed_turn(
            user_input="以后回答简洁",
            assistant_response="明白",
            user_id="local",
            project_key=None,
            session_id="session-1",
            turn_id="turn-1",
        )

    assert created is True
    assert service._task_supervisor.get_task("rag-extraction-turn-1") is not None
    await service.shutdown_background_tasks()
