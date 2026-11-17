"""桌宠窗口：透明置顶的小铁站在桌面上（C-pet 组，0.4.6）。

* 底图是 Joseph 原图的两帧 PNG（``assets/pet/xiaotie-original-256-f0/f1.png``，
  来源见 ``assets/pet/README.md``），``tk.PhotoImage`` 直接读 PNG——
  打包后的 Windows 运行时没有 Pillow，所以这里绝不依赖 Pillow。
* 窗口跑在自己的线程里（自己的 ``tk.Tk`` 根窗口＋命令队列），不抢
  操作台窗口的 GUI 线程；所有状态只读 ``PetController.snapshot()``，
  所有动作只调控制器的线程安全方法。
* 透明：窗口背景用一个不会出现在图里的品红做透明色（``-transparentcolor``，
  Windows 上 tkinter 的做法），图片本身带 alpha；没显示器/不是 Windows
  时建窗失败会抛 ``PetWindowUnavailable``，调用方按“没有桌宠”处理，
  绝不因此挡住操作台启动。
* 动作分两档：桌宠档幅度放开（巡逻真的挪位置、走路两帧交替＋脚下淡影
  缩胀）；操作台框里那档是 ``XiaotieAvatar`` 的 CSS（框内原地走），
  两边是同一张原图、同一套十条标准，不另起一套动画系统。
* 减动效时全部停：不巡逻、不走路、不飞、不演小剧场，只静静站着。
"""

from __future__ import annotations

import contextlib
import logging
import queue
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from .pet import PetController

logger = logging.getLogger(__name__)

#: 桌宠窗口大小（图片 256px ＋气泡与影子的余量）。
PET_WINDOW_SIZE = 280
PET_IMAGE_SIZE = 200
#: 透明色：窗口背景刷成这个色再整窗透明（图里没有这个品红，不会误透）。
TRANSPARENT_COLOR = "#ff00ff"
#: 巡逻节拍（毫秒）：每拍走一步（控制器定方向与步长）。
PATROL_TICK_MS = 500
#: 飞回动画：分这么多步飞向左上角主脑位。
FLY_HOME_STEPS = 12
FLY_HOME_TICK_MS = 50

ASSETS_DIR = Path(__file__).resolve().parent / "assets" / "pet"
FRAME_F0 = ASSETS_DIR / "xiaotie-original-256-f0.png"
FRAME_F1 = ASSETS_DIR / "xiaotie-original-256-f1.png"


class PetWindowUnavailable(Exception):
    """桌宠窗口建不起来（没显示器、不是 Windows、tkinter 不可用等）。"""


