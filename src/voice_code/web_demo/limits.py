"""Resource limit defaults for the web demo (tenant sessions).

会话不再过期：30 分钟从"会话死刑"变成热 runtime 的空闲回收阈值
（transcript 与 sandbox 永久保留，回收后下条消息冷启动重建，用户无感知）。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WebDemoLimits:
    # 热 runtime 空闲回收阈值（不是会话过期）；tenant token 有效期见 tenants.py
    runtime_idle_timeout_seconds: float = 30 * 60
    runtime_recycle_interval_seconds: float = 30.0
    max_active_runtimes: int = 20
    turn_timeout_seconds: int = 5 * 60
    permission_timeout_seconds: float = 300.0
    max_patch_chars: int = 50_000
    max_session_creates_per_minute: int = 10
    # 持久化后每租户长期占用，配额上调（评审决议 64MB → 256MB）
    max_workspace_bytes: int = 256 * 1024 * 1024
    max_workspace_files: int = 200
    max_file_bytes: int = 256 * 1024
    max_download_bytes: int = 12 * 1024 * 1024
    max_upload_bytes: int = 8 * 1024 * 1024
    # 历史回放单条 tool 结果的最大字符数（防撑爆前端）
    max_history_tool_chars: int = 4_000
