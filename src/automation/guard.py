"""执行守卫：允许清单、禁区清单、焦点校验、频率上限、连续失败熔断。

默认允许清单为**空**：未显式配置时任何目标都不被允许（fail-closed）。
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Deque, Iterable, Mapping, Optional, Sequence, Set

from .contracts import (
    CONSECUTIVE_FAILURE_LIMIT,
    FORBIDDEN_TARGET_MARKERS,
    RATE_LIMIT_MAX_ACTIONS,
    RATE_LIMIT_WINDOW_SECONDS,
    ActionRequest,
    GuardDecision,
    Observation,
    TargetScope,
)

#: 需要参与禁区匹配的目标标识字段（只匹配"去哪里"，不扫描用户输入内容，
#: 否则在文本编辑器里输入 "password" 会被误判为密码框）。
_TARGET_FIELDS = (
    "target_label",
    "app",
    "application",
    "window",
    "window_title",
    "process",
    "process_name",
    "path",
)


@dataclass
class Guard:
    """有状态守卫，每个会话持有一个实例。"""

    allowlist: Iterable[str] = field(default_factory=tuple)
    forbidden_markers: Sequence[str] = FORBIDDEN_TARGET_MARKERS
    rate_limit_max: int = RATE_LIMIT_MAX_ACTIONS
    rate_limit_window: float = RATE_LIMIT_WINDOW_SECONDS
    failure_limit: int = CONSECUTIVE_FAILURE_LIMIT
    clock: Callable[[], float] = time.monotonic

    _attempts: Deque[float] = field(default_factory=deque, init=False, repr=False)
    _consecutive_failures: int = field(default=0, init=False, repr=False)
    _tripped: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        self.allowlist = tuple(str(a).strip().lower() for a in self.allowlist if str(a).strip())

    # ---------------------------------------------------------------- 静态规则
    def check(
        self,
        request: ActionRequest,
        observation: Optional[Observation],
    ) -> GuardDecision:
        target_id = str(request.target_ref or "").strip().lower()

        if not self.allowlist:
            return self._deny("allowlist_empty")
        if not target_id:
            return self._deny("target_missing")
        if not self._target_allowed(target_id):
            return self._deny("target_not_allowed", target=target_id)

        forbidden = self._forbidden_marker(target_id, request.params)
        if forbidden:
            return self._deny("forbidden_target", marker=forbidden)

        focus = self._focus_violation(request, observation)
        if focus:
            return self._deny(focus)

        if self._rate_limited():
            return self._deny("rate_limited")

        return GuardDecision(allowed=True, reason="ok")

    def _target_allowed(self, target_id: str) -> bool:
        for entry in self.allowlist:
            if entry.endswith("*"):
                if target_id.startswith(entry[:-1]):
                    return True
            elif target_id == entry:
                return True
        return False

    def _forbidden_marker(
        self,
        target_id: str,
        params: Mapping[str, object],
    ) -> Optional[str]:
        haystacks = [target_id]
        for key in _TARGET_FIELDS:
            value = params.get(key)
            if isinstance(value, str) and value.strip():
                haystacks.append(value.strip().lower())
        for text in haystacks:
            for marker in self.forbidden_markers:
                if marker.lower() in text:
                    return marker
        return None

    @staticmethod
    def _focus_violation(
        request: ActionRequest,
        observation: Optional[Observation],
    ) -> Optional[str]:
        expected = request.expected_focus
        requires_focus = request.target in (
            TargetScope.WINDOW,
            TargetScope.APPLICATION,
        )
        if expected is None and not requires_focus:
            return None
        if observation is None:
            return "observation_unavailable"
        if expected is not None:
            if observation.focus != expected:
                return "focus_mismatch"
            return None
        if observation.focus != request.target_ref:
            return "focus_mismatch"
        return None

    # ---------------------------------------------------------------- 状态记录
    def record_attempt(self, now: Optional[float] = None) -> None:
        stamp = self.clock() if now is None else now
        self._attempts.append(stamp)
        self._prune(stamp)

    def record_success(self) -> None:
        self._consecutive_failures = 0
        self._tripped = False

    def record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.failure_limit:
            self._tripped = True

    @property
    def tripped(self) -> bool:
        return self._tripped

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    def reset(self) -> None:
        """仅用于测试/人工复位；正常路径不应调用。"""

        self._attempts.clear()
        self._consecutive_failures = 0
        self._tripped = False

    # ---------------------------------------------------------------- 内部
    def _rate_limited(self) -> bool:
        now = self.clock()
        self._prune(now)
        return len(self._attempts) >= self.rate_limit_max

    def _prune(self, now: float) -> None:
        cutoff = now - self.rate_limit_window
        while self._attempts and self._attempts[0] <= cutoff:
            self._attempts.popleft()

    @staticmethod
    def _deny(reason: str, **detail: object) -> GuardDecision:
        return GuardDecision(allowed=False, reason=reason, detail=detail)


def empty_allowlist() -> Set[str]:
    """返回默认允许清单（空集合），便于调用方显式表达"未配置"。"""

    return set()