class PetWindow:
    """桌宠窗口（自带线程）。建窗失败只抛 ``PetWindowUnavailable``。"""

    def __init__(
        self,
        controller: PetController,
        *,
        on_open_console: Optional[Callable[[], None]] = None,
        frame_f0: Any = FRAME_F0,
        frame_f1: Any = FRAME_F1,
    ) -> None:
        self.controller = controller
        self._on_open_console = on_open_console
        self._frame_f0 = Path(frame_f0)
        self._frame_f1 = Path(frame_f1)
        self._commands: queue.Queue[str] = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        #: 只存错误文案，不存异常对象：Tk() 失败时异常的回溯链拽着半构造的
        #: Tk 实例，存对象会让它活到测试护栏（tests/_tk_thread_guard.py）
        #: 那里，报“仍有 Tk 对象存活”。存文案就够排障了。
        self._error: Optional[str] = None
        self._running = False

    # -- 生命周期（调用方线程） ------------------------------------------- #
    def start(self) -> None:
        """起窗口线程；5 秒内没起来就按不可用处理（不无限等）。"""
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._window_main, name="jarvis-pet-window", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=5.0):
            raise PetWindowUnavailable("桌宠窗口 5 秒内没起来（可能没有显示器）")
        if self._error is not None:
            raise PetWindowUnavailable(f"桌宠窗口建不起来：{self._error}")

    def stop(self) -> None:
        self._commands.put("stop")
        if self._thread is not None:
            self._thread.join(timeout=3.0)

    def pop_to_front(self) -> None:
        """有事时跳到前面找他（任务干完/要确认/早上待办时调）。"""
        self._commands.put("pop")

    def fly_home(self) -> None:
        """飞回操作台左上角主脑位（窗口线程里做动画，到了调 dock_arrived）。"""
        self._commands.put("fly_home")

    # -- 窗口线程 ---------------------------------------------------------- #
    def _window_main(self) -> None:
        try:
            import tkinter as tk
        except Exception as exc:  # noqa: BLE001 - 没 tkinter 就是不可用
            self._error = f"{type(exc).__name__}：{exc}"
            self._ready.set()
            return
        try:
            root = tk.Tk()
        except Exception as exc:  # noqa: BLE001 - 没显示器时 Tk() 直接抛
            self._error = f"{type(exc).__name__}：{exc}"
            self._ready.set()
            return
        self._tk = tk
        self._root = root
        try:
            self._build(root)
        except Exception as exc:  # noqa: BLE001 - 建窗中途失败同样按不可用
            self._error = f"{type(exc).__name__}：{exc}"
            with contextlib.suppress(Exception):
                root.destroy()
            self._ready.set()
            return
        self._running = True
        self._ready.set()
        self._schedule_tick()
        with contextlib.suppress(Exception):
            root.mainloop()
        self._running = False

    def _build(self, root: Any) -> None:
        tk = self._tk
        root.withdraw()
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        try:
            root.attributes("-transparentcolor", TRANSPARENT_COLOR)
        except Exception:  # noqa: BLE001 - 非 Windows 上该属性不存在，不透明也能站
            logger.warning("桌宠窗口透明色不支持，按不透明窗口显示")
        root.configure(bg=TRANSPARENT_COLOR)
        root.geometry(f"{PET_WINDOW_SIZE}x{PET_WINDOW_SIZE}+100+100")

        self._canvas = tk.Canvas(
            root, width=PET_WINDOW_SIZE, height=PET_WINDOW_SIZE,
            bg=TRANSPARENT_COLOR, highlightthickness=0,
        )
        self._canvas.pack()
        # 脚下淡影：跟着走路缩胀（和操作台头像的 xt-shadow 同一个做法）。
        self._shadow = self._canvas.create_oval(70, 248, 210, 262, fill="#000000", outline="")
        self._frames = []
        for path in (self._frame_f0, self._frame_f1):
            try:
                self._frames.append(tk.PhotoImage(file=str(path)))
            except Exception as exc:  # noqa: BLE001 - 图读不出来就用色块占位，不崩
                logger.warning("桌宠图片读不出来 %s：%s", path, exc)
                self._frames.append(None)
        self._image_id = self._canvas.create_image(
            PET_WINDOW_SIZE // 2, 130, image=self._frames[0] if self._frames else None
        )
        # 反应堆光晕：胸口蓝色圆点（原图实测 x 59% / y 72%，与操作台一致），
        # 亮度随忙闲变（颜色永远蓝色，不随 Bot 配色走）。
        self._reactor = self._canvas.create_oval(0, 0, 0, 0, fill="#35b6ff", outline="")
        # 气泡：只在有话时出现，一句话，说完 6 秒自动收。
        self._bubble = tk.Label(
            root, text="", bg="#ffffff", fg="#222222",
            font=("Microsoft YaHei UI", 11), wraplength=220,
            relief="solid", borderwidth=1, padx=8, pady=4,
        )
        self._bubble.place_forget()
        self._variant_label = tk.Label(
            root, text="", bg="#fff3e0", fg="#7a4a00",
            font=("Microsoft YaHei UI", 10), padx=6, pady=2,
        )
        self._variant_label.place_forget()

        self._drag: Optional[dict[str, int]] = None
        self._fly_home_left = 0
        root.bind("<ButtonPress-1>", self._on_press)
        root.bind("<B1-Motion>", self._on_drag)
        root.bind("<ButtonRelease-1>", self._on_release)
        root.bind("<Double-Button-1>", self._on_double_click)
        root.bind("<Button-3>", self._on_right_click)
        self._menu = tk.Menu(root, tearoff=0)
        self._menu.add_command(label="喂能量块", command=self._on_feed)
        self._menu.add_command(label="打开贾维斯操作台", command=self._on_open_console_menu)
        self._menu.add_command(label="收起桌宠", command=self._on_hide_menu)

    # -- 输入 -------------------------------------------------------------- #
    def _on_press(self, event: Any) -> None:
        self._drag = {"x": event.x_root, "y": event.y_root, "moved": 0}

    def _on_drag(self, event: Any) -> None:
        if self._drag is None:
            return
        dx = event.x_root - self._drag["x"]
        dy = event.y_root - self._drag["y"]
        if abs(dx) + abs(dy) < 4:
            return
        self._drag["moved"] = 1
        self.controller.on_drag(dx, dy)
        try:
            nx = self._root.winfo_x() + dx
            ny = self._root.winfo_y() + dy
        except Exception:  # noqa: BLE001 - 读不到位置就不挪
            nx, ny = 100, 100
        self.controller.place(nx, ny)
        self._move_window(nx, ny)
        self._drag["x"], self._drag["y"] = event.x_root, event.y_root

    def _on_release(self, _event: Any) -> None:
        if self._drag is None:
            return
        moved = self._drag["moved"]
        self._drag = None
        if moved:
            self.controller.on_drag_end()

    def _on_double_click(self, _event: Any) -> None:
        """双击：挥手打招呼（控制器按钟点问候）。"""
        self.controller.on_double_click()

    def _on_right_click(self, event: Any) -> None:
        with contextlib.suppress(Exception):
            self._menu.tk_popup(event.x_root, event.y_root)

    def _on_feed(self) -> None:
        self.controller.feed_energy_block()

    def _on_open_console_menu(self) -> None:
        if self._on_open_console is not None:
            self._on_open_console()

    def _on_hide_menu(self) -> None:
        self.controller.dock()
        self._commands.put("fly_home")

    # -- 渲染循环 ---------------------------------------------------------- #
    def _schedule_tick(self) -> None:
        if not self._running:
            return
        try:
            self._tick()
        except Exception:  # noqa: BLE001 - 一拍渲染失败不杀窗口线程
            logger.warning("桌宠渲染失败，跳过这一拍", exc_info=True)
        with contextlib.suppress(Exception):
            self._root.after(PATROL_TICK_MS, self._schedule_tick)

    def _tick(self) -> None:
        # 先处理调用方线程发来的命令（弹前面 / 飞回去 / 停）。
        while True:
            try:
                cmd = self._commands.get_nowait()
            except queue.Empty:
                break
            if cmd == "stop":
                self._running = False
                self._root.destroy()
                return
            if cmd == "pop":
                self._pop_to_front()
            elif cmd == "fly_home":
                self._fly_home_left = FLY_HOME_STEPS
        snap = self.controller.snapshot()
        if snap.docked and snap.pose != "flying_home":
            self._root.withdraw()
            return
        if snap.pose == "flying_home":
            self._step_fly_home(snap)
            return
        self._root.deiconify()
        # 初次站上桌面：停在右下角（别挡中间的字）。
        if snap.x == 0 and snap.y == 0:
            try:
                sw = self._root.winfo_screenwidth()
                sh = self._root.winfo_screenheight()
            except Exception:  # noqa: BLE001 - 读不到屏幕尺寸就用默认位置
                sw, sh = 1280, 800
            self.controller.place(max(sw - 340, 0), max(sh - 340, 0))
        # 巡逻：控制器定方向与步长，窗口只负责把窗口挪过去。
        if not snap.docked:
            try:
                sw = self._root.winfo_screenwidth()
                sh = self._root.winfo_screenheight()
            except Exception:  # noqa: BLE001 - 读不到屏幕尺寸就不巡逻
                sw, sh = 1280, 800
            step = self.controller.patrol_step(sw, sh)
            self._move_window(step["x"], step["y"])
        self.controller.tick()
        snap = self.controller.snapshot()
        self._render(snap)

    def _move_window(self, x: int, y: int) -> None:
        with contextlib.suppress(Exception):
            self._root.geometry(f"{PET_WINDOW_SIZE}x{PET_WINDOW_SIZE}+{int(x)}+{int(y)}")

    def _pop_to_front(self) -> None:
        with contextlib.suppress(Exception):
            self._root.deiconify()
            self._root.lift()
            self._root.attributes("-topmost", True)

    def _step_fly_home(self, snap: Any) -> None:
        """飞回去：分步往左上角主脑位飞，到了告诉控制器“桌面上没小铁了”。"""
        if self._fly_home_left <= 0:
            self._fly_home_left = FLY_HOME_STEPS
        try:
            x = self._root.winfo_x()
            y = self._root.winfo_y()
        except Exception:  # noqa: BLE001 - 读不到位置就直接算到了
            x, y = 0, 0
        target_x, target_y = 8, 8
        nx = x + (target_x - x) // max(self._fly_home_left, 1)
        ny = y + (target_y - y) // max(self._fly_home_left, 1)
        self._move_window(nx, ny)
        self._fly_home_left -= 1
        if self._fly_home_left <= 0:
            self.controller.dock_arrived()
            self._root.withdraw()

    def _render(self, snap: Any) -> None:
        # 走路两帧交替（f0/f1），不走路时固定 f0；减动效时永远 f0。
        frame = self._frames[0] if self._frames else None
        if snap.pose in ("walking", "running") and len(self._frames) > 1:
            frame = self._frames[snap.step_phase] or self._frames[0]
        if frame is not None:
            with contextlib.suppress(Exception):
                self._canvas.itemconfig(self._image_id, image=frame)
        # 脚下淡影跟着走路缩胀（走路那拍收窄、落地放开）。
        shrink = 0.86 if (snap.pose in ("walking", "running") and snap.step_phase == 1) else 1.0
        cx, rx = PET_WINDOW_SIZE // 2, int(70 * shrink)
        with contextlib.suppress(Exception):
            self._canvas.coords(self._shadow, cx - rx, 248, cx + rx, 262)
        # 反应堆光晕：亮度＝忙闲（蓝 0.25–1.0 之间插值，不全身发光）。
        b = max(0.0, min(1.0, snap.reactor_brightness))
        blue = _reactor_color(b)
        rx0, ry0 = int(PET_WINDOW_SIZE * 0.59), int(PET_WINDOW_SIZE * 0.72)
        r = 11 if b > 0.8 else 8
        with contextlib.suppress(Exception):
            self._canvas.coords(self._reactor, rx0 - r, ry0 - r, rx0 + r, ry0 + r)
            self._canvas.itemconfig(self._reactor, fill=blue)
        # 变身行头：配件名写在图下方小条上（不改原图）。
        if snap.variant:
            self._variant_label.configure(text=f"{snap.variant} · {snap.variant_line or ''}".strip(" ·"))
            self._variant_label.place(x=40, y=252)
        else:
            self._variant_label.place_forget()
        # 气泡：有话才出现，一句话。
        if snap.speech:
            self._bubble.configure(text=snap.speech)
            self._bubble.place(x=10, y=4)
        else:
            self._bubble.place_forget()
        # 进度光圈：干活时在窗口标题栏……没有标题栏，改画在图外圈的细环上
        # （与操作台“干活只留进度光圈一个信号”一致，这里用淡影颜色深浅表示，
        # 不另加红点/标签/横条）。
        if snap.progress is not None:
            with contextlib.suppress(Exception):
                self._canvas.itemconfig(self._shadow, fill="#1a3a55")
        else:
            with contextlib.suppress(Exception):
                self._canvas.itemconfig(self._shadow, fill="#000000")


def _reactor_color(brightness: float) -> str:
    """反应堆蓝按亮度插值（暗＝#0a2a44，亮＝#35b6ff），永远是蓝色。"""
    dim = (0x0A, 0x2A, 0x44)
    bright = (0x35, 0xB6, 0xFF)
    r = round(dim[0] + (bright[0] - dim[0]) * brightness)
    g = round(dim[1] + (bright[1] - dim[1]) * brightness)
    b = round(dim[2] + (bright[2] - dim[2]) * brightness)
    return f"#{r:02x}{g:02x}{b:02x}"


__all__ = ["PetWindow", "PetWindowUnavailable", "PET_WINDOW_SIZE"]
