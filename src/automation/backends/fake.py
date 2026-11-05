"""确定性的假后端：用于地基阶段的闭环测试，不接触真实屏幕/键鼠。

它只维护"焦点 + 修订号 + 最近输入长度"，不保存任何输入明文，因此即使
被误用于生产也不会把敏感内容写进内存或审计。
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional

from ..contracts import (
    ActionCapability,
    ActionRequest,
    ActionResult,
    AutomationBackend,
    BackendKind,
    Observation,
    Reversibility,
    TargetScope,
)

#: 明确可逆的假动作：执行前保存状态快照，回滚时恢复。
_REVERSIBLE_ACTIONS = frozenset({"focus", "type", "click", "key", "noop"})

#: 明确不可逆的假动作：执行后无法恢复，必须在确认前告知。
_IRREVERSIBLE_ACTIONS = {"delete": "destructive_action"}


class FakeAutomationBackend(AutomationBackend):
    """内存假后端。"""

    kind = BackendKind.FAKE

    def __init__(self, *, initial_focus: Optional[str] = None) -> None:
        self._lock = threading.Lock()
        self._focus = initial_focus
        self._revision = 0
        self._last_typed_len: Optional[int] = None
        # (session_id, step_no) -> 执行前状态快照，供回滚恢复。
        self._undo: Dict[tuple, tuple] = {}

    @property
    def focus(self) -> Optional[str]:
        with self._lock:
            return self._focus

    def observe(self, scope: TargetScope = TargetScope.SCREEN) -> Observation:
        with self._lock:
            summary: Dict[str, Any] = {"revision": self._revision}
            if self._last_typed_len is not None:
                summary["last_typed_len"] = self._last_typed_len
            return Observation(
                focus=self._focus,
                scope=scope,
                captured_at=time.time(),
                summary=summary,
            )

    def capability(self, request: ActionRequest) -> ActionCapability:
        action = str(request.action or "").strip().lower()
        if action in _REVERSIBLE_ACTIONS:
            return ActionCapability(
                action=action,
                reversibility=Reversibility.REVERSIBLE,
                reason="state_snapshot_available",
            )
        if action in _IRREVERSIBLE_ACTIONS:
            return ActionCapability(
                action=action,
                reversibility=Reversibility.IRREVERSIBLE,
                reason=_IRREVERSIBLE_ACTIONS[action],
            )
        return ActionCapability(
            action=action or "<empty>",
            reversibility=Reversibility.IRREVERSIBLE,
            reason="unknown_action",
        )

    def execute(self, request: ActionRequest) -> ActionResult:
        with self._lock:
            action = str(request.action or "")
            self._undo[(request.session_id, request.step_no)] = (
                self._focus,
                self._last_typed_len,
            )
            if action == "delete":
                return self._failed(request, "irreversible_action")
            if action == "focus":
                app = str(request.params.get("app") or request.target_ref or "")
                if not app:
                    return self._failed(request, "focus_target_missing")
                self._focus = app
                return self._executed(request, "focus_applied")
            if action == "type":
                text = str(request.params.get("text") or "")
                # 只保留长度：假后端也不保存用户输入明文。
                self._last_typed_len = len(text)
                return self._executed(request, "text_typed")
            if action == "click":
                return self._executed(request, "clicked")
            if action in ("key", "noop"):
                return self._executed(request, "noop")
            return self._failed(request, "unsupported_action")

    def rollback(self, *, request: ActionRequest, result: ActionResult) -> ActionResult:
        """恢复执行前快照；找不到快照就如实失败，绝不假装回滚成功。"""

        with self._lock:
            if not result.ok:
                return self._failed(request, "rollback_of_failed_action")
            key = (request.session_id, request.step_no)
            snapshot = self._undo.pop(key, None)
            if snapshot is None:
                return self._failed(request, "rollback_not_available")
            self._focus, self._last_typed_len = snapshot
            self._revision += 1
            return self._executed(request, "rolled_back")

    # ---------------------------------------------------------------- helpers
    def _executed(self, request: ActionRequest, detail: str) -> ActionResult:
        self._revision += 1
        return ActionResult(
            status="executed",
            action=request.action,
            ok=True,
            observation=self._observe_locked(request.target),
            detail={"detail": detail, "revision": self._revision},
        )

    def _failed(self, request: ActionRequest, error: str) -> ActionResult:
        return ActionResult(
            status="failed",
            action=request.action,
            ok=False,
            error=error,
            observation=self._observe_locked(request.target),
        )

    def _observe_locked(self, scope: TargetScope) -> Observation:
        summary: Dict[str, Any] = {"revision": self._revision}
        if self._last_typed_len is not None:
            summary["last_typed_len"] = self._last_typed_len
        return Observation(
            focus=self._focus,
            scope=scope,
            captured_at=time.time(),
            summary=summary,
        )
