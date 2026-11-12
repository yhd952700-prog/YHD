"""急停：全局热键注册、取消令牌、修饰键残留清理。

热键注册通过可注入的 ``registrar`` 完成：本轮不引入任何真实键鼠/GPL 依赖。
注册失败时必须尝试备用组合并如实上报，绝不假装注册成功。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Protocol, Sequence, Tuple

DEFAULT_HOTKEYS: Tuple[Tuple[str, ...], ...] = (
    ("ctrl", "alt", "esc"),
    ("ctrl", "shift", "esc"),
    ("ctrl", "alt", "f12"),
)

MODIFIER_KEYS: Tuple[str, ...] = ("ctrl", "alt", "shift", "win")

_estop_lock = threading.Lock()
_estop: Optional["EmergencyStop"] = None


def get_emergency_stop() -> "EmergencyStop":
    """进程级急停单例（工具与会话共享同一个取消令牌）。"""

    global _estop
    if _estop is None:
        with _estop_lock:
            if _estop is None:
                _estop = EmergencyStop()
    return _estop


@dataclass(frozen=True)
class HotkeyRegistration:
    """热键注册结果。``registered=False`` 时 ``reason`` 必须说明原因。"""

    registered: bool
    combo: Optional[str] = None
    attempts: Tuple[Dict[str, object], ...] = ()
    reason: str = ""


class HotkeyRegistrar(Protocol):
    """真实热键注册器的接口。

    实现方必须如实返回注册结果：占用/无权限/平台不支持都应返回 ``False``，
    绝不能假装已经注册。``unregister`` 负责释放全局热键。
    """

    def register(self, combo: Sequence[str]) -> bool:
        ...

    def unregister(self) -> None:
        ...


class EmergencyStop:
    """急停开关。

    ``registrar``：既可以是 ``HotkeyRegistrar`` 对象，也可以是
    ``registrar(combo) -> bool`` 的可调用对象（兼容第 1 批的用法）。
    ``modifier_releaser(key)``：释放单个修饰键，返回 None，失败时抛异常。
    两者默认均为 None——此时必须如实报告"未配置"，不能伪造已注册。
    """

    def __init__(
        self,
        *,
        registrar: Optional[object] = None,
        modifier_releaser: Optional[Callable[[str], None]] = None,
        hotkeys: Iterable[Sequence[str]] = DEFAULT_HOTKEYS,
    ) -> None:
        self._registrar = registrar
        self._releaser = modifier_releaser
        self._hotkeys = tuple(tuple(str(k) for k in combo) for combo in hotkeys)
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._registration: Optional[HotkeyRegistration] = None
        self._triggered_at: Optional[float] = None

    # ---------------------------------------------------------------- 热键
    def set_registrar(self, registrar: object, *, replace: bool = False) -> bool:
        """注入/替换注册器。已成功注册时默认拒绝覆盖，避免悄悄换掉热键。"""

        with self._lock:
            if registrar is self._registrar:
                return True
            if self._registration is not None and self._registration.registered and not replace:
                return False
            self._registrar = registrar
            self._registration = None
            return True

    def _call_register(self, combo: Sequence[str]) -> bool:
        method = getattr(self._registrar, "register", None)
        if callable(method):
            return bool(method(combo))
        return bool(self._registrar(combo))  # type: ignore[operator]

    def register(self) -> HotkeyRegistration:
        with self._lock:
            if self._registration is not None and self._registration.registered:
                return self._registration
        attempts: List[Dict[str, object]] = []
        if self._registrar is None:
            registration = HotkeyRegistration(
                registered=False,
                attempts=(),
                reason="no_hotkey_registrar_configured",
            )
            self._registration = registration
            return registration

        for combo in self._hotkeys:
            label = "+".join(combo)
            try:
                ok = self._call_register(combo)
            except Exception as exc:  # noqa: BLE001 - 注册失败要上报，不能吞
                message = f"{type(exc).__name__}: {exc}"[:200]
                attempts.append({"combo": label, "ok": False, "error": message})
                continue
            attempts.append({"combo": label, "ok": ok})
            if ok:
                registration = HotkeyRegistration(
                    registered=True,
                    combo=label,
                    attempts=tuple(attempts),
                    reason="registered",
                )
                self._registration = registration
                return registration

        registration = HotkeyRegistration(
            registered=False,
            attempts=tuple(attempts),
            reason="all_combinations_unavailable",
        )
        self._registration = registration
        return registration

    def unregister(self) -> Dict[str, object]:
        """释放已注册的热键，并如实报告是否真的释放成功。"""

        with self._lock:
            registrar = self._registrar
            registration = self._registration
            if registrar is None or registration is None or not registration.registered:
                return {"unregistered": False, "reason": "not_registered"}
            self._registration = None
        release = getattr(registrar, "unregister", None)
        if not callable(release):
            return {
                "unregistered": False,
                "reason": "registrar_cannot_unregister",
                "combo": registration.combo,
            }
        try:
            release()
        except Exception as exc:  # noqa: BLE001 - 释放失败也要如实上报
            with self._lock:
                self._registration = registration
            return {
                "unregistered": False,
                "reason": f"unregister_failed:{type(exc).__name__}",
                "error": str(exc)[:200],
                "combo": registration.combo,
            }
        return {"unregistered": True, "reason": "unregistered", "combo": registration.combo}

    @property
    def registration(self) -> Optional[HotkeyRegistration]:
        return self._registration

    # ---------------------------------------------------------------- 急停
    def trigger(self, reason: str = "manual") -> Dict[str, object]:
        with self._lock:
            self._event.set()
            self._triggered_at = time.time()
        return {
            "stopped": True,
            "reason": str(reason),
            "modifiers": self.clear_modifiers(),
            "triggered_at": self._triggered_at,
        }

    @property
    def active(self) -> bool:
        return self._event.is_set()

    def reset(self) -> None:
        with self._lock:
            self._event.clear()
            self._triggered_at = None

    def wait(self, timeout: Optional[float] = None) -> bool:
        """阻塞等待急停触发（可被取消令牌复用）。"""

        return self._event.wait(timeout)

    # ---------------------------------------------------------------- 修饰键
    def clear_modifiers(self) -> Dict[str, object]:
        if self._releaser is None:
            return {
                "supported": False,
                "released": [],
                "failures": [],
                "reason": "no_modifier_releaser_configured",
            }

        released: List[str] = []
        failures: List[Dict[str, str]] = []
        for key in MODIFIER_KEYS:
            try:
                self._releaser(key)
                released.append(key)
            except Exception as exc:  # noqa: BLE001 - 逐键上报
                failures.append({"key": key, "error": type(exc).__name__})
        return {"supported": True, "released": released, "failures": failures}
