"""Tenant registry, token issuance, and conversation index for the web demo.

设计要点（对照 docs/specs/web-demo-tenant-sessions-spec.md）：
- 访问码即租户身份：配置 csv（"alice=Alice,bob=Bob"）在启动时同步进 SQLite，
  key 同时是 tenant_id 与用户输入的访问码；被移除的码标记 disabled 而不是删除，
  租户的 transcript / sandbox 数据永久保留。
- token 不透明、可撤销：明文只在登录响应里出现一次，库里只存 SHA-256 哈希；
  校验通过即滑动续期（默认 30 天）。
- 消息正文不落库，只存会话索引（transcript_path 指向 JSONL 文件），单写不双写。
- 跨租户查询与"不存在"返回同一个错误码，不泄露资源存在性。

并发模型：uvicorn 单进程多协程 + 少量同步阻塞调用；sqlite3 单连接加锁串行化，
几个受邀用户的量级下完全够用，避免引入额外依赖。
"""

from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

DEFAULT_TOKEN_TTL = timedelta(days=30)

# tenant_id 会出现在磁盘路径里，必须文件系统安全
# Access codes double as tenant ids; they are operator-chosen secrets, so allow
# mixed case and underscores. Path safety only requires excluding separators,
# dots, and whitespace.
_TENANT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tenants (
    tenant_id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    access_code_hash TEXT NOT NULL,
    disabled INTEGER NOT NULL DEFAULT 0,
    current_conversation_id TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tenant_tokens (
    token_hash TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    last_used_at TEXT NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS conversations (
    conversation_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
    title TEXT NOT NULL DEFAULT '',
    transcript_path TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, conversation_id)
);
"""


class TenantAuthError(RuntimeError):
    """结构化鉴权错误，code 供传输层映射状态码。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class Tenant:
    tenant_id: str
    label: str
    disabled: bool
    created_at: datetime
    current_conversation_id: str | None


@dataclass(frozen=True, slots=True)
class ConversationRecord:
    conversation_id: str
    tenant_id: str
    title: str
    transcript_path: str
    status: str
    created_at: datetime
    updated_at: datetime


def _hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


def _to_iso(value: datetime) -> str:
    return value.isoformat()


def _from_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


def parse_access_codes_csv(csv: str) -> dict[str, str]:
    """解析 "alice=Alice,bob=Bob" → {tenant_id: label}。

    key 同时是 tenant_id 和访问码（评审决议：显式指定、便于日志阅读）。
    """
    entries: dict[str, str] = {}
    for chunk in csv.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "=" not in chunk:
            raise ValueError(f"Invalid access code entry (expected code=Label): {chunk!r}")
        tenant_id, label = chunk.split("=", 1)
        tenant_id = tenant_id.strip()
        label = label.strip()
        if not _TENANT_ID_RE.match(tenant_id):
            raise ValueError(
                f"Invalid tenant id {tenant_id!r}: letters, digits, dashes, underscores only."
            )
        if not label:
            raise ValueError(f"Missing label for tenant {tenant_id!r}.")
        entries[tenant_id] = label
    if not entries:
        raise ValueError("Access codes csv must define at least one tenant.")
    return entries


class TenantStore:
    """SQLite-backed tenant/token/conversation store."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False：uvicorn 的 handler 可能跑在不同线程，加锁串行化
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    @classmethod
    def from_access_codes_csv(cls, db_path: str | Path, csv: str) -> TenantStore:
        store = cls(db_path)
        store.sync_access_codes(csv)
        return store

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # --- 配置同步 ---

    def sync_access_codes(self, csv: str) -> None:
        """把配置 csv 同步为租户表：新增/更新码；不在配置里的租户标记 disabled。

        访问码本身即 tenant_id，轮换某个租户的码等于换 id——owner 量级下可接受，
        换来的是零管理界面（改 .env 重启即生效）。
        """
        entries = parse_access_codes_csv(csv)
        with self._lock:
            for tenant_id, label in entries.items():
                code_hash = _hash_secret(tenant_id)
                self._conn.execute(
                    """
                    INSERT INTO tenants (tenant_id, label, access_code_hash, disabled, created_at)
                    VALUES (?, ?, ?, 0, ?)
                    ON CONFLICT(tenant_id) DO UPDATE SET
                        label = excluded.label,
                        access_code_hash = excluded.access_code_hash,
                        disabled = 0
                    """,
                    (tenant_id, label, code_hash, _to_iso(_now())),
                )
            if entries:
                placeholders = ",".join("?" for _ in entries)
                self._conn.execute(
                    f"UPDATE tenants SET disabled = 1 WHERE tenant_id NOT IN ({placeholders})",
                    tuple(entries),
                )
            self._conn.commit()

    # --- 访问码登录 ---

    def verify_access_code(self, access_code: str) -> Tenant:
        code_hash = _hash_secret(access_code.strip())
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM tenants WHERE access_code_hash = ?", (code_hash,)
            ).fetchone()
        if row is None:
            raise TenantAuthError("INVALID_ACCESS_CODE", "Invalid access code.")
        tenant = self._row_to_tenant(row)
        if tenant.disabled:
            raise TenantAuthError("TENANT_DISABLED", "This access code has been disabled.")
        return tenant

    def get_tenant(self, tenant_id: str) -> Tenant | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM tenants WHERE tenant_id = ?", (tenant_id,)
            ).fetchone()
        return self._row_to_tenant(row) if row else None

    # --- token ---

    def issue_token(
        self, tenant_id: str, *, ttl: timedelta = DEFAULT_TOKEN_TTL
    ) -> tuple[str, datetime]:
        """签发 token，返回（明文, 过期时间）。明文只在此处出现一次。"""
        raw_token = secrets.token_urlsafe(32)
        now = _now()
        expires_at = now + ttl
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO tenant_tokens
    (token_hash, tenant_id, created_at, expires_at, last_used_at, revoked)
                VALUES (?, ?, ?, ?, ?, 0)
                """,
                (
                    _hash_secret(raw_token), tenant_id,
                    _to_iso(now), _to_iso(expires_at), _to_iso(now),
                ),
            )
            self._conn.commit()
        return raw_token, expires_at

    def verify_token(self, raw_token: str) -> Tenant:
        """校验 token 并滑动续期。过期/撤销/不存在都是同一个错误码。"""
        if not raw_token:
            raise TenantAuthError("INVALID_TOKEN", "Missing tenant token.")
        token_hash = _hash_secret(raw_token)
        now = _now()
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM tenant_tokens WHERE token_hash = ?", (token_hash,)
            ).fetchone()
            if row is None or row["revoked"] or _from_iso(row["expires_at"]) <= now:
                raise TenantAuthError("INVALID_TOKEN", "Tenant token is invalid or expired.")
            # 滑动续期：每次使用刷新过期时间与 last_used_at
            self._conn.execute(
                "UPDATE tenant_tokens SET expires_at = ?, last_used_at = ? WHERE token_hash = ?",
                (_to_iso(now + DEFAULT_TOKEN_TTL), _to_iso(now), token_hash),
            )
            tenant_row = self._conn.execute(
                "SELECT * FROM tenants WHERE tenant_id = ?", (row["tenant_id"],)
            ).fetchone()
            self._conn.commit()
        if tenant_row is None:
            raise TenantAuthError("INVALID_TOKEN", "Tenant token is invalid or expired.")
        tenant = self._row_to_tenant(tenant_row)
        if tenant.disabled:
            raise TenantAuthError("TENANT_DISABLED", "This tenant has been disabled.")
        return tenant

    def revoke_token(self, raw_token: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE tenant_tokens SET revoked = 1 WHERE token_hash = ?",
                (_hash_secret(raw_token),),
            )
            self._conn.commit()

    # --- 会话索引 ---

    def create_conversation(
        self,
        *,
        tenant_id: str,
        conversation_id: str,
        title: str,
        transcript_path: str,
    ) -> ConversationRecord:
        now = _now()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO conversations
                    (conversation_id, tenant_id, title, transcript_path,
                     status, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'active', ?, ?)
                """,
                (conversation_id, tenant_id, title, transcript_path, _to_iso(now), _to_iso(now)),
            )
            # 新会话自动成为当前活跃会话
            self._conn.execute(
                "UPDATE tenants SET current_conversation_id = ? WHERE tenant_id = ?",
                (conversation_id, tenant_id),
            )
            self._conn.commit()
        return ConversationRecord(
            conversation_id=conversation_id,
            tenant_id=tenant_id,
            title=title,
            transcript_path=transcript_path,
            status="active",
            created_at=now,
            updated_at=now,
        )

    def list_conversations(self, tenant_id: str) -> list[ConversationRecord]:
        """活跃会话列表（updated_at 倒序）；归档会话保留记录但不出现。"""
        with self._lock:
            rows = self._conn.execute(
                (
                    "SELECT * FROM conversations WHERE tenant_id = ? AND status != 'archived' "
                    "ORDER BY updated_at DESC"
                ),
                (tenant_id,),
            ).fetchall()
        return [self._row_to_conversation(row) for row in rows]

    def archive_conversation(self, tenant_id: str, conversation_id: str) -> None:
        """归档会话：记录保留（transcript 文件不动），仅标记状态。"""
        self.get_conversation(tenant_id, conversation_id)  # 归属校验
        with self._lock:
            self._conn.execute(
                (
                    "UPDATE conversations SET status = 'archived', updated_at = ? "
                    "WHERE tenant_id = ? AND conversation_id = ?"
                ),
                (_to_iso(_now()), tenant_id, conversation_id),
            )
            self._conn.commit()

    def get_conversation(self, tenant_id: str, conversation_id: str) -> ConversationRecord:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM conversations WHERE tenant_id = ? AND conversation_id = ?",
                (tenant_id, conversation_id),
            ).fetchone()
        if row is None:
            # 跨租户与不存在同一个错误码，不泄露存在性
            raise TenantAuthError("CONVERSATION_NOT_FOUND", "Conversation not found.")
        return self._row_to_conversation(row)

    def get_current_conversation(self, tenant_id: str) -> ConversationRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT current_conversation_id FROM tenants WHERE tenant_id = ?", (tenant_id,)
            ).fetchone()
            current_id = row["current_conversation_id"] if row else None
        if not current_id:
            return None
        try:
            return self.get_conversation(tenant_id, current_id)
        except TenantAuthError:
            return None

    def set_current_conversation(self, tenant_id: str, conversation_id: str) -> None:
        # 先确认归属，再移动指针
        self.get_conversation(tenant_id, conversation_id)
        with self._lock:
            self._conn.execute(
                "UPDATE tenants SET current_conversation_id = ? WHERE tenant_id = ?",
                (conversation_id, tenant_id),
            )
            self._conn.commit()

    def update_conversation_title(
        self, tenant_id: str, conversation_id: str, title: str
    ) -> None:
        with self._lock:
            cursor = self._conn.execute(
                (
                    "UPDATE conversations SET title = ?, updated_at = ? "
                    "WHERE tenant_id = ? AND conversation_id = ?"
                ),
                (title, _to_iso(_now()), tenant_id, conversation_id),
            )
            self._conn.commit()
        if cursor.rowcount == 0:
            raise TenantAuthError("CONVERSATION_NOT_FOUND", "Conversation not found.")

    def touch_conversation(self, tenant_id: str, conversation_id: str) -> None:
        with self._lock:
            self._conn.execute(
                (
                    "UPDATE conversations SET updated_at = ? "
                    "WHERE tenant_id = ? AND conversation_id = ?"
                ),
                (_to_iso(_now()), tenant_id, conversation_id),
            )
            self._conn.commit()

    # --- row mapping ---

    @staticmethod
    def _row_to_tenant(row: sqlite3.Row) -> Tenant:
        return Tenant(
            tenant_id=row["tenant_id"],
            label=row["label"],
            disabled=bool(row["disabled"]),
            created_at=_from_iso(row["created_at"]),
            current_conversation_id=row["current_conversation_id"],
        )

    @staticmethod
    def _row_to_conversation(row: sqlite3.Row) -> ConversationRecord:
        return ConversationRecord(
            conversation_id=row["conversation_id"],
            tenant_id=row["tenant_id"],
            title=row["title"],
            transcript_path=row["transcript_path"],
            status=row["status"],
            created_at=_from_iso(row["created_at"]),
            updated_at=_from_iso(row["updated_at"]),
        )
