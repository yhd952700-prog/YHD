"""修复面板：六类故障各一个可点动作，结果带证据、回退与日志位置。

与 ``repair.py`` 的分工：本模块只负责界面与线程调度，不直接改本机状态；
每个动作真实做了什么由 ``repair`` 里的函数决定，界面只展示它的返回值。

线程模型与状态窗口一致：动作在后台线程执行，结果先进 ``queue``，由 Tk 主线程
（``after`` 轮询）消费并更新控件 —— 绝不在子线程里碰 tkinter。
"""

from __future__ import annotations

import contextlib
import queue
import threading
import tkinter as tk
from collections.abc import Callable
from tkinter import filedialog, messagebox
from typing import Any

from . import dpi, repair

_ROW_SPECS = (
    (
        "port",
        "1. 端口被占用",
        "停掉当前网关 → 从该端口起找第一个可绑定端口 → 原地重启。重复点击会停在同一个"
        "可用端口，不会一路往上漂；地址变化会写在结果里。",
        "换端口并重启",
    ),
    (
        "directory",
        "2. 数据 / 日志目录不可写",
        "先对候选目录做真实写入探测，通过后才写入配置并重启网关；探测失败则什么都不改。",
        "改数据目录…",
    ),
    (
        "model_key",
        "3. 模型 Key 无效",
        "打开「系统设置」页，并把模型名放进剪贴板。当前构建产物没有“定位到某个模型"
        "编辑框”的深链，这一步不能自动聚焦 Key 输入框（不假装已聚焦）。",
        "打开模型设置",
    ),
    (
        "network",
        "4. 网络 / DNS / TCP 不通",
        "对已配置的模型端点做 DNS → TCP → HTTP 三步实测，指出哪个域名、哪一步失败。"
        "只发一次不带任何凭据的请求，不会触发模型计费。",
        "只测连接",
    ),
    (
        "database",
        "5. 本地库损坏",
        "把损坏的库移到 recovered-<时间戳> 目录，再从 .bak 恢复或重建空库，"
        "恢复后跑 quick_check 并重启网关。会丢：该库里原有的数据（原件保留在上述目录）。",
        "从备份恢复",
    ),
    (
        "assets",
        "6. 静态资源缺失（白屏）",
        "从安装包 payload 或本机构建产物补齐缺失文件，只新增、不覆盖已有文件；"
        "补齐后重新检查引用与 HTTP。找不到可展开的副本时会明确提示重新安装。",
        "重新展开前端资源",
    ),
)


