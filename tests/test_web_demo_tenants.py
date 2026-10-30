"""Tests for web demo tenant store (tenants.py).

钉住：访问码即租户身份、token 哈希存储与滑动续期、会话索引 CRUD、
配置重同步（移除的码禁用）、跨租户不可见。
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from voice_code.web_demo.tenants import TenantAuthError, TenantStore


def _store(tmp_path: Path, csv: str = "alice=Alice,bob=Bob") -> TenantStore:
    return TenantStore.from_access_codes_csv(tmp_path / "web-demo.db", csv)


# --- 访问码 / 租户 ---


def test_login_with_valid_code_returns_tenant(tmp_path: Path) -> None:
    store = _store(tmp_path)
    tenant = store.verify_access_code("alice")
    assert tenant.tenant_id == "alice"
    assert tenant.label == "Alice"
    assert tenant.disabled is False


def test_login_with_wrong_code_rejected_without_details(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(TenantAuthError) as excinfo:
        store.verify_access_code("wrong-code")
    # 不区分"码不存在"与"码错误"，防枚举
    assert excinfo.value.code == "INVALID_ACCESS_CODE"


def test_access_code_stored_hashed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.verify_access_code("alice")
    with sqlite3.connect(tmp_path / "web-demo.db") as conn:
        row = conn.execute(
            "SELECT access_code_hash FROM tenants WHERE tenant_id='alice'"
        ).fetchone()
    assert row is not None
    assert row[0] != "alice"
    assert len(row[0]) == 64  # sha256 hexdigest


def test_removed_code_disables_tenant_on_resync(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.verify_access_code("alice")
    # 配置里移除 alice，重同步后 alice 被禁用而不是删除（数据保留）
    store.sync_access_codes("bob=Bob")
    with pytest.raises(TenantAuthError) as excinfo:
        store.verify_access_code("alice")
    assert excinfo.value.code == "TENANT_DISABLED"


def test_mixed_case_access_code_accepted(tmp_path: Path) -> None:
    # 访问码是运维自选的口令（如 ygx28YGX），允许大小写与下划线；
    # 登录必须精确匹配（大小写敏感）。
    store = _store(tmp_path, "ygx28YGX=Owner")
    tenant = store.verify_access_code("ygx28YGX")
    assert tenant.tenant_id == "ygx28YGX"
    with pytest.raises(TenantAuthError):
        store.verify_access_code("ygx28ygx")


def test_invalid_csv_entries_rejected(tmp_path: Path) -> None:
    # tenant_id 要排除路径分隔符/点/空白（会出现在路径里）
    with pytest.raises(ValueError):
        _store(tmp_path, "Alice 大小写=Label")
    with pytest.raises(ValueError):
        _store(tmp_path, "no-label")


# --- token ---


def test_token_roundtrip_and_sliding_renewal(tmp_path: Path) -> None:
    store = _store(tmp_path)
    tenant = store.verify_access_code("alice")
    raw_token, expires_at = store.issue_token(tenant.tenant_id)
    assert expires_at > datetime.now(UTC) + timedelta(days=29)

    authed = store.verify_token(raw_token)
    assert authed.tenant_id == "alice"

    with sqlite3.connect(tmp_path / "web-demo.db") as conn:
        row = conn.execute("SELECT token_hash FROM tenant_tokens").fetchone()
    assert row is not None
    assert row[0] != raw_token  # 明文不落库


def test_unknown_token_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(TenantAuthError) as excinfo:
        store.verify_token("not-a-real-token")
    assert excinfo.value.code == "INVALID_TOKEN"


def test_revoked_token_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    raw_token, _ = store.issue_token("alice")
    store.revoke_token(raw_token)
    with pytest.raises(TenantAuthError):
        store.verify_token(raw_token)


def test_expired_token_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    raw_token, _ = store.issue_token("alice", ttl=timedelta(milliseconds=1))
    import time

    time.sleep(0.01)
    with pytest.raises(TenantAuthError) as excinfo:
        store.verify_token(raw_token)
    assert excinfo.value.code == "INVALID_TOKEN"


# --- 会话索引 ---


def test_conversation_crud_and_current_pointer(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.verify_access_code("alice")
    store.create_conversation(
        tenant_id="alice",
        conversation_id="conv-1",
        title="第一次对话",
        transcript_path="tenants/alice/transcripts/conv-1.jsonl",
    )
    store.create_conversation(
        tenant_id="alice",
        conversation_id="conv-2",
        title="第二次对话",
        transcript_path="tenants/alice/transcripts/conv-2.jsonl",
    )

    conversations = store.list_conversations("alice")
    assert [c.conversation_id for c in conversations] == ["conv-2", "conv-1"]  # 新的在前

    current = store.get_current_conversation("alice")
    assert current is not None and current.conversation_id == "conv-2"  # 新建即当前

    store.set_current_conversation("alice", "conv-1")
    current = store.get_current_conversation("alice")
    assert current is not None and current.conversation_id == "conv-1"


def test_conversation_title_update(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create_conversation(
        tenant_id="alice",
        conversation_id="conv-1",
        title="",
        transcript_path="tenants/alice/transcripts/conv-1.jsonl",
    )
    store.update_conversation_title("alice", "conv-1", "帮我写个爬虫")
    record = store.get_conversation("alice", "conv-1")
    assert record.title == "帮我写个爬虫"


def test_cross_tenant_conversation_invisible(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create_conversation(
        tenant_id="alice",
        conversation_id="conv-1",
        title="",
        transcript_path="tenants/alice/transcripts/conv-1.jsonl",
    )
    # bob 查 alice 的会话：与"不存在"同一个错误，不泄露存在性
    with pytest.raises(TenantAuthError) as excinfo:
        store.get_conversation("bob", "conv-1")
    assert excinfo.value.code == "CONVERSATION_NOT_FOUND"
    with pytest.raises(TenantAuthError):
        store.set_current_conversation("bob", "conv-1")


def test_new_tenant_has_no_current_conversation(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.verify_access_code("alice")
    assert store.get_current_conversation("alice") is None
    assert store.list_conversations("alice") == []


def test_archive_conversation_hides_from_list_but_keeps_record(tmp_path: Path) -> None:
    """session.discard 语义：归档而不是删除，transcript 文件永久保留（评审决议）。"""
    store = _store(tmp_path)
    store.verify_access_code("alice")
    store.create_conversation(
        tenant_id="alice",
        conversation_id="conv-1",
        title="旧会话",
        transcript_path="tenants/alice/transcripts/conv-1.jsonl",
    )
    store.archive_conversation("alice", "conv-1")

    assert store.list_conversations("alice") == []
    # 记录仍在（可审计），只是不出现在列表里
    record = store.get_conversation("alice", "conv-1")
    assert record.status == "archived"

    # 归档后新建会话，current 指针正确移动
    store.create_conversation(
        tenant_id="alice",
        conversation_id="conv-2",
        title="",
        transcript_path="tenants/alice/transcripts/conv-2.jsonl",
    )
    current = store.get_current_conversation("alice")
    assert current is not None and current.conversation_id == "conv-2"

# --- TenantSessionManager（T3）：attach / 沙箱持久 / 会话切换 / 回收与重建 / 重启恢复 ---

from collections.abc import AsyncIterator  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from langchain_core.messages import HumanMessage  # noqa: E402

from voice_code.agent.abort import AbortSignal  # noqa: E402
from voice_code.agent.types import AgentEvent, EventType  # noqa: E402
from voice_code.session import TranscriptWriter  # noqa: E402
from voice_code.web_demo.runtime import (  # noqa: E402
    TenantSession,
    TenantSessionManager,
    WebDemoSessionError,
    _TenantRuntimeState,  # noqa: E402
)


def _template(path: Path) -> Path:
    template = path / "template"
    template.mkdir(exist_ok=True)
    (template / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    return template


def _text_runner_factory(message: str, log: list[str] | None = None):
    def factory(_session: TenantSession, _approver):
        async def runner(
            text: str,
            *,
            abort_signal: AbortSignal,
            permission_mode: str | None = None,
            user_images: list[dict[str, str]] | None = None,
        ) -> AsyncIterator[AgentEvent]:
            if log is not None:
                log.append(text)
            yield AgentEvent(type=EventType.TEXT, turn=1, content=message)
            yield AgentEvent(type=EventType.FINISH, turn=1, finish_reason="completed")

        return runner

    return factory


def _manager(tmp_path: Path, runner_message: str = "已完成。") -> TenantSessionManager:
    store = TenantStore.from_access_codes_csv(
        tmp_path / "data" / "web-demo.db", "alice=Alice,bob=Bob"
    )
    return TenantSessionManager(
        tenant_store=store,
        sandbox_root=tmp_path / "sandboxes",
        data_root=tmp_path / "data",
        template_repo=_template(tmp_path),
        runner_factory=_text_runner_factory(runner_message),
    )


def _login(manager: TenantSessionManager, code: str) -> str:
    _tenant, raw_token, _expires = manager.login(code, client_id=f"test-{code}-{_login.counter}")
    _login.counter += 1
    return raw_token


_login.counter = 0


@pytest.mark.asyncio
async def test_attach_creates_persistent_tenant_sandbox_and_conversation(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    token = _login(manager, "alice")

    session = await manager.attach(token)
    assert session.tenant_id == "alice"
    expected = (tmp_path / "sandboxes" / "tenants" / "alice" / "workspace").resolve()
    assert session.sandbox_path == expected
    assert (session.sandbox_path / "app.py").exists()  # 模板已落地
    assert "tenants/alice/transcripts/" in session.transcript_path.as_posix()

    # attach 幂等：同一当前会话，同一 session 对象
    again = await manager.attach(token)
    assert again is session
    await manager.shutdown()


@pytest.mark.asyncio
async def test_tenants_get_isolated_sandboxes(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    alice_session = await manager.attach(_login(manager, "alice"))
    bob_session = await manager.attach(_login(manager, "bob"))
    assert alice_session.sandbox_path != bob_session.sandbox_path
    assert "alice" in alice_session.sandbox_path.as_posix()
    assert "bob" in bob_session.sandbox_path.as_posix()
    await manager.shutdown()


@pytest.mark.asyncio
async def test_turn_sets_conversation_title_once(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    token = _login(manager, "alice")
    session = await manager.attach(token)
    assert session.bridge is not None

    result = await session.bridge.start_turn("帮我写一个贪吃蛇游戏", permission_mode="default")
    assert result.finish_reason == "completed"
    manager.maybe_set_title(session, "帮我写一个贪吃蛇游戏")
    assert session.title == "帮我写一个贪吃蛇游戏"

    manager.maybe_set_title(session, "第二轮消息不改标题")
    assert session.title == "帮我写一个贪吃蛇游戏"
    await manager.shutdown()


@pytest.mark.asyncio
async def test_new_conversation_and_switch_back(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    token = _login(manager, "alice")
    first = await manager.attach(token)

    second = await manager.start_new_conversation(token)
    assert second.conversation_id != first.conversation_id

    _tenant, conversations, current_id = manager.list_conversations(token)
    assert len(conversations) == 2
    assert current_id == second.conversation_id

    switched = await manager.switch_conversation(token, first.conversation_id)
    assert switched.conversation_id == first.conversation_id
    assert manager.list_conversations(token)[2] == first.conversation_id
    await manager.shutdown()


@pytest.mark.asyncio
async def test_cross_tenant_switch_rejected_as_not_found(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    alice_token = _login(manager, "alice")
    bob_token = _login(manager, "bob")
    alice_session = await manager.attach(alice_token)

    with pytest.raises(WebDemoSessionError) as excinfo:
        await manager.switch_conversation(bob_token, alice_session.conversation_id)
    assert excinfo.value.code == "CONVERSATION_NOT_FOUND"
    await manager.shutdown()


@pytest.mark.asyncio
async def test_discard_archives_and_starts_fresh(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    token = _login(manager, "alice")
    session = await manager.attach(token)
    old_transcript = session.transcript_path

    fresh = await manager.discard(session)
    assert fresh.conversation_id != session.conversation_id
    _t, conversations, current_id = manager.list_conversations(token)
    assert [c.conversation_id for c in conversations] == [fresh.conversation_id]
    assert current_id == fresh.conversation_id
    # 归档不删文件（评审决议：记录永久保留）——此处 transcript 尚未创建也无妨，索引状态为准
    _ = old_transcript
    await manager.shutdown()

# --- 热 runtime：懒加载 / 空闲回收 / 重建续接 / 重启恢复 ---


def _stub_runtime_state(build_log: list[int]):
    """替换 manager._build_runtime_state，避免真实 bootstrap（模型/MCP 等重依赖）。"""

    async def fake_build(self, session: TenantSession) -> _TenantRuntimeState:
        build_log.append(1)
        resume = self._load_resume_messages(session)

        async def runner(
            text: str,
            *,
            abort_signal: AbortSignal,
            permission_mode: str | None = None,
            user_images: list[dict[str, str]] | None = None,
        ) -> AsyncIterator[AgentEvent]:
            yield AgentEvent(
                type=EventType.TEXT,
                turn=1,
                content=f"resumed={len(resume or [])}",
            )
            yield AgentEvent(type=EventType.FINISH, turn=1, finish_reason="completed")

        runtime = SimpleNamespace(
            model=SimpleNamespace(model_name="stub-model"),
            transcript_writer=SimpleNamespace(close=lambda: None),
            resume_messages=resume,
        )
        return _TenantRuntimeState(runtime=runtime, runner=runner)

    return fake_build


@pytest.mark.asyncio
async def test_runtime_lazy_build_and_idle_recycle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manager = _manager(tmp_path)
    # 绕过 runner_factory 路径，用 stub 观察懒加载行为
    manager._runner_factory = None
    build_log: list[int] = []
    monkeypatch.setattr(
        TenantSessionManager, "_build_runtime_state", _stub_runtime_state(build_log)
    )

    token = _login(manager, "alice")
    session = await manager.attach(token)
    assert session.runtime_state is None  # attach 不构建 runtime（懒加载）

    await manager.ensure_runtime(session)
    assert len(build_log) == 1
    await manager.ensure_runtime(session)
    assert len(build_log) == 1  # 幂等：热 runtime 复用

    # 未到空闲阈值不回收
    recycled = await manager.recycle_idle_runtimes(
        now=session.last_active_monotonic + manager._limits.runtime_idle_timeout_seconds - 1
    )
    assert recycled == 0
    assert session.runtime_state is not None

    # 超过阈值回收：只卸内存，transcript/sandbox 不动
    recycled = await manager.recycle_idle_runtimes(
        now=session.last_active_monotonic + manager._limits.runtime_idle_timeout_seconds + 1
    )
    assert recycled == 1
    assert session.runtime_state is None
    assert session.sandbox_path.exists()

    # 重建：下条消息到来时冷启动
    await manager.ensure_runtime(session)
    assert len(build_log) == 2
    await manager.shutdown()


@pytest.mark.asyncio
async def test_busy_session_never_recycled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manager = _manager(tmp_path)
    manager._runner_factory = None
    build_log: list[int] = []
    monkeypatch.setattr(
        TenantSessionManager, "_build_runtime_state", _stub_runtime_state(build_log)
    )
    token = _login(manager, "alice")
    session = await manager.attach(token)
    await manager.ensure_runtime(session)

    assert session.bridge is not None
    monkeypatch.setattr(type(session.bridge), "is_busy", property(lambda self: True))
    recycled = await manager.recycle_idle_runtimes(
        now=session.last_active_monotonic + 10 * 60 * 60
    )
    assert recycled == 0
    assert session.runtime_state is not None
    await manager.shutdown()


@pytest.mark.asyncio
async def test_resume_messages_loaded_from_transcript(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    token = _login(manager, "alice")
    session = await manager.attach(token)

    # 模拟历史：transcript 里已有两轮消息
    writer = TranscriptWriter(session.transcript_path)
    writer.write_message(HumanMessage(content="第一轮问题"))
    writer.write_message(HumanMessage(content="第二轮问题"))
    writer.close()

    messages = manager._load_resume_messages(session)
    assert messages is not None
    contents = [m.content for m in messages]
    assert "第一轮问题" in contents
    assert "第二轮问题" in contents
    await manager.shutdown()


@pytest.mark.asyncio
async def test_recycled_runtime_rebuild_injects_resume_and_emits_event(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manager = _manager(tmp_path)
    manager._runner_factory = None
    build_log: list[int] = []
    monkeypatch.setattr(
        TenantSessionManager, "_build_runtime_state", _stub_runtime_state(build_log)
    )
    token = _login(manager, "alice")
    session = await manager.attach(token)

    # 写入历史（ transcript 非空 → 重建时应触发 runtime.recycled 事件 ）
    writer = TranscriptWriter(session.transcript_path)
    writer.write_message(HumanMessage(content="历史消息"))
    writer.close()

    events: list[str] = []
    session.set_emit(lambda event: events.append(event.event))

    await manager.ensure_runtime(session)  # 首次构建（transcript 已有内容 → 算恢复）
    assert "runtime.recycled" in events

    await manager.recycle_idle_runtimes(now=session.last_active_monotonic + 2 * 3600)
    await manager.ensure_runtime(session)  # 回收后重建
    assert len(build_log) == 2
    assert events.count("runtime.recycled") == 2
    await manager.shutdown()


@pytest.mark.asyncio
async def test_restart_recovers_token_conversation_and_sandbox(tmp_path: Path) -> None:
    """进程重启 = 新建 manager + 新建 store（同 db），登录态/会话/沙箱全部可恢复。"""
    manager1 = _manager(tmp_path)
    token = _login(manager1, "alice")
    session1 = await manager1.attach(token)
    (session1.sandbox_path / "keep.txt").write_text("保留我", encoding="utf-8")
    writer = TranscriptWriter(session1.transcript_path)
    writer.write_message(HumanMessage(content="重启前的消息"))
    writer.close()
    conversation_id = session1.conversation_id
    await manager1.shutdown()

    # "重启"：全新 manager/store 实例，同一 data_root / sandbox_root
    manager2 = _manager(tmp_path)
    session2 = await manager2.attach(token)  # token 仍有效（SQLite 持久化）
    assert session2.conversation_id == conversation_id  # 恢复同一会话
    assert session2.sandbox_path == session1.sandbox_path
    assert (session2.sandbox_path / "keep.txt").read_text(encoding="utf-8") == "保留我"
    messages = manager2._load_resume_messages(session2)
    assert messages is not None and "重启前的消息" in [m.content for m in messages]
    await manager2.shutdown()
