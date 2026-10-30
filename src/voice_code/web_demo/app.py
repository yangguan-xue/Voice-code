"""FastAPI transport layer for the web demo sandbox agent (tenant model).

对外 ASGI 入口（uvicorn 承载）。鉴权模型：
- 登录：POST /api/auth/login 用访问码换 tenantToken（服务端只存哈希，可撤销）
- REST：Authorization: Bearer <tenantToken>（auth.py 依赖注入）
- WS：消息层鉴权（tenant.session.attach 的 sessionToken 字段承载 tenantToken，
  其余 method 同字段定位会话）——浏览器 new WebSocket() 不能自定义请求头

启动方式：
    reasoning-web-demo --sandbox-root ... --access-codes "alice=Alice,bob=Bob"
    # 或 uvicorn 工厂模式（配置从环境变量读）
    uv run uvicorn --factory voice_code.web_demo.app:create_app_from_env \
        --host 127.0.0.1 --port 8787

WebSocket 三路径（同一个端点）：
    /        本地开发前端默认连 ws://127.0.0.1:8787 根路径
    /ws-demo 生产 nginx 把 /ws-demo 原路径反代到后端（proxy_pass 不带 URI 部分）
    /ws      语义化路径，留作未来切换
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field

from voice_code.desktop.protocol import BridgeEvent
from voice_code.llm.models import _load_dotenv
from voice_code.web_demo.auth import require_tenant
from voice_code.web_demo.handlers import (
    allowed_origins as resolve_allowed_origins,
)
from voice_code.web_demo.handlers import (
    cancel_pending_tasks,
    event_json,
    release_attachments,
    respond_to_raw_message,
)
from voice_code.web_demo.history import transcript_to_turns
from voice_code.web_demo.limits import WebDemoLimits
from voice_code.web_demo.protocol import WebDemoEvent
from voice_code.web_demo.runtime import (
    TenantSession,
    TenantSessionManager,
    WebDemoSessionError,
)
from voice_code.web_demo.sandbox import FileTooLargeError
from voice_code.web_demo.tenants import Tenant, TenantStore

# WebDemoSessionError code → HTTP 状态码
_SESSION_ERROR_STATUS = {
    "UNAUTHORIZED": 401,
    "TENANT_DISABLED": 403,
    "RATE_LIMITED": 429,
    "CAPACITY_EXCEEDED": 503,
    "CONVERSATION_NOT_FOUND": 404,
}


class LoginRequest(BaseModel):
    """POST /api/auth/login 的请求体（accessCode / access_code 两种写法都收）。"""

    model_config = ConfigDict(populate_by_name=True)

    access_code: str = Field(alias="accessCode")


class _FastAPIWebSocketSender:
    """WebDemoSender 协议的 FastAPI WebSocket 实现。"""

    def __init__(self, websocket: WebSocket) -> None:
        self._websocket = websocket
        self._lock = asyncio.Lock()

    async def send(self, message: str) -> None:
        # 多个消息处理任务会并发写同一连接，加锁防止帧交错
        async with self._lock:
            await self._websocket.send_text(message)

    async def emit(self, event: BridgeEvent | WebDemoEvent) -> None:
        await self.send(event_json(event))

    @property
    def client_id(self) -> str:
        client = self._websocket.client
        if client is not None and client.host:
            return client.host
        return "anonymous"


def _session_error(exc: WebDemoSessionError) -> HTTPException:
    return HTTPException(
        status_code=_SESSION_ERROR_STATUS.get(exc.code, 400),
        detail=exc.message,
    )

def create_app(
    *,
    session_manager: TenantSessionManager,
    allowed_origins: list[str] | None = None,
) -> FastAPI:
    """构建 FastAPI 应用（工厂模式：测试可造多个实例，uvicorn --factory 延迟创建）。"""
    app = FastAPI(title="voice-code web demo", version="0.3.0")

    # 浏览器跨源调用 REST（如文件上传）需要 CORS；WS 不受 CORS 约束。
    cors_origins = [
        origin
        for origin in resolve_allowed_origins("127.0.0.1", allowed_origins)
        if origin
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )
    app.state.session_manager = session_manager
    app.state.allowed_origins = allowed_origins

    async def ws_endpoint(websocket: WebSocket) -> None:
        origin = websocket.headers.get("origin")
        if origin not in resolve_allowed_origins(websocket.url.hostname or "", allowed_origins):
            await websocket.close(code=4403)
            return
        await websocket.accept()
        sender = _FastAPIWebSocketSender(websocket)
        attached_sessions: set[TenantSession] = set()
        tasks: set[asyncio.Task[None]] = set()
        try:
            while True:
                raw = await websocket.receive_text()
                # 每条消息独立 task：turn.start 的响应要等整个回合结束，
                # 直接 await 会挡住回合进行中发来的 permission.resolve
                task = asyncio.create_task(
                    respond_to_raw_message(manager, raw, sender, attached_sessions)
                )
                tasks.add(task)
                task.add_done_callback(tasks.discard)
        except WebSocketDisconnect:
            pass
        finally:
            await release_attachments(attached_sessions)
            await cancel_pending_tasks(tasks)

    manager = session_manager
    app.add_api_websocket_route("/ws", ws_endpoint)
    app.add_api_websocket_route("/ws-demo", ws_endpoint)
    app.add_api_websocket_route("/", ws_endpoint)

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    # --- 鉴权 ---

    @app.post("/api/auth/login", status_code=201)
    async def login(body: LoginRequest, request: Request):
        client_id = request.client.host if request.client else "anonymous"
        try:
            tenant, raw_token, expires_at = manager.login(
                body.access_code, client_id=client_id
            )
        except WebDemoSessionError as exc:
            raise _session_error(exc) from exc
        return {
            "tenantToken": raw_token,
            "tenantId": tenant.tenant_id,
            "tenantLabel": tenant.label,
            "expiresAt": expires_at.isoformat().replace("+00:00", "Z"),
        }

    @app.post("/api/auth/logout", status_code=204)
    async def logout(request: Request):
        authorization = request.headers.get("authorization", "")
        _scheme, _, token = authorization.partition(" ")
        manager.logout(token.strip())
        return None

    @app.get("/api/auth/me")
    async def auth_me(tenant: Tenant = Depends(require_tenant)):
        return {"tenantId": tenant.tenant_id, "tenantLabel": tenant.label}

    # --- 会话 ---

    @app.get("/api/conversations")
    async def list_conversations(request: Request):
        token = _bearer_from(request)
        try:
            _tenant, conversations, current_id = manager.list_conversations(token)
        except WebDemoSessionError as exc:
            raise _session_error(exc) from exc
        return {
            "conversations": [
                {
                    "conversationId": c.conversation_id,
                    "title": c.title or "新会话",
                    "updatedAt": c.updated_at.isoformat().replace("+00:00", "Z"),
                    "isCurrent": c.conversation_id == current_id,
                }
                for c in conversations
            ],
            "currentConversationId": current_id,
        }

    @app.get("/api/conversations/{conversation_id}/messages")
    async def get_conversation_messages(conversation_id: str, request: Request):
        token = _bearer_from(request)
        try:
            record = manager.get_conversation_record(token, conversation_id)
            transcript_path = manager.resolve_transcript_path(record.tenant_id, record)
        except WebDemoSessionError as exc:
            raise _session_error(exc) from exc
        turns = transcript_to_turns(
            transcript_path,
            sandbox_path=manager.tenant_workspace_path(record.tenant_id),
            max_tool_chars=manager._limits.max_history_tool_chars,
        )
        return {"conversationId": conversation_id, "turns": turns}

    @app.post("/api/conversations/new", status_code=201)
    async def new_conversation(request: Request):
        token = _bearer_from(request)
        try:
            session = await manager.start_new_conversation(token)
        except WebDemoSessionError as exc:
            raise _session_error(exc) from exc
        return session.to_payload()

    @app.post("/api/conversations/{conversation_id}/activate")
    async def activate_conversation(conversation_id: str, request: Request):
        token = _bearer_from(request)
        try:
            session = await manager.switch_conversation(token, conversation_id)
        except WebDemoSessionError as exc:
            raise _session_error(exc) from exc
        return session.to_payload()

    @app.post("/api/conversations/current/files", status_code=201)
    async def upload_file(
        request: Request,
        path: str = Query(..., min_length=1),
        tenant: Tenant = Depends(require_tenant),
    ):
        """上传文件到租户当前会话的沙盒（裸字节流，免 python-multipart 依赖）。"""
        token = _bearer_from(request)
        try:
            session = await manager.get_session(token)
        except WebDemoSessionError as exc:
            raise _session_error(exc) from exc
        data = await request.body()
        try:
            written = await manager.write_file(session, path, data)
        except FileTooLargeError as exc:
            raise HTTPException(
                status_code=413, detail="Uploaded file is too large."
            ) from exc
        except (ValueError, WebDemoSessionError) as exc:
            detail = exc.message if isinstance(exc, WebDemoSessionError) else str(exc)
            status = 413 if isinstance(exc, WebDemoSessionError) else 400
            raise HTTPException(status_code=status, detail=detail) from exc
        return {"path": written, "size": len(data), "tenantId": tenant.tenant_id}

    def _bearer_from(request: Request) -> str:
        authorization = request.headers.get("authorization", "")
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise HTTPException(status_code=401, detail="Missing bearer token.")
        return token.strip()

    return app


def create_app_from_env() -> FastAPI:
    """从环境变量构建应用（uvicorn --factory 的入口，与 systemd 部署对接）。

    环境变量（对照 web_demo/cli.py 的命令行参数，一一对应）：
        REASONING_WEB_DEMO_ACCESS_CODES     租户访问码 csv "alice=Alice,bob=Bob"（必填）
        REASONING_WEB_DEMO_SANDBOX_ROOT     沙箱根目录（必填）
        REASONING_WEB_DEMO_DATA_ROOT        数据根目录（默认 <sandbox-root>/_data）
        REASONING_WEB_DEMO_TEMPLATE_REPO    模板仓库路径（可选）
        REASONING_WEB_DEMO_PROFILE          profile（可选）
        REASONING_WEB_DEMO_MODEL            模型名（可选）
        REASONING_WEB_DEMO_IDLE_TIMEOUT_SECONDS  热 runtime 空闲回收阈值，默认 1800
        REASONING_WEB_DEMO_MAX_RUNTIMES     最大活跃 runtime 数，默认 20
        REASONING_WEB_DEMO_ALLOWED_ORIGINS  额外允许的 Origin，逗号分隔（可选）
    """
    _load_dotenv()
    access_codes = os.getenv("REASONING_WEB_DEMO_ACCESS_CODES", "")
    if not access_codes:
        raise RuntimeError("REASONING_WEB_DEMO_ACCESS_CODES is required")
    sandbox_root = os.getenv("REASONING_WEB_DEMO_SANDBOX_ROOT", "")
    if not sandbox_root:
        raise RuntimeError("REASONING_WEB_DEMO_SANDBOX_ROOT is required")

    data_root = os.getenv("REASONING_WEB_DEMO_DATA_ROOT", "") or str(
        Path(sandbox_root) / "_data"
    )
    template_repo = os.getenv("REASONING_WEB_DEMO_TEMPLATE_REPO", "")
    store = TenantStore.from_access_codes_csv(Path(data_root) / "web-demo.db", access_codes)
    manager = TenantSessionManager(
        tenant_store=store,
        sandbox_root=Path(sandbox_root),
        data_root=Path(data_root),
        template_repo=Path(template_repo) if template_repo else None,
        profile=os.getenv("REASONING_WEB_DEMO_PROFILE", "") or None,
        model_name=os.getenv("REASONING_WEB_DEMO_MODEL", "") or None,
        limits=WebDemoLimits(
            runtime_idle_timeout_seconds=float(
                os.getenv("REASONING_WEB_DEMO_IDLE_TIMEOUT_SECONDS", "1800")
            ),
            max_active_runtimes=int(os.getenv("REASONING_WEB_DEMO_MAX_RUNTIMES", "20")),
        ),
    )

    return create_app(session_manager=manager)
