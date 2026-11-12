"""审计 details 落库前必须脱敏（2026-11-08 日志泄漏审计 F3）。

审计库是证据库，但不能把上游 401 错误体里回显的 API Key 原样固化进
SQLite —— 那样"日志已脱敏"只是把泄漏换了个文件。这里直接写入敏感 details，
再从数据库读回原始行断言。
"""
from __future__ import annotations

from src.common.redaction import leak_violations
from src.kernels.audit import AuditEventType, AuditScope, AuditStore


def test_audit_details_are_redacted_before_persisting(tmp_path):
    store = AuditStore(db_path=str(tmp_path / "audit.db"))
    try:
        event = store.log_event(
            AuditEventType.STATE_CHANGE,
            "tester",
            AuditScope.L1,
            "failure",
            details={
                "reason": (
                    "AuthenticationError: Bearer sk-LHLEAK-1234567890abcdef "
                    "https://example.invalid/keys?key=PLAINSECRET-ABCDEF123456"
                ),
                "nested": {"token": "sk-ABCDEF1234567890", "ok": 1},
                "items": ["key=PLAIN-KEY-1234567890"],
            },
            correlation_id="c-leak",
        )
        row = store._conn.execute(
            "SELECT details FROM audit_events WHERE event_id = ?", (event.event_id,)
        ).fetchone()
    finally:
        close = getattr(store, "close", None)
        if callable(close):
            close()

    raw = row[0]
    assert "sk-LHLEAK-1234567890abcdef" not in raw
    assert "sk-ABCDEF1234567890" not in raw
    assert "PLAINSECRET-ABCDEF123456" not in raw
    assert "PLAIN-KEY-1234567890" not in raw
    assert leak_violations(raw) == []
