"""确认票据：SQLite 持久化、单次消费、TTL ≤ 30s、绑定主体 + 会话 + 步号 + 指纹。

不复用现有 grant 体系。票据的原子消费依赖一条 ``UPDATE ... WHERE status='pending'
AND expires_at > now`` 语句，并要求 ``rowcount == 1``；并发消费时只有一方成功。
"""

from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
import threading
import time
from typing import Callable

from .contracts import (
    MAX_CONFIRMATION_TTL_SECONDS,
    AutomationError,
    ConfirmationTicket,
    TicketConsumeResult,
)

_QUEUE_SCHEMA = """
CREATE TABLE IF NOT EXISTS automation_confirmation_tickets (
    ticket_id TEXT PRIMARY KEY,
    principal TEXT NOT NULL,
    session_id TEXT NOT NULL,
    step_no INTEGER NOT NULL,
    action_fingerprint TEXT NOT NULL,
    preview TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    consumed_at REAL
);
CREATE INDEX IF NOT EXISTS idx_automation_tickets_status
    ON automation_confirmation_tickets(status, expires_at);
"""

_PREVIEW_LIMIT = 200


class SqliteConfirmationBroker:
    """确认票据的 SQLite 实现。

    ``ticket_id`` 存的是票据令牌的 sha256：原始令牌只返回给调用方，不落库，
    即使数据库被读取也无法重放。``clock`` 可注入，便于确定性测试过期。
    """

    def __init__(
        self,
        db_path: str,
        *,
        clock: Callable[[], float] = time.time,
        max_ttl: float = MAX_CONFIRMATION_TTL_SECONDS,
    ) -> None:
        self._db_path = db_path
        self._clock = clock
        self._max_ttl = max(1.0, min(MAX_CONFIRMATION_TTL_SECONDS, float(max_ttl)))
        self._lock = threading.RLock()
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self._conn = sqlite3.connect(
            db_path, timeout=30.0, check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        with self._conn:
            self._conn.executescript(_QUEUE_SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---------------------------------------------------------------- 签发
    def issue(
        self,
        *,
        principal: str,
        principal_kind: str,
        session_id: str,
        step_no: int,
        action_fingerprint: str,
        preview: str,
        ttl_seconds: float = MAX_CONFIRMATION_TTL_SECONDS,
    ) -> ConfirmationTicket:
        kind = str(principal_kind or "").strip().lower()
        if kind != "human":
            # 服务身份永远不能确认任何动作。
            raise AutomationError("service_identity", "只有人类主体可以签发确认票据")
        if not str(principal or "").strip():
            raise AutomationError("principal_missing")
        if not str(session_id or "").strip():
            raise AutomationError("session_missing")
        if not str(action_fingerprint or "").strip():
            raise AutomationError("fingerprint_missing")
        try:
            step = int(step_no)
        except (TypeError, ValueError) as exc:
            raise AutomationError("invalid_step_no") from exc

        ttl = max(1.0, min(self._max_ttl, float(ttl_seconds)))
        now = self._clock()
        token = secrets.token_urlsafe(32)
        ticket_id = _sha256(token)
        preview_text = str(preview or "")[:_PREVIEW_LIMIT]

        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO automation_confirmation_tickets "
                "(ticket_id, principal, session_id, step_no, action_fingerprint, "
                " preview, status, created_at, expires_at, consumed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, NULL)",
                (
                    ticket_id,
                    str(principal),
                    str(session_id),
                    step,
                    str(action_fingerprint),
                    preview_text,
                    now,
                    now + ttl,
                ),
            )

        return ConfirmationTicket(
            ticket=token,
            principal=str(principal),
            session_id=str(session_id),
            step_no=step,
            action_fingerprint=str(action_fingerprint),
            preview=preview_text,
            expires_at=now + ttl,
        )

    # ---------------------------------------------------------------- 消费
    def consume(
        self,
        *,
        ticket: str,
        principal: str,
        session_id: str,
        step_no: int,
        action_fingerprint: str,
    ) -> TicketConsumeResult:
        ticket_id = _sha256(str(ticket or ""))
        now = self._clock()

        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                row = self._conn.execute(
                    "SELECT * FROM automation_confirmation_tickets WHERE ticket_id=?",
                    (ticket_id,),
                ).fetchone()
                if row is None:
                    self._conn.rollback()
                    return TicketConsumeResult(ok=False, reason="not_found")

                if (
                    row["principal"] != str(principal)
                    or row["session_id"] != str(session_id)
                    or int(row["step_no"]) != int(step_no)
                    or row["action_fingerprint"] != str(action_fingerprint)
                ):
                    self._conn.rollback()
                    return TicketConsumeResult(ok=False, reason="binding_mismatch")

                if row["status"] != "pending":
                    self._conn.rollback()
                    return TicketConsumeResult(ok=False, reason="already_consumed")

                cursor = self._conn.execute(
                    "UPDATE automation_confirmation_tickets "
                    "SET status='consumed', consumed_at=? "
                    "WHERE ticket_id=? AND principal=? AND session_id=? AND step_no=? "
                    "AND action_fingerprint=? AND status='pending' AND expires_at > ?",
                    (
                        now,
                        ticket_id,
                        str(principal),
                        str(session_id),
                        int(step_no),
                        str(action_fingerprint),
                        now,
                    ),
                )
                if cursor.rowcount != 1:
                    self._conn.rollback()
                    reason = "expired" if float(row["expires_at"]) <= now else "consume_failed"
                    return TicketConsumeResult(ok=False, reason=reason)

                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

        return TicketConsumeResult(
            ok=True,
            ticket=ConfirmationTicket(
                ticket=str(ticket),
                principal=str(principal),
                session_id=str(session_id),
                step_no=int(step_no),
                action_fingerprint=str(action_fingerprint),
                preview=str(row["preview"]),
                expires_at=float(row["expires_at"]),
            ),
        )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
