"""只读观察：前台窗口、窗口清单与可枚举元素。

这一层**没有任何输入注入**：不点击、不输入、不截图、不读跨进程内存。
按用户授权，只读观察默认开启（无副作用），但必须受两道限制：

* **节流**：每 ``OBSERVATION_RATE_LIMIT_WINDOW_SECONDS`` 秒最多采
  ``OBSERVATION_RATE_LIMIT_MAX`` 次，超限直接拒绝，不返回旧数据伪装成新数据；
* **一键关闭**：``ObservationPolicy.disable()`` 立即关闭，``enable()`` 恢复；
  环境变量 ``LIUHAO_AUTOMATION_OBSERVE_ENABLED`` 显式设为假值时，进程启动
  就不注册任何观察工具（管理员级硬开关）。

隐私：窗口标题、进程名、可执行路径允许回显给人类，但落审计时只写长度与
哈希，见 :meth:`AutomationAudit.record_observation`。
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, replace
from typing import Any, Callable, Dict, List, Mapping, Optional, Protocol, Sequence, Tuple

from ..ai.tool_registry import Tool
from .audit import AutomationAudit
from .contracts import (
    AutomationError,
    ElementInfo,
    Observation,
    TargetScope,
    WindowInfo,
)
from .gate import AutomationGate

OBSERVE_ENV = "LIUHAO_AUTOMATION_OBSERVE_ENABLED"

#: 默认节流：10 秒内最多采 4 次（约 24 次/分钟），足以看当前状态，
#: 又不至于变成截图式轮询。
OBSERVATION_RATE_LIMIT_MAX = 4
OBSERVATION_RATE_LIMIT_WINDOW_SECONDS = 10.0

DEFAULT_MAX_WINDOWS = 20
DEFAULT_MAX_ELEMENTS = 200

#: 读不到控件树时统一使用的可读说明；不得编造元素或坐标。
ELEMENTS_UNSUPPORTED_REASON = "该窗口不支持元素级读取"

_FALSY = frozenset({"0", "false", "no", "off"})

_policy_lock = threading.Lock()
_policy: Optional["ObservationPolicy"] = None


def observation_enabled(env: Optional[Mapping[str, str]] = None) -> bool:
    """只读观察是否启用。默认开启；只有显式假值才关闭。"""

    value = env.get(OBSERVE_ENV) if env is not None else os.environ.get(OBSERVE_ENV)
    return str(value or "").strip().lower() not in _FALSY


def get_observation_policy() -> "ObservationPolicy":
    """进程级观察策略单例，保证一键关闭对所有工具入口生效。"""

    global _policy
    if _policy is None:
        with _policy_lock:
            if _policy is None:
                _policy = ObservationPolicy()
    return _policy


class ObservationPolicy:
    """观察开关 + 节流。

    Args:
        enabled: 显式指定初始开关；None 时读环境变量（默认开）。
        max_per_window: 窗口期内允许的采集次数。
        window_seconds: 节流窗口长度（秒）。
        clock: 单调时钟，测试可注入；``acquire(now=...)`` 优先使用显式值。
    """

    def __init__(
        self,
        *,
        enabled: Optional[bool] = None,
        max_per_window: int = OBSERVATION_RATE_LIMIT_MAX,
        window_seconds: float = OBSERVATION_RATE_LIMIT_WINDOW_SECONDS,
        env: Optional[Mapping[str, str]] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_per_window < 1:
            raise ValueError("max_per_window 必须 >= 1")
        if window_seconds <= 0:
            raise ValueError("window_seconds 必须 > 0")
        configured = observation_enabled(env) if enabled is None else bool(enabled)
        self._configured_enabled = configured
        self._enabled = configured
        self._max_per_window = int(max_per_window)
        self._window_seconds = float(window_seconds)
        self._clock = clock
        self._lock = threading.Lock()
        self._events: List[float] = []
        self._disabled_reason = "" if configured else "disabled_by_config"

    # ---------------------------------------------------------------- 开关
    @property
    def configured_enabled(self) -> bool:
        """进程启动时的管理员级开关（环境变量/构造参数）。"""

        return self._configured_enabled

    @property
    def enabled(self) -> bool:
        """当前运行时开关（可被 :meth:`disable` 一键关闭）。"""

        with self._lock:
            return self._enabled

    @property
    def disabled_reason(self) -> str:
        with self._lock:
            return self._disabled_reason

    def disable(self, reason: str = "user_disabled") -> Dict[str, Any]:
        with self._lock:
            self._enabled = False
            self._disabled_reason = str(reason or "user_disabled")
            self._events.clear()
        return self.snapshot()

    def enable(self) -> Dict[str, Any]:
        with self._lock:
            self._enabled = True
            self._disabled_reason = ""
            self._events.clear()
        return self.snapshot()

    # ---------------------------------------------------------------- 节流
    def acquire(self, now: Optional[float] = None) -> None:
        """占用一次采集配额。关闭或超限时抛出 ``AutomationError``。"""

        current = self._clock() if now is None else float(now)
        with self._lock:
            if not self._enabled:
                raise AutomationError(
                    "observation_disabled",
                    f"观察已关闭（{self._disabled_reason or 'disabled'}）",
                )
            cutoff = current - self._window_seconds
            self._events = [value for value in self._events if value >= cutoff]
            if len(self._events) >= self._max_per_window:
                raise AutomationError(
                    "observation_rate_limited",
                    "观察过于频繁："
                    f"{self._window_seconds:g} 秒内最多 {self._max_per_window} 次",
                )
            # 失败的上报与成功一样占用配额：宁可少采，也不给轮询留后门。
            self._events.append(current)

    def snapshot(self, now: Optional[float] = None) -> Dict[str, Any]:
        current = self._clock() if now is None else float(now)
        with self._lock:
            cutoff = current - self._window_seconds
            used = len([value for value in self._events if value >= cutoff])
            return {
                "enabled": self._enabled,
                "configured_enabled": self._configured_enabled,
                "max_per_window": self._max_per_window,
                "window_seconds": self._window_seconds,
                "used_in_window": used,
                "disabled_reason": self._disabled_reason,
            }


class WindowObserver(Protocol):
    """只读观察器接口。实现方不得编造窗口、元素或坐标。"""

    backend: str

    def observe(
        self,
        scope: TargetScope = TargetScope.WINDOW,
        *,
        include_elements: bool = True,
    ) -> Observation:
        """读取当前窗口状态；读不到的字段必须如实留空并说明原因。"""


@dataclass(frozen=True)
class ObservationGateDecision:
    """观察工具的准入门槛：管理员开关 + 人类主体。

    与动作的三重闸不同：观察没有副作用，所以不要求装配闸/后端闸；
    但窗口标题属于敏感信息，仍然只允许人类主体读取。
    """

    allowed: bool
    reason: str
    enabled: bool
    principal_open: bool
    principal_kind: str

    def as_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "enabled": self.enabled,
            "principal_open": self.principal_open,
            "principal_kind": self.principal_kind,
        }


class ObservationGate:
    """观察准入判定。

    ``enabled`` 取策略的**启动配置**（管理员级硬开关）：环境变量显式关闭时
    连工具都不注册。运行时的 ``disable()`` 只影响采集调用，不影响工具存在，
    这样用户还能用同一个工具重新打开。
    """

    def __init__(
        self,
        policy: Optional[ObservationPolicy] = None,
        *,
        principal_kind_resolver: Optional[Callable[[str], Optional[str]]] = None,
    ) -> None:
        self._policy = policy or get_observation_policy()
        self._resolver = principal_kind_resolver

    def evaluate(
        self,
        principal: str,
        *,
        metadata: Optional[Mapping[str, object]] = None,
    ) -> ObservationGateDecision:
        enabled = self._policy.configured_enabled
        # 复用动作闸的主体解析逻辑（查不到身份即 unknown），不另起一套鉴权。
        gate = AutomationGate(principal_kind_resolver=self._resolver)
        decision = gate.evaluate(principal, metadata=metadata)
        principal_open = bool(str(principal or "").strip()) and (
            decision.principal_kind == "human"
        )
        reasons = []
        if not enabled:
            reasons.append("observation_disabled_by_config")
        if not principal_open:
            reasons.append("principal_closed")
        return ObservationGateDecision(
            allowed=enabled and principal_open,
            reason=";".join(reasons) or "open",
            enabled=enabled,
            principal_open=principal_open,
            principal_kind=decision.principal_kind,
        )


class ObservationService:
    """把观察器、节流与审计串起来。"""

    def __init__(
        self,
        observer: WindowObserver,
        *,
        policy: Optional[ObservationPolicy] = None,
        audit: Optional[AutomationAudit] = None,
        max_windows: int = DEFAULT_MAX_WINDOWS,
        max_elements: int = DEFAULT_MAX_ELEMENTS,
    ) -> None:
        if max_windows < 1:
            raise ValueError("max_windows 必须 >= 1")
        if max_elements < 0:
            raise ValueError("max_elements 必须 >= 0")
        self._observer = observer
        self._policy = policy or get_observation_policy()
        self._audit = audit
        self._max_windows = int(max_windows)
        self._max_elements = int(max_elements)

    def observe(
        self,
        *,
        principal: str,
        scope: TargetScope = TargetScope.WINDOW,
        include_elements: bool = True,
        now: Optional[float] = None,
    ) -> Observation:
        """采集一次。关闭/超限/后端不可用都抛 ``AutomationError``。"""

        self._policy.acquire(now)
        observation = self._observer.observe(scope, include_elements=include_elements)
        if not isinstance(observation, Observation):
            raise AutomationError(
                "observation_invalid",
                "观察器返回了非 Observation 结果",
            )
        observation = self._truncate(observation)
        if self._audit is not None:
            self._audit.record_observation(principal=principal, observation=observation)
        return observation

    def _truncate(self, observation: Observation) -> Observation:
        windows = tuple(observation.windows)
        elements = tuple(observation.elements)
        truncated = bool(observation.truncated)
        if len(windows) > self._max_windows:
            windows = windows[: self._max_windows]
            truncated = True
        if len(elements) > self._max_elements:
            elements = elements[: self._max_elements]
            truncated = True
        if len(windows) == len(observation.windows) and len(elements) == len(
            observation.elements
        ):
            return observation
        return replace(
            observation,
            windows=windows,
            elements=elements,
            truncated=truncated,
        )

    @property
    def backend(self) -> str:
        return str(self._observer.backend)


class FakeWindowObserver:
    """注入式假观察器：测试与无桌面环境使用，不触碰真实系统。

    ``windows`` / ``elements`` 必须由调用方显式给出，缺口（比如没有元素数据）
    会如实报告"不支持元素级读取"，不会编造坐标。
    """

    backend = "fake"

    def __init__(
        self,
        windows: Sequence[WindowInfo | Mapping[str, Any]] = (),
        *,
        elements: Optional[
            Mapping[int, Sequence[ElementInfo | Mapping[str, Any]]]
        ] = None,
        foreground_handle: Optional[int] = None,
        elements_supported: Optional[bool] = None,
        elements_reason: str = "",
        summary: Optional[Mapping[str, Any]] = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._windows = tuple(_coerce_window(item) for item in windows)
        self._elements = {
            int(handle): tuple(_coerce_element(item) for item in values)
            for handle, values in (elements or {}).items()
        }
        self._foreground_handle = foreground_handle
        self._elements_supported = elements_supported
        self._elements_reason = str(elements_reason or "")
        self._summary = dict(summary or {})
        self._clock = clock
        self.calls = 0

    def observe(
        self,
        scope: TargetScope = TargetScope.WINDOW,
        *,
        include_elements: bool = True,
    ) -> Observation:
        self.calls += 1
        foreground = self._resolve_foreground()
        if foreground is not None:
            foreground = self._with_element_state(foreground, include_elements)

        windows = list(self._windows)
        if scope is TargetScope.SCREEN:
            if foreground is not None:
                # 前台窗口永远排第一，方便人类直接看到。
                windows = [
                    item for item in windows if item.handle == foreground.handle
                ] + [item for item in windows if item.handle != foreground.handle]
        else:
            windows = (
                [item for item in windows if item.handle == foreground.handle]
                if foreground is not None
                else []
            )

        elements: Tuple[ElementInfo, ...] = ()
        supported = False
        reason = ""
        if include_elements:
            if foreground is None:
                reason = "没有前台窗口，无法读取元素"
            else:
                elements = self._elements.get(foreground.handle, ())
                if self._elements_supported is None:
                    supported = bool(elements)
                    if supported:
                        reason = ""
                    else:
                        reason = self._elements_reason or ELEMENTS_UNSUPPORTED_REASON
                else:
                    supported = bool(self._elements_supported)
                    reason = (
                        ""
                        if supported
                        else (self._elements_reason or ELEMENTS_UNSUPPORTED_REASON)
                    )
                    if not supported:
                        elements = ()
        else:
            reason = "调用方未请求元素"

        summary = dict(self._summary)
        summary.setdefault("window_count", len(windows))
        summary.setdefault("fake", True)

        return Observation(
            focus=(foreground.process_name or foreground.title)
            if foreground is not None
            else None,
            scope=scope,
            captured_at=self._clock(),
            summary=summary,
            backend=self.backend,
            foreground=foreground,
            windows=tuple(windows),
            elements=tuple(elements),
            elements_supported=supported,
            elements_reason=reason,
            truncated=False,
        )

    # ---------------------------------------------------------------- helpers
    def _resolve_foreground(self) -> Optional[WindowInfo]:
        if self._foreground_handle is not None:
            for item in self._windows:
                if item.handle == int(self._foreground_handle):
                    return replace(item, foreground=True)
            return None
        for item in self._windows:
            if item.foreground:
                return item
        return self._windows[0] if self._windows else None

    def _with_element_state(
        self, window: WindowInfo, include_elements: bool
    ) -> WindowInfo:
        if not include_elements:
            return replace(window, elements_supported=False, element_count=0)
        data = self._elements.get(window.handle)
        if self._elements_supported is not None and not self._elements_supported:
            return replace(window, elements_supported=False, element_count=0)
        count = len(data) if data else 0
        return replace(
            window,
            elements_supported=bool(count),
            element_count=count,
        )


def _coerce_window(value: WindowInfo | Mapping[str, Any]) -> WindowInfo:
    if isinstance(value, WindowInfo):
        return value
    data = dict(value)
    bounds = data.get("bounds")
    if bounds is not None:
        bounds = tuple(int(part) for part in bounds)
    return WindowInfo(
        handle=int(data.get("handle", 0)),
        title=str(data.get("title", "") or ""),
        process_name=str(data.get("process_name", "") or ""),
        executable_path=str(data.get("executable_path", "") or ""),
        pid=data.get("pid"),
        foreground=bool(data.get("foreground", False)),
        fullscreen=bool(data.get("fullscreen", False)),
        visible=bool(data.get("visible", True)),
        minimized=bool(data.get("minimized", False)),
        bounds=bounds,
        elements_supported=bool(data.get("elements_supported", False)),
        element_count=int(data.get("element_count", 0) or 0),
        process_error=str(data.get("process_error", "") or ""),
        fullscreen_error=str(data.get("fullscreen_error", "") or ""),
    )


def _coerce_element(value: ElementInfo | Mapping[str, Any]) -> ElementInfo:
    if isinstance(value, ElementInfo):
        return value
    data = dict(value)
    bounds = data.get("bounds")
    if bounds is not None:
        bounds = tuple(int(part) for part in bounds)
    control_id = data.get("control_id")
    return ElementInfo(
        handle=int(data.get("handle", 0)),
        class_name=str(data.get("class_name", "") or ""),
        control_id=int(control_id) if control_id is not None else None,
        enabled=bool(data.get("enabled", True)),
        visible=bool(data.get("visible", True)),
        bounds=bounds,
    )


def build_observation_tools(
    principal: str,
    *,
    metadata: Optional[Mapping[str, object]] = None,
    policy: Optional[ObservationPolicy] = None,
    observer: Optional[WindowObserver] = None,
    audit: Optional[AutomationAudit] = None,
    gate: Optional[ObservationGate] = None,
    max_windows: int = DEFAULT_MAX_WINDOWS,
    max_elements: int = DEFAULT_MAX_ELEMENTS,
) -> List[Tool]:
    """返回只读观察工具；管理员关闭或非人类主体时返回空列表。

    工具只有四个：采集、关闭、打开、状态。没有任何点击/输入入口。
    """

    policy = policy or get_observation_policy()
    decision = (gate or ObservationGate(policy)).evaluate(
        principal, metadata=metadata
    )
    if not decision.allowed:
        return []

    if observer is None:
        from .windows_observer import WindowsObserver

        observer = WindowsObserver()
    service = ObservationService(
        observer,
        policy=policy,
        audit=audit if audit is not None else AutomationAudit(),
        max_windows=max_windows,
        max_elements=max_elements,
    )

    def _json(payload: Dict[str, Any]) -> str:
        return json.dumps(payload, ensure_ascii=False, default=str)

    def capture(
        scope: str = TargetScope.WINDOW.value,
        include_elements: bool = True,
        **kwargs: Any,
    ) -> str:
        """读取前台窗口标题、进程名、可执行路径、全屏状态与元素清单。"""
        try:
            resolved = TargetScope(str(scope or kwargs.get("scope") or "window"))
        except ValueError:
            return _json(
                {
                    "status": "error",
                    "error": "invalid_scope",
                    "message": f"未知范围: {scope}",
                }
            )
        try:
            observation = service.observe(
                principal=principal,
                scope=resolved,
                include_elements=bool(include_elements),
            )
        except AutomationError as exc:
            return _json(
                {"status": "error", "error": exc.reason, "message": str(exc)}
            )
        except Exception as exc:  # noqa: BLE001 - 失败要如实回显，不伪造结果
            return _json(
                {
                    "status": "error",
                    "error": "observation_failed",
                    "message": f"{type(exc).__name__}: {exc}"[:200],
                }
            )
        return _json({"status": "ok", "observation": observation.to_dict()})

    def disable(reason: str = "user_disabled", **kwargs: Any) -> str:
        """一键关闭只读观察（本进程内立即生效）。"""
        snapshot = policy.disable(str(reason or kwargs.get("reason") or "user_disabled"))
        return _json({"status": "ok", "observation_policy": snapshot})

    def enable() -> str:
        """重新打开只读观察。管理员级环境变量关闭时此工具不会注册。"""
        return _json({"status": "ok", "observation_policy": policy.enable()})

    def status() -> str:
        """返回观察开关、节流配额与准入判决。"""
        return _json(
            {
                "status": "ok",
                "observation_policy": policy.snapshot(),
                "gate": decision.as_dict(),
                "backend": service.backend,
            }
        )

    return [
        Tool(
            tool_id="liuhao.automation.observation.capture",
            name="automation.observation.capture",
            version="1.0.0",
            description="只读观察前台窗口（标题/进程/路径/全屏）与元素清单，不注入任何输入",
            capability="automation.observation.capture",
            schema={
                "type": "object",
                "properties": {
                    "scope": {
                        "type": "string",
                        "enum": [item.value for item in TargetScope],
                        "default": TargetScope.WINDOW.value,
                    },
                    "include_elements": {"type": "boolean", "default": True},
                },
            },
            fn=capture,
            risk="LOW",
        ),
        Tool(
            tool_id="liuhao.automation.observation.disable",
            name="automation.observation.disable",
            version="1.0.0",
            description="一键关闭只读观察（立即生效）",
            capability="automation.observation.disable",
            schema={
                "type": "object",
                "properties": {"reason": {"type": "string", "default": "user_disabled"}},
            },
            fn=disable,
            risk="LOW",
        ),
        Tool(
            tool_id="liuhao.automation.observation.enable",
            name="automation.observation.enable",
            version="1.0.0",
            description="重新打开只读观察",
            capability="automation.observation.enable",
            schema={"type": "object", "properties": {}},
            fn=enable,
            risk="LOW",
        ),
        Tool(
            tool_id="liuhao.automation.observation.status",
            name="automation.observation.status",
            version="1.0.0",
            description="查询只读观察的开关、节流配额与准入状态",
            capability="automation.observation.status",
            schema={"type": "object", "properties": {}},
            fn=status,
            risk="LOW",
        ),
    ]


__all__ = [
    "DEFAULT_MAX_ELEMENTS",
    "DEFAULT_MAX_WINDOWS",
    "ELEMENTS_UNSUPPORTED_REASON",
    "FakeWindowObserver",
    "OBSERVE_ENV",
    "OBSERVATION_RATE_LIMIT_MAX",
    "OBSERVATION_RATE_LIMIT_WINDOW_SECONDS",
    "ObservationGate",
    "ObservationGateDecision",
    "ObservationPolicy",
    "ObservationService",
    "WindowObserver",
    "build_observation_tools",
    "get_observation_policy",
    "observation_enabled",
]
