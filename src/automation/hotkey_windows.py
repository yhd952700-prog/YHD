"""Windows 侧真实全局热键注册器（pywin32），**默认不启用**。

开关是 ``LIUHAO_AUTOMATION_HOTKEY_ENABLED``，缺省/非真值一律视为关闭：
不允许因为"模块被 import 了"就偷偷占用用户的全局热键。

诚实性要求：
* ``RegisterHotKey`` 失败（典型是热键已被别的软件占用，Win32 错误 1409）时
  必须返回 ``False``，并把错误写进 ``last_error``；
* 线程/消息循环起不来时同样返回 ``False``，不留下一个"看起来已注册"的状态；
* ``unregister`` 必须真正调用 ``UnregisterHotKey``。
"""

from __future__ import annotations

import os
import threading
import time
from typing import Dict, Optional, Sequence, Tuple

HOTKEY_ENV = "LIUHAO_AUTOMATION_HOTKEY_ENABLED"
_TRUE_VALUES = {"1", "true", "yes", "on"}

# Win32 常量（避免在模块 import 时就依赖 pywin32）。
WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
_MODIFIERS: Dict[str, int] = {
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
    "windows": MOD_WIN,
}
_SPECIAL_KEYS: Dict[str, int] = {
    "esc": 0x1B,
    "escape": 0x1B,
    "enter": 0x0D,
    "return": 0x0D,
    "space": 0x20,
    "tab": 0x09,
    "backspace": 0x08,
    "delete": 0x2E,
    "insert": 0x2D,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "pagedown": 0x22,
    "up": 0x26,
    "down": 0x28,
    "left": 0x25,
    "right": 0x27,
}
for _index in range(1, 25):
    _SPECIAL_KEYS[f"f{_index}"] = 0x6F + _index

#: 注册用的热键 id（进程内唯一即可；固定值便于排障）。
DEFAULT_HOTKEY_ID = 0x4C48  # "LH"


def hotkey_enabled(env: Optional[Dict[str, str]] = None) -> bool:
    """默认关闭：只有显式真值才返回 True。"""

    source = os.environ if env is None else env
    return str(source.get(HOTKEY_ENV, "")).strip().lower() in _TRUE_VALUES


def parse_combo(combo: Sequence[str]) -> Tuple[int, int]:
    """把 ``("ctrl", "alt", "esc")`` 解析成 ``(modifiers, virtual_key)``。"""

    tokens = [str(token).strip().lower() for token in combo if str(token).strip()]
    if not tokens:
        raise ValueError("empty_hotkey_combo")
    modifiers = 0
    keys: list[str] = []
    for token in tokens:
        if token in _MODIFIERS:
            modifiers |= _MODIFIERS[token]
        else:
            keys.append(token)
    if len(keys) != 1:
        raise ValueError(f"hotkey_combo_requires_exactly_one_key:{'+'.join(tokens)}")
    if modifiers == 0:
        raise ValueError(f"hotkey_requires_modifier:{'+'.join(tokens)}")
    key = keys[0]
    if key in _SPECIAL_KEYS:
        return modifiers, _SPECIAL_KEYS[key]
    if len(key) == 1 and key.isalnum() and key.isascii():
        return modifiers, ord(key.upper())
    raise ValueError(f"unsupported_hotkey_key:{key}")


class Win32HotkeyBackend:
    """pywin32 的最小封装；单测可注入替身验证失败路径，不碰真实全局热键。"""

    def __init__(self) -> None:
        try:
            import win32con
            import win32gui
        except ImportError as exc:  # pragma: no cover - 只可能在没有 pywin32 的机器上
            raise RuntimeError("pywin32_unavailable") from exc
        self._con = win32con
        self._gui = win32gui

    def register(self, hotkey_id: int, modifiers: int, virtual_key: int) -> None:
        # pywin32 在失败时抛 pywintypes.error（例如 1409 = 已被占用）。
        self._gui.RegisterHotKey(0, hotkey_id, modifiers, virtual_key)

    def unregister(self, hotkey_id: int) -> None:
        self._gui.UnregisterHotKey(0, hotkey_id)

    def peek(self) -> Optional[tuple]:
        """非阻塞取一条线程消息；无消息返回 None。"""

        ret, message = self._gui.PeekMessage(None, 0, 0, self._con.PM_REMOVE)
        return message if ret else None


