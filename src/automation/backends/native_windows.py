"""Windows 真后端（0.4.6，5-3 电脑操控一期·安全版）。

只做「看」和「开」，不做自由键鼠/自由命令：

* 看：复用已有的 :class:`src.automation.windows_observer.WindowsObserver`
 （pywin32 读窗口，PSF 许可）+ ``uiautomation``（Apache-2.0）读前台窗口的
  控件结构（只读结构，不读控件文本）。
* 开：``pywinauto``（BSD-3-Clause）做聚焦 / 关闭窗口；打开程序只允许
  白名单里的三个常用程序（记事本 / 文件资源管理器 / 默认浏览器），且只
  按程序名启动，**不接受任意路径、任意命令行**。

诚实边界（对外报告必须照这个口径写）：

* 本机是 Linux 或缺依赖时，:meth:`NativeWindowsBackend._require_windows`
  直接抛 ``AutomationError("native_backend_unavailable")``，绝不返回假的
  窗口列表、也不假装打开成功。真 Windows 实测见
  ``docs/d-group-0.4.6.md``「只能在真 Windows 验」一节。
* ``pywinauto`` / ``uiautomation`` 都是函数内懒加载：Linux 上 import 本
  模块不报错，CI 不装 Windows 依赖也能跑。
* 打开浏览器只负责「把浏览器窗口带到前台」，不往地址栏输网址、不点页面；
  要开指定网页走联网/浏览器技能那条线，不走这里。
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any, Dict, Optional

from ..contracts import (
    ActionCapability,
    ActionRequest,
    ActionResult,
    AutomationBackend,
    AutomationError,
    BackendKind,
    Observation,
    Reversibility,
    TargetScope,
)
from ..observe import ObservationService, get_observation_policy
from ..windows_observer import WindowsObserver

#: 允许打开的程序（只按名，不接受路径/参数）。键是动作里传的 ``app``，
#: 值是 ``(可执行名, 启动方式)``；启动方式只用 ``start`` + 裸程序名。
ALLOWED_APPS: Dict[str, Dict[str, str]] = {
    # 记事本：最小验证用（打开 → 前台能看到 → 关闭）。
    "notepad": {"exe": "notepad.exe", "label": "记事本"},
    # 文件资源管理器：explorer.exe 直接起会打开一个新窗口。
    "explorer": {"exe": "explorer.exe", "label": "文件资源管理器"},
    # 默认浏览器：用系统默认浏览器打开一个空白页（不指定网址）。
    "browser": {"exe": "", "label": "默认浏览器"},
}

#: 本轮开放的动作（白名单之外一律 ``unsupported_action``）。
ALLOWED_ACTIONS = frozenset({"open", "focus", "close", "foreground", "list_windows"})

#: 可逆动作：focus / foreground 只是换焦点，执行前记下原焦点即可恢复；
#: open / close 不可逆（关掉的窗口找不回来），必须在确认前告知。
_REVERSIBLE_ACTIONS = frozenset({"focus", "foreground"})


class NativeWindowsBackend(AutomationBackend):
    """Windows 真后端。``kind`` 为 ``native``。"""

    kind = BackendKind.NATIVE

    def __init__(
        self,
        *,
        observer: Optional[WindowsObserver] = None,
        allowed_apps: Optional[Dict[str, Dict[str, str]]] = None,
    ) -> None:
        self._observer = observer
        self._allowed_apps = dict(allowed_apps) if allowed_apps is not None else dict(ALLOWED_APPS)
        self._lock = threading.Lock()
        self._focus: Optional[str] = None
        self._revision = 0
        self._undo: Dict[tuple, Optional[str]] = {}

    # ------------------------------------------------------------------ 看
    def _require_windows(self) -> None:
        """非 Windows 或缺依赖时如实失败，不伪造结果。"""
        if os.name != "nt":
            raise AutomationError(
                "native_backend_unavailable",
                "电脑操控真后端只在 Windows 上可用（本机是 Linux，未执行任何操作）",
            )

    def _observer_or_raise(self) -> WindowsObserver:
        self._require_windows()
        if self._observer is None:
            self._observer = WindowsObserver()
        return self._observer

    def observe(self, scope: TargetScope = TargetScope.SCREEN) -> Observation:
        observer = self._observer_or_raise()
        # 复用只读观察的节流/审计（与 FakeWindowObserver 同一条管线）。
        service = ObservationService(observer, policy=get_observation_policy())
        observation = service.observe(principal="native-backend", scope=scope)
        with self._lock:
            foreground = observation.foreground
            self._focus = (
                (foreground.process_name or foreground.title) if foreground else None
            )
            self._revision += 1
        return observation

    def read_foreground_elements(self) -> Dict[str, Any]:
        """用 uiautomation 只读前台窗口的控件结构（不读控件文本）。

        返回 ``{"supported": bool, "count": int, "reason": str}``；读不到就
        如实报原因，不编造元素。控件文本刻意不读（可能是用户输入/聊天内容）。
        """
        self._require_windows()
        try:
            import uiautomation as auto  # type: ignore[import-not-found]
        except Exception as exc:  # noqa: BLE001 - 缺依赖如实上报
            return {
                "supported": False,
                "count": 0,
                "reason": f"uiautomation_unavailable:{type(exc).__name__}",
            }
        try:
            control = auto.GetForegroundControl()
            if control is None:
                return {"supported": False, "count": 0, "reason": "no_foreground_control"}
            children = list(control.GetChildren() or [])
            return {
                "supported": True,
                "count": len(children),
                "reason": "",
                "control_types": sorted(
                    {str(child.ControlTypeName or "") for child in children if child}
                )[:20],
            }
        except Exception as exc:  # noqa: BLE001 - 读不到如实报
            return {
                "supported": False,
                "count": 0,
                "reason": f"uiautomation_read_failed:{type(exc).__name__}",
            }

    # ------------------------------------------------------------------ 能力
    def capability(self, request: ActionRequest) -> ActionCapability:
        action = str(request.action or "").strip().lower()
        if action not in ALLOWED_ACTIONS:
            return ActionCapability(
                action=action or "<empty>",
                reversibility=Reversibility.IRREVERSIBLE,
                reason="unsupported_action",
            )
        if action in _REVERSIBLE_ACTIONS:
            return ActionCapability(
                action=action,
                reversibility=Reversibility.REVERSIBLE,
                reason="focus_snapshot_available",
            )
        return ActionCapability(
            action=action,
            reversibility=Reversibility.IRREVERSIBLE,
            reason="open_or_close_cannot_be_undone",
        )

    # ------------------------------------------------------------------ 做
    def execute(self, request: ActionRequest) -> ActionResult:
        self._require_windows()
        action = str(request.action or "").strip().lower()
        if action not in ALLOWED_ACTIONS:
            return self._failed(request, "unsupported_action")
        with self._lock:
            self._undo[(request.session_id, request.step_no)] = self._focus
            if action == "list_windows":
                try:
                    observation = self.observe(TargetScope.SCREEN)
                except AutomationError as exc:
                    return self._failed(request, exc.reason)
                return self._executed(request, "windows_listed", observation)
            if action == "foreground":
                try:
                    observation = self.observe(TargetScope.WINDOW)
                except AutomationError as exc:
                    return self._failed(request, exc.reason)
                return self._executed(request, "foreground_read", observation)
            if action == "open":
                return self._open(request)
            if action == "focus":
                return self._focus_window(request)
            if action == "close":
                return self._close_window(request)
            return self._failed(request, "unsupported_action")

    def _open(self, request: ActionRequest) -> ActionResult:
        app = str(request.params.get("app") or request.target_ref or "").strip().lower()
        spec = self._allowed_apps.get(app)
        if spec is None:
            return self._failed(request, "app_not_allowed")
        try:
            import subprocess

            if app == "browser":
                # 默认浏览器空白页：不指定网址，不往地址栏输任何东西。
                os.startfile("about:blank")  # noqa: S606 - 白名单内的固定值
            else:
                subprocess.Popen(  # noqa: S603 - 白名单内的裸程序名，无参数
                    [spec["exe"]],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        except Exception as exc:  # noqa: BLE001 - 启动失败如实报
            return self._failed(request, f"open_failed:{type(exc).__name__}")
        self._focus = spec["label"]
        return self._executed(request, "app_opened", self._observe_best_effort())

    def _pywinauto(self) -> Any:
        try:
            from pywinauto import Application  # type: ignore[import-not-found]
        except Exception as exc:  # noqa: BLE001
            raise AutomationError(
                "native_backend_unavailable",
                f"pywinauto 不可用：{type(exc).__name__}（未执行任何操作）",
            ) from exc
        return Application

    def _focus_window(self, request: ActionRequest) -> ActionResult:
        title = str(request.params.get("window_title") or request.target_ref or "").strip()
        if not title:
            return self._failed(request, "focus_target_missing")
        try:
            Application = self._pywinauto()
            app = Application(backend="uia").connect(title_re=f".*{_regex_escape(title)}.*")
            window = app.top_window()
            window.set_focus()
        except AutomationError:
            raise
        except Exception as exc:  # noqa: BLE001 - 找不到窗口如实报
            return self._failed(request, f"focus_failed:{type(exc).__name__}")
        self._focus = title
        return self._executed(request, "focus_applied", self._observe_best_effort())

    def _close_window(self, request: ActionRequest) -> ActionResult:
        title = str(request.params.get("window_title") or request.target_ref or "").strip()
        if not title:
            return self._failed(request, "close_target_missing")
        try:
            Application = self._pywinauto()
            app = Application(backend="uia").connect(title_re=f".*{_regex_escape(title)}.*")
            window = app.top_window()
            window.close()
        except AutomationError:
            raise
        except Exception as exc:  # noqa: BLE001 - 关不掉如实报
            return self._failed(request, f"close_failed:{type(exc).__name__}")
        self._focus = None
        return self._executed(request, "window_closed", self._observe_best_effort())

    def rollback(self, *, request: ActionRequest, result: ActionResult) -> ActionResult:
        """只恢复焦点（focus/foreground）；open/close 不可逆，如实拒绝。"""
        self._require_windows()
        with self._lock:
            if not result.ok:
                return self._failed(request, "rollback_of_failed_action")
            if str(request.action or "").strip().lower() not in _REVERSIBLE_ACTIONS:
                return self._failed(request, "action_not_reversible")
            key = (request.session_id, request.step_no)
            previous = self._undo.pop(key, None)
            if previous is None:
                return self._failed(request, "rollback_not_available")
            self._focus = previous
            self._revision += 1
            return self._executed(request, "rolled_back", self._observe_best_effort())

    # ---------------------------------------------------------------- helpers
    def _observe_best_effort(self) -> Optional[Observation]:
        try:
            return self._observer_or_raise().observe(TargetScope.WINDOW)
        except Exception:  # noqa: BLE001 - 观察失败不掩盖执行结果
            return Observation(
                focus=self._focus, scope=TargetScope.WINDOW, captured_at=time.time()
            )

    def _executed(
        self, request: ActionRequest, detail: str, observation: Optional[Observation] = None
    ) -> ActionResult:
        self._revision += 1
        return ActionResult(
            status="executed",
            action=request.action,
            ok=True,
            observation=observation or self._observe_best_effort(),
            detail={"detail": detail, "revision": self._revision, "backend": "native"},
        )

    def _failed(self, request: ActionRequest, error: str) -> ActionResult:
        return ActionResult(
            status="failed",
            action=request.action,
            ok=False,
            error=error,
            observation=self._observe_best_effort(),
        )


def _regex_escape(text: str) -> str:
    """标题做正则转义：用户输入的标题不能变成正则注入。"""
    import re

    return re.escape(text)


__all__ = ["ALLOWED_ACTIONS", "ALLOWED_APPS", "NativeWindowsBackend"]
