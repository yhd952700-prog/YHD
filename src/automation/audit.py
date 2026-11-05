"""自动化动作审计：每条动作一条事件，只记哈希不记明文。

禁止写入明文输入、剪贴板、OCR 全文。审计写入失败即视为动作失败（fail-closed）：
本模块把底层异常包装成 :class:`AutomationAuditError` 向上抛出，调用方不得吞掉。
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, Mapping, Optional

from ..kernels.audit import AuditEventType, AuditScope


class AutomationAuditError(Exception):
    """审计写失败。动作必须失败，不允许"继续执行但没留下证据"。"""


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _text_digest(value: str) -> Dict[str, Any]:
    return {"len": len(value), "sha256": _sha256_text(value)}


def _sanitize_param(key: str, value: Any) -> Any:
    """把参数转成可审计的摘要形式。"""

    if isinstance(value, str):
        return _text_digest(value)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if key in ("x", "y", "x_norm", "y_norm"):
            number = float(value)
            if 0.0 <= number <= 1.0:
                # 坐标一律记录归一化值，不记录像素绝对值。
                return {"normalized": round(number, 4)}
            return {"invalid_coordinate": True}
        return value
    if isinstance(value, Mapping):
        return {
            str(k): _sanitize_param(str(k), v)
            for k, v in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple, set)):
        payload = (
            repr(sorted(map(str, value)))
            if isinstance(value, set)
            else repr(list(value))
        )
        return {"items": len(value), "sha256": _sha256_text(payload)}
    return _text_digest(str(value))


class AutomationAudit:
    """把自动化动作写进审计链。

    ``store`` 可注入（测试用独立 SQLite）；None 时使用全局审计单例。
    """

    def __init__(self, store: Optional[Any] = None) -> None:
        self._store = store

    def _target(self) -> Any:
        if self._store is not None:
            return self._store
        from ..kernels.audit import get_audit_store

        return get_audit_store()

    def record_action(
        self,
        *,
        principal: str,
        session_id: str,
        step_no: int,
        action: str,
        outcome: str,
        target: str = "",
        target_ref: str = "",
        expected_focus: Optional[str] = None,
        params: Optional[Mapping[str, Any]] = None,
        fingerprint: str = "",
        stage: str = "intent",
        error: Optional[str] = None,
        reversibility: Optional[str] = None,
    ) -> str:
        """写入一条 AUTOMATION_ACTION 事件，返回 event_id。失败即抛异常。"""

        details: Dict[str, Any] = {
            "stage": stage,
            "session": _text_digest(str(session_id)),
            "step_no": int(step_no),
            "action": str(action),
            "target": str(target),
            "target_ref": _text_digest(str(target_ref)) if target_ref else None,
            "expected_focus": (
                _text_digest(str(expected_focus)) if expected_focus is not None else None
            ),
            "action_fingerprint": str(fingerprint),
            "params": _sanitize_param("params", dict(params or {})),
        }
        if reversibility is not None:
            details["reversibility"] = str(reversibility)
        if error is not None:
            details["error"] = _text_digest(str(error))

        try:
            event = self._target().log_event(
                event_type=AuditEventType.AUTOMATION_ACTION,
                principal_id=str(principal),
                scope=AuditScope.L2,
                outcome=str(outcome),
                details=details,
            )
        except Exception as exc:  # noqa: BLE001 - fail-closed，必须向上抛
            raise AutomationAuditError(f"审计写入失败: {type(exc).__name__}") from exc

        return str(getattr(event, "event_id", ""))