class WindowsHotkeyRegistrar:
    """在独立线程里注册全局热键并跑消息循环；触发时调用 ``on_trigger``。"""

    def __init__(
        self,
        *,
        on_trigger: Optional[object] = None,
        backend: Optional[object] = None,
        hotkey_id: int = DEFAULT_HOTKEY_ID,
        ready_timeout: float = 3.0,
        poll_interval: float = 0.02,
    ) -> None:
        self._on_trigger = on_trigger
        self._backend = backend
        self._hotkey_id = int(hotkey_id)
        self._ready_timeout = float(ready_timeout)
        self._poll_interval = float(poll_interval)
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop: Optional[threading.Event] = None
        self._ready: Optional[threading.Event] = None
        self._registered = False
        self._combo = ""
        self._error = ""
        self._trigger_error = ""
        self._unregister_error = ""

    # ---------------------------------------------------------------- 接口
    def register(self, combo: Sequence[str]) -> bool:
        with self._lock:
            if self._registered:
                return True
            if self._thread is not None and self._thread.is_alive():
                self._error = "hotkey_registration_in_progress"
                return False

        try:
            modifiers, virtual_key = parse_combo(combo)
        except ValueError as exc:
            with self._lock:
                self._error = str(exc)
            return False

        stop = threading.Event()
        ready = threading.Event()
        with self._lock:
            self._stop = stop
            self._ready = ready
            self._combo = "+".join(str(token).strip().lower() for token in combo)
            self._error = ""
            self._unregister_error = ""
            self._trigger_error = ""

        thread = threading.Thread(
            target=self._loop,
            args=(stop, ready, modifiers, virtual_key),
            name="liuhao-automation-hotkey",
            daemon=True,
        )
        with self._lock:
            self._thread = thread
        thread.start()

        if not ready.wait(self._ready_timeout):
            with self._lock:
                self._error = "hotkey_registration_timeout"
            stop.set()
            self._join(thread)
            return False

        with self._lock:
            ok = self._registered
            if not ok and not self._error:
                self._error = "hotkey_registration_failed"
        if not ok:
            stop.set()
            self._join(thread)
        return ok

    def unregister(self) -> None:
        with self._lock:
            stop = self._stop
            thread = self._thread
        if stop is not None:
            stop.set()
        if thread is not None and thread is not threading.current_thread():
            self._join(thread)
        with self._lock:
            if thread is not None and thread.is_alive():
                self._unregister_error = "hotkey_thread_still_running"
            self._thread = None
            self._registered = False

    @property
    def registered(self) -> bool:
        return self._registered

    @property
    def last_error(self) -> str:
        return self._error or self._unregister_error

    @property
    def trigger_error(self) -> str:
        return self._trigger_error

    # ---------------------------------------------------------------- 内部
    def _join(self, thread: threading.Thread) -> None:
        thread.join(timeout=max(1.0, self._ready_timeout))

    def _loop(
        self,
        stop: threading.Event,
        ready: threading.Event,
        modifiers: int,
        virtual_key: int,
    ) -> None:
        backend = None
        try:
            backend = self._backend or Win32HotkeyBackend()
            backend.register(self._hotkey_id, modifiers, virtual_key)
            with self._lock:
                self._registered = True
            ready.set()
            while not stop.is_set():
                message = backend.peek()
                if message is None:
                    time.sleep(self._poll_interval)
                    continue
                msg_id = message[1] if len(message) > 1 else None
                if msg_id == WM_HOTKEY:
                    self._fire()
        except Exception as exc:  # noqa: BLE001 - 注册失败必须原样上报
            with self._lock:
                self._error = f"{type(exc).__name__}: {exc}"[:200]
            ready.set()
        finally:
            if backend is not None:
                try:
                    backend.unregister(self._hotkey_id)
                except Exception as exc:  # noqa: BLE001 - 释放失败要留痕
                    with self._lock:
                        self._unregister_error = (
                            f"unregister_failed:{type(exc).__name__}: {exc}"
                        )[:200]
            with self._lock:
                self._registered = False
            ready.set()

    def _fire(self) -> None:
        callback = self._on_trigger
        if not callable(callback):
            return
        try:
            callback()
        except Exception as exc:  # noqa: BLE001 - 回调失败不能终止监听线程
            with self._lock:
                self._trigger_error = f"{type(exc).__name__}: {exc}"[:200]


def configure_from_env(
    estop: Optional[object] = None,
    *,
    env: Optional[Dict[str, str]] = None,
) -> Optional[object]:
    """按环境变量决定是否接入真实注册器；关闭时返回 None 且不做任何注册。

    打开后会尝试默认组合，失败结果保留在 ``estop.registration`` 里
    （含每个组合的失败原因），绝不把失败改写成成功。
    """

    if not hotkey_enabled(env):
        return None
    from .estop import get_emergency_stop

    target = estop or get_emergency_stop()
    existing = getattr(target, "registration", None)
    if existing is not None and existing.registered:
        return existing
    registrar = WindowsHotkeyRegistrar(on_trigger=lambda: target.trigger("hotkey"))
    if not target.set_registrar(registrar):
        return target.registration
    return target.register()
