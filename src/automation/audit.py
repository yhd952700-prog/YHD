"""自动化动作审计：每条动作一条事件，只记哈希不记明文。

禁止写入明文输入、剪贴板、OCR 全文。审计写入失败即视为动作失败（fail-closed）：
本模块把底层异常包装成 :class:`AutomationAuditError` 向上抛出，调用方不得吞掉。
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, Mapping, Optional

from ..kernels.audit import AuditEventType, AuditScope
from .contracts import ElementInfo, Observation, WindowInfo


class AutomationAuditError(Exception):
    """审计写失败。动作必须失败，不允许"继续执行但没留下证据"。"""


#: 单条观察审计里最多记录多少个窗口/元素明细；超出部分只保留总数，
#: 避免一次观察写出上千条哈希把审计库拖慢。
AUDIT_MAX_WINDOW_DETAILS = 20
AUDIT_MAX_ELEMENT_DETAILS = 50


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _text_digest(value: str) -> Dict[str, Any]:
    return {"len": len(value), "sha256": _sha256_text(value)}


def _window_digest(window: WindowInfo) -> Dict[str, Any]:
    """窗口信息转审计摘要：文本只记长度与哈希，坐标只记是否存在。

    句柄与 PID 是进程内标识，不含用户内容；窗口标题、进程名、可执行路径、
    错误文本全部走 ``_text_digest``，审计库中不出现明文。
    """

    return {
        "handle": int(window.handle),
        "pid": window.pid,
        "title": _text_digest(window.title) if window.title else None,
        "process_name": _text_digest(window.process_name) if window.process_name else None,
        "executable_path": (
            _text_digest(window.executable_path) if window.executable_path else None
        ),
        "process_error": (
            _text_digest(window.process_error) if window.process_error else None
        ),
        "foreground": bool(window.foreground),
        "fullscreen": bool(window.fullscreen),
        "fullscreen_error": (
            _text_digest(window.fullscreen_error) if window.fullscreen_error else None
        ),
        "visible": bool(window.visible),
        "minimized": bool(window.minimized),
        "bounds_present": window.bounds is not None,
        "elements_supported": bool(window.elements_supported),
        "element_count": int(window.element_count),
    }


def _element_digest(element: ElementInfo) -> Dict[str, Any]:
    return {
        "handle": int(element.handle),
        "class_name": _text_digest(element.class_name) if element.class_name else None,
        "control_id": element.control_id,
        "enabled": bool(element.enabled),
        "visible": bool(element.visible),
        "bounds_present": element.bounds is not None,
    }


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

    def record_observation(
        self,
        *,
        principal: str,
        observation: Observation,
        outcome: str = "observed",
    ) -> str:
        """写入一条 AUTOMATION_OBSERVATION 事件，返回 event_id。

        隐私规则与动作审计一致：窗口标题、进程名、可执行路径只记
        ``{"len", "sha256"}``，不落明文；坐标只记 ``bounds_present``。
        明细条数有上限，超出只保留计数，避免观察高频时把审计库写爆。
        写入失败即抛 :class:`AutomationAuditError`，调用方不得把没有证据的
        观察结果交给用户。
        """

        windows = tuple(observation.windows)
        elements = tuple(observation.elements)
        details: Dict[str, Any] = {
            "stage": "observation",
            "backend": str(observation.backend),
            "scope": observation.scope.value,
            "observation_digest": observation.digest(),
            "captured_at": float(observation.captured_at),
            "focus": _text_digest(observation.focus) if observation.focus else None,
            "foreground": (
                _window_digest(observation.foreground)
                if observation.foreground is not None
                else None
            ),
            "window_count": len(windows),
            "windows": [
                _window_digest(item) for item in windows[:AUDIT_MAX_WINDOW_DETAILS]
            ],
            "windows_truncated_for_audit": max(
                0, len(windows) - AUDIT_MAX_WINDOW_DETAILS
            ),
            "element_count": len(elements),
            "elements": [
                _element_digest(item) for item in elements[:AUDIT_MAX_ELEMENT_DETAILS]
            ],
            "elements_truncated_for_audit": max(
                0, len(elements) - AUDIT_MAX_ELEMENT_DETAILS
            ),
            "elements_supported": bool(observation.elements_supported),
            "elements_reason": (
                _text_digest(observation.elements_reason)
                if observation.elements_reason
                else None
            ),
            "truncated": bool(observation.truncated),
        }

        try:
            event = self._target().log_event(
                event_type=AuditEventType.AUTOMATION_OBSERVATION,
                principal_id=str(principal),
                scope=AuditScope.L2,
                outcome=str(outcome),
                details=details,
            )
        except Exception as exc:  # noqa: BLE001 - fail-closed，必须向上抛
            raise AutomationAuditError(f"审计写入失败: {type(exc).__name__}") from exc

        return str(getattr(event, "event_id", ""))
