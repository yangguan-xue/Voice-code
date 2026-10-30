"""Bearer token auth dependencies for the web demo REST API.

WS 通道在消息层鉴权（浏览器 WS 不能自定义头）；REST 通道统一走
Authorization: Bearer <tenantToken>。
"""

from __future__ import annotations

from fastapi import Depends, Header, HTTPException, Request

from voice_code.web_demo.runtime import TenantSessionManager, WebDemoSessionError
from voice_code.web_demo.tenants import Tenant


def bearer_token(authorization: str = Header(...)) -> str:
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="Missing bearer token.")
    return token.strip()


async def require_tenant(
    request: Request,
    token: str = Depends(bearer_token),
) -> Tenant:
    """Bearer token → Tenant。无效 401，禁用 403。"""
    manager: TenantSessionManager = request.app.state.session_manager
    try:
        return manager.verify_token(token)
    except WebDemoSessionError as exc:
        status = 403 if exc.code == "TENANT_DISABLED" else 401
        raise HTTPException(status_code=status, detail=exc.message) from exc
