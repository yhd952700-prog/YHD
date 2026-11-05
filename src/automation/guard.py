"""执行守卫：允许清单、禁区清单、焦点校验、频率上限、连续失败熔断。

默认允许清单为**空**：未显式配置时任何目标都不被允许（fail-closed）。
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Iterable, Mapping, Optional, Sequence, Set, Tuple

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


@dataclass(frozen=True)
class ParamConstraint:
    """动作参数的允许范围。

    只有显式声明的参数才被允许；``required`` 缺失即拒绝。
    ``kind`` 支持 ``number`` / ``string`` / ``boolean``。
    """

    required: bool = False
    kind: Optional[str] = None
    min: Optional[float] = None
    max: Optional[float] = None
    min_length: Optional[int] = None
    max_length: Optional[int] = None
    allowed_values: Tuple[Any, ...] = ()


@dataclass(frozen=True)
class ActionRule:
    """动作级授权：哪个动作、作用于哪些目标、参数允许范围。"""

    action: str
    targets: Tuple[str, ...] = ()
    params: Mapping[str, ParamConstraint] = field(default_factory=dict)
    allow_unknown_params: bool = False


def target_matches(pattern: str, target_id: str) -> bool:
    """精确匹配，或尾部 ``*`` 前缀匹配（与全局允许清单同一语义）。"""

    entry = str(pattern or "").strip().lower()
    if not entry:
        return False
    if entry.endswith("*"):
        return str(target_id).startswith(entry[:-1])
    return str(target_id) == entry


@dataclass
class Guard:
    """有状态守卫，每个会话持有一个实例。"""

    allowlist: Iterable[str] = field(default_factory=tuple)
    action_rules: Iterable[ActionRule] = field(default_factory=tuple)
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
        rules = []
        for rule in self.action_rules:
            rules.append(
                ActionRule(
                    action=str(rule.action).strip().lower(),
                    targets=tuple(str(t).strip().lower() for t in rule.targets),
                    params=dict(rule.params),
                    allow_unknown_params=bool(rule.allow_unknown_params),
                )
            )
        self.action_rules = tuple(rules)

    # ---------------------------------------------------------------- 静态规则
    def check(
        self,
        request: ActionRequest,
        observation: Optional[Observation],
    ) -> GuardDecision:
        target_id = str(request.target_ref or "").strip().lower()

        if not self.allowlist:
            return self._deny("allowlist_empty", "允许清单为空：未配置任何可用目标")
        if not target_id:
            return self._deny("target_missing", "动作缺少目标标识")
        if not self._target_allowed(target_id):
            return self._deny(
                "target_not_allowed",
                f"目标 {target_id} 不在允许清单内",
                target=target_id,
            )

        forbidden = self._forbidden_marker(target_id, request.params)
        if forbidden:
            return self._deny(
                "forbidden_target",
                f"目标命中禁区规则 {forbidden}",
                marker=forbidden,
            )

        action_violation = self._action_violation(request)
        if action_violation is not None:
            return action_violation

        focus = self._focus_violation(request, observation)
        if focus:
            return self._deny(focus, "观察到的焦点与预期不一致")

        if self._rate_limited():
            return self._deny(
                "rate_limited",
                f"超过 {self.rate_limit_max} 动作/{int(self.rate_limit_window)} 秒上限",
            )

        return GuardDecision(allowed=True, reason="ok")

    def _target_allowed(self, target_id: str) -> bool:
        return any(target_matches(entry, target_id) for entry in self.allowlist)

    def _action_violation(self, request: ActionRequest) -> Optional[GuardDecision]:
        """动作级白名单。未配置规则时保持目标级授权（向后兼容）。"""

        if not self.action_rules:
            return None

        action = str(request.action or "").strip().lower()
        rules = [rule for rule in self.action_rules if rule.action == action]
        if not rules:
            return self._deny(
                "action_not_allowed",
                f"动作 {action or '<empty>'} 未获授权",
            )

        target_id = str(request.target_ref or "").strip().lower()
        target_rules = [
            rule
            for rule in rules
            if not rule.targets or any(target_matches(t, target_id) for t in rule.targets)
        ]
        if not target_rules:
            return self._deny(
                "action_target_not_allowed",
                f"动作 {action} 不允许作用于目标 {target_id}",
            )

        last: Optional[GuardDecision] = None
        for rule in target_rules:
            violation = self._param_violation(action, rule, request.params)
            if violation is None:
                return None
            last = violation
        return last or self._deny(
            "action_not_allowed",
            f"动作 {action} 未通过参数校验",
        )

    def _param_violation(
        self,
        action: str,
        rule: ActionRule,
        params: Mapping[str, Any],
    ) -> Optional[GuardDecision]:
        declared = dict(rule.params)
        if not rule.allow_unknown_params:
            for key in params:
                if key not in declared:
                    return self._deny(
                        "param_not_allowed",
                        f"动作 {action} 不接受参数 {key}",
                        param=key,
                    )

        for key, constraint in declared.items():
            if key not in params:
                if constraint.required:
                    return self._deny(
                        "param_missing",
                        f"动作 {action} 缺少必填参数 {key}",
                        param=key,
                    )
                continue

            value = params[key]
            if constraint.kind is not None:
                kind_error = _kind_violation(key, value, constraint.kind)
                if kind_error:
                    return self._deny(
                        kind_error,
                        f"参数 {key} 类型应为 {constraint.kind}",
                        param=key,
                    )

            if constraint.kind == "number":
                number = float(value)
                if constraint.min is not None and number < constraint.min:
                    return self._deny(
                        "param_below_min",
                        f"参数 {key}={number} 小于下限 {constraint.min}",
                        param=key,
                    )
                if constraint.max is not None and number > constraint.max:
                    return self._deny(
                        "param_above_max",
                        f"参数 {key}={number} 大于上限 {constraint.max}",
                        param=key,
                    )

            if constraint.kind == "string":
                length = len(str(value))
                if constraint.min_length is not None and length < constraint.min_length:
                    return self._deny(
                        "param_too_short",
                        f"参数 {key} 长度 {length} 小于下限 {constraint.min_length}",
                        param=key,
                    )
                if constraint.max_length is not None and length > constraint.max_length:
                    return self._deny(
                        "param_too_long",
                        f"参数 {key} 长度 {length} 超过上限 {constraint.max_length}",
                        param=key,
                    )

            if constraint.allowed_values and value not in constraint.allowed_values:
                return self._deny(
                    "param_value_not_allowed",
                    f"参数 {key} 的取值不在允许集合内",
                    param=key,
                )

        return None

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
    def _deny(reason: str, message: str = "", **detail: object) -> GuardDecision:
        return GuardDecision(allowed=False, reason=reason, message=message, detail=detail)


def _kind_violation(key: str, value: object, kind: str) -> Optional[str]:
    """类型约束校验，返回 reason（合法时返回 ``None``）。

    ``number`` 明确排除 ``bool``：Python 里 ``True`` 是 ``int`` 的子类，
    若放行布尔值，``{"x": true}`` 就能绕过数值范围检查。
    """

    expected = str(kind).strip().lower()
    if expected == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return "param_wrong_type"
        return None
    if expected == "string":
        if not isinstance(value, str):
            return "param_wrong_type"
        return None
    if expected == "boolean":
        if not isinstance(value, bool):
            return "param_wrong_type"
        return None
    return "param_constraint_unsupported"


def empty_allowlist() -> Set[str]:
    """返回默认允许清单（空集合），便于调用方显式表达"未配置"。"""

    return set()