class RepairDialog:
    """六类故障的修复入口（模态以外：不阻塞状态窗口）。"""

    def __init__(self, app: Any) -> None:
        self._app = app
        self._queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._rows: dict[str, dict[str, Any]] = {}
        self._status: dict[str, Any] = {}
        self._closed = False

        self.window = tk.Toplevel(app._root)
        self.window.title("一键修复")
        self.window.transient(app._root)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.minsize(560, 420)

        outer = tk.Frame(self.window, padx=14, pady=12)
        outer.pack(fill=tk.BOTH, expand=True)
        tk.Label(
            outer,
            text="每个动作都会真实改动本机，并写入 repair.log（已脱敏）。服务没起来时同样可用。",
            anchor="w",
            justify=tk.LEFT,
            wraplength=600,
            fg="#444444",
        ).pack(anchor="w", pady=(0, 8))

        self._scroll = dpi.ScrollableFrame(outer)
        self._scroll.frame.pack(fill=tk.BOTH, expand=True)
        content = self._scroll.inner

        for key, title, description, label in _ROW_SPECS:
            self._rows[key] = self._build_row(content, key, title, description, label)
        # 数据库这一行额外给「重建空库」入口：两条路都要求二次确认。
        extra = tk.Frame(content)
        extra.pack(anchor="w", padx=(12, 0), pady=(0, 8))
        tk.Button(
            extra,
            text="重建空库（危险）",
            command=lambda: self._run_database(rebuild=True),
        ).pack(side=tk.LEFT)
        tk.Label(
            extra,
            text="找不到备份时用这个；原件同样会保留到 recovered-<时间戳>。",
            fg="#666666",
        ).pack(side=tk.LEFT, padx=(8, 0))

        footer = tk.Frame(outer)
        footer.pack(fill=tk.X, pady=(10, 0))
        tk.Button(footer, text="重新检测", command=self._load_status).pack(side=tk.LEFT)
        tk.Button(footer, text="关闭", command=self.close).pack(side=tk.RIGHT)

        self.window.after(60, self._fit)
        self.window.after(120, self._drain)
        self.window.after(60, self._load_status)

    # ------------------------------------------------------------------ #
    # 构建
    # ------------------------------------------------------------------ #
    def _build_row(
        self, parent: tk.Widget, key: str, title: str, description: str, label: str
    ) -> dict[str, Any]:
        frame = tk.Frame(parent)
        frame.pack(fill=tk.X, anchor="w", pady=(0, 10))
        tk.Label(
            frame, text=title, font=("Microsoft YaHei UI", 10, "bold"), anchor="w"
        ).pack(anchor="w")
        tk.Label(
            frame, text=description, anchor="w", justify=tk.LEFT, wraplength=600, fg="#444444"
        ).pack(anchor="w")
        status = tk.Label(frame, text="当前状态：检测中…", anchor="w", fg="#555555")
        status.pack(anchor="w")
        controls = tk.Frame(frame)
        controls.pack(anchor="w", pady=(2, 0))
        button = tk.Button(
            controls,
            text=label,
            command=lambda: self._run_row(key),
        )
        button.pack(side=tk.LEFT)
        revert = tk.Button(controls, text="回退这次操作", state=tk.DISABLED)
        revert.pack(side=tk.LEFT, padx=(8, 0))
        result = tk.Label(
            frame, text="", anchor="w", justify=tk.LEFT, wraplength=600, fg="#333333"
        )
        result.pack(anchor="w")
        return {
            "button": button,
            "revert": revert,
            "result": result,
            "status": status,
            "revert_action": None,
        }

    def _fit(self) -> None:
        with contextlib.suppress(Exception):
            self._scroll.prepare()
            width = min(700, max(560, self.window.winfo_screenwidth() // 2))
            height = min(760, max(420, int(self.window.winfo_screenheight() * 0.7)))
            parent = self._app._root
            x = parent.winfo_rootx() + 40 if parent is not None else 100
            y = parent.winfo_rooty() + 40 if parent is not None else 100
            self.window.geometry(f"{width}x{height}+{max(0, x)}+{max(0, y)}")

    # ------------------------------------------------------------------ #
    # 状态
    # ------------------------------------------------------------------ #
    def _load_status(self) -> None:
        self._set_all_status("检测中…")

        def worker() -> None:
            try:
                status = repair.collect_status(self._app.service)
            except Exception as exc:  # noqa: BLE001 - 检测失败也要给出中文说明
                status = {"error": f"{type(exc).__name__}: {exc}"}
            self._queue.put(("status", status))

        threading.Thread(target=worker, name="liuhao-repair-status", daemon=True).start()

    def _set_all_status(self, text: str) -> None:
        for row in self._rows.values():
            with contextlib.suppress(Exception):
                row["status"].config(text=f"当前状态：{text}")

    def _apply_status(self, status: dict[str, Any]) -> None:
        self._status = status
        if status.get("error"):
            self._set_all_status(f"检测失败（{status['error']}）")
            return
        port = status.get("port") or {}
        if port.get("port"):
            service = getattr(self._app, "service", None)
            mine = bool(
                service is not None
                and service.is_running()
                and int(getattr(service, "port", 0) or 0) == int(port["port"])
            )
            if port.get("free"):
                state_text = "空闲（无需换端口）"
            elif mine:
                state_text = "由当前网关占用（正常）"
            else:
                state_text = "被其它程序占用"
            self._rows["port"]["status"].config(
                text=f"当前状态：端口 {port['port']} {state_text}"
            )
        directory = status.get("data_dir") or {}
        self._rows["directory"]["status"].config(
            text=(
                f"当前状态：{directory.get('path')} "
                + ("可写" if directory.get("writable") else "不可写")
                + (" · 日志目录可写" if directory.get("logs_writable") else " · 日志目录不可写")
            )
        )
        models = status.get("models") or {}
        endpoints = models.get("endpoints") or []
        without_key = models.get("without_key") or []
        self._rows["model_key"]["status"].config(
            text=(
                f"当前状态：已配置 {len(endpoints)} 个端点"
                + (f"，未填 Key：{'、'.join(without_key)}" if without_key else "，Key 是否有效需在设置页测试")
            )
        )
        domains = sorted({str(item.get("base_url") or "") for item in endpoints})
        self._rows["network"]["status"].config(
            text=(
                "当前状态：待测域名 " + "、".join(domains)
                if domains else "当前状态：没有配置 base_url，无法直连测试"
            )
        )
        databases = status.get("databases") or {}
        self._rows["database"]["status"].config(
            text=(
                f"当前状态：检查 {databases.get('checked', 0)} 个库，"
                + (f"损坏 {len(databases.get('corrupt') or [])} 个" if databases.get("corrupt") else "未发现损坏")
            )
        )
        assets = status.get("assets") or {}
        self._rows["assets"]["status"].config(
            text=(
                f"当前状态：{assets.get('dist')} · 引用 {assets.get('references', 0)} 个，"
                + (f"缺 {len(assets.get('missing') or [])} 个" if assets.get("missing") else "齐全")
            )
        )

    # ------------------------------------------------------------------ #
    # 执行
    # ------------------------------------------------------------------ #
    def _run_row(self, key: str) -> None:
        if key == "port":
            self._run(key, lambda: repair.switch_port(self._app.service))
        elif key == "directory":
            self._run_directory()
        elif key == "model_key":
            self._run_model_settings()
        elif key == "network":
            self._run(key, self._test_endpoints)
        elif key == "database":
            self._run_database(rebuild=False)
        elif key == "assets":
            self._run(key, lambda: repair.repair_static_assets(service=self._app.service))

    def _run_directory(self) -> None:
        chosen = filedialog.askdirectory(
            parent=self.window, title="选择新的数据目录（网关数据与日志都会放在这里）"
        )
        if not chosen:
            return
        confirm = (
            f"把网关的数据目录改到：\n{chosen}\n\n"
            "会做一次真实写入探测，通过后写入配置并重启网关；"
            "原目录里的文件不会自动搬过去。继续？"
        )
        if not messagebox.askyesno("确认切换数据目录", confirm, parent=self.window):
            return
        self._run(
            "directory",
            lambda: repair.change_data_dir(chosen, service=self._app.service),
        )

    def _run_model_settings(self) -> None:
        models = (self._status.get("models") or {}).get("endpoints") or []
        model_id = str(models[0].get("name") or "") if models else ""
        self._run(
            "model_key",
            lambda: repair.open_model_settings(
                self._app.service,
                model_id or None,
                opener=self._open_url,
                clipboard=self._copy_text,
            ),
        )

    def _run_database(self, *, rebuild: bool) -> None:
        mode = "rebuild" if rebuild else "restore"
        if rebuild:
            confirm = (
                "重建空库会丢：该数据库里已有的会话、记忆与配置数据。\n"
                "原件会被移动到 recovered-<时间戳> 目录，不会删除，可事后手动找回。\n\n"
                "确认重建空库？"
            )
            title = "确认重建空库（危险）"
        else:
            confirm = (
                "会先把损坏的库移到 recovered-<时间戳> 目录，再优先从 .bak / backups 恢复；\n"
                "找不到备份时改为重建空库（会丢该库原有数据，原件同样保留）。\n\n继续？"
            )
            title = "确认恢复数据库"
        if not messagebox.askyesno(title, confirm, parent=self.window):
            return
        self._run(
            "database",
            lambda: repair.recover_database(mode=mode, service=self._app.service),
        )

    def _test_endpoints(self) -> dict[str, Any]:
        urls = [
            str(item.get("base_url") or "")
            for item in (self._status.get("models") or {}).get("endpoints") or []
        ]
        return repair.probe_endpoints([url for url in urls if url])

    def _run(self, key: str, action: Callable[[], dict[str, Any]]) -> None:
        row = self._rows[key]
        with contextlib.suppress(Exception):
            row["button"].config(state=tk.DISABLED)
        with contextlib.suppress(Exception):
            row["revert"].config(state=tk.DISABLED)
        row["result"].config(text="正在执行…（动作会真实改动本机，最多等 90 秒）", fg="#333333")

        def worker() -> None:
            try:
                result = action()
            except Exception as exc:  # noqa: BLE001 - 任何异常都以中文回到界面
                result = {
                    "ok": False,
                    "changed": False,
                    "summary": f"执行失败：{type(exc).__name__}: {exc}",
                    "evidence": [],
                }
            self._queue.put((key, result))

        threading.Thread(target=worker, name=f"liuhao-repair-{key}", daemon=True).start()

    def _drain(self) -> None:
        if self._closed:
            return
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "status":
                    self._apply_status(payload)
                else:
                    self._apply_result(kind, payload)
        except queue.Empty:
            pass
        with contextlib.suppress(Exception):
            self.window.after(150, self._drain)

    def _apply_result(self, key: str, result: dict[str, Any]) -> None:
        row = self._rows.get(key)
        if row is None:
            return
        with contextlib.suppress(Exception):
            row["button"].config(state=tk.NORMAL)
        lines = [str(result.get("summary") or "（没有说明）")]
        for item in result.get("evidence") or []:
            value = item.get("value")
            if isinstance(value, (list, tuple)):
                value = "、".join(str(entry) for entry in value) or "无"
            lines.append(f"· {item.get('label')}：{value}")
        if result.get("log"):
            lines.append(f"日志：{result['log']}")
        ok = bool(result.get("ok"))
        row["result"].config(
            text="\n".join(lines), fg="#1b6e1b" if ok else "#a33a00"
        )
        revert_action = result.get("revert_action")
        row["revert_action"] = revert_action if result.get("changed") else None
        with contextlib.suppress(Exception):
            row["revert"].config(
                state=tk.NORMAL if row["revert_action"] else tk.DISABLED,
                command=lambda: self._revert(key),
            )
        if key == "directory" and ok and result.get("changed"):
            self._notify_data_dir_changed(result)
        self._load_status()

    def _revert(self, key: str) -> None:
        row = self._rows.get(key)
        if not row or not row.get("revert_action"):
            return
        if not messagebox.askyesno(
            "确认回退",
            "会按这次动作的反向操作改动本机（可能重启网关）。继续？",
            parent=self.window,
        ):
            return
        self._run(key, row["revert_action"])

    def _notify_data_dir_changed(self, result: dict[str, Any]) -> None:
        for item in result.get("evidence") or []:
            if item.get("label") == "新数据目录":
                callback = getattr(self._app, "_on_data_dir_changed", None)
                if callable(callback):
                    with contextlib.suppress(Exception):
                        callback(str(item.get("value") or ""))

    # ------------------------------------------------------------------ #
    # 小工具
    # ------------------------------------------------------------------ #
    def _open_url(self, url: str) -> None:
        callback = getattr(self._app, "_open_url", None)
        if callable(callback):
            callback(url)
            return
        import webbrowser

        webbrowser.open(url)

    def _copy_text(self, text: str) -> None:
        self.window.clipboard_clear()
        self.window.clipboard_append(text)
        self.window.update_idletasks()

    def close(self) -> None:
        self._closed = True
        with contextlib.suppress(Exception):
            self.window.destroy()
        with contextlib.suppress(Exception):
            self._app._repair_dialog = None


def open_repair_dialog(app: Any) -> RepairDialog:
    """打开（或前置已有的）修复面板。"""
    existing = getattr(app, "_repair_dialog", None)
    if existing is not None:
        with contextlib.suppress(Exception):
            existing.window.deiconify()
            existing.window.lift()
            existing.window.focus_force()
            return existing
    dialog = RepairDialog(app)
    with contextlib.suppress(Exception):
        app._repair_dialog = dialog
    return dialog


__all__ = ["RepairDialog", "open_repair_dialog"]


