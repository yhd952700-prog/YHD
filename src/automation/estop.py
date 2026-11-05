"""急停：全局热键注册、取消令牌、修饰键残留清理。

热键注册通过可注入的 ``registrar`` 完成：本轮不引入任何真实键鼠/GPL 依赖。
注册失败时必须尝试备用组合并如实上报，绝不假装注册成功。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

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


class EmergencyStop:
    """急停开关。

    ``registrar(combo) -> bool``：尝试注册一组组合键，返回是否成功。
    ``modifier_releaser(key)``：释放单个修饰键，返回 None，失败时抛异常。
    两者默认均为 None——此时必须如实报告"未配置"，不能伪造已注册。
    """

    def __init__(
        self,
        *,
        registrar: Optional[Callable[[Sequence[str]], bool]] = None,
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
    def register(self) -> HotkeyRegistration:
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
                ok = bool(self._registrar(combo))
            except Exception as exc:  # noqa: BLE001 - 注册失败要上报，不能吞
                attempts.append({"combo": label, "ok": False, "error": type(exc).__name__})
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
