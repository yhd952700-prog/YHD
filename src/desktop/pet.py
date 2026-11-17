"""桌宠小铁：纯逻辑层（C-pet 组，0.4.6）。

本模块**不依赖 tkinter、不依赖显示器、不直接碰网络**，所以在沙箱里也能
完整测试。窗口渲染见 ``pet_window.py``（只读这里的状态快照），事件来源
见 ``PetFeed``（走 A2a 推送合同 ``/v1/a2a/ws/status``，断了按合同回落
轮询 ``GET /v1/a2a/overview``）。

形象与十条标准
--------------
* 底图就是 Joseph 原图抠图（``apps/console/console/public/
  xiaotie-original-cut.webp``，943×943），桌宠用的两帧 PNG 由它转来，
  见 ``src/desktop/assets/pet/README.md``：f0 是原图等比缩放，f1 只做
  2.5° 微转＋上抬 3px（走路的另一只脚），不重画、不改色、不改身形。
  旧的 ``liuhao-pet-*.png`` 是以前的卡通图，不是 Joseph 的原图，不用。
* 原图是单张平面抠图，没有分开的手脚图层，所以手脚没法像 SVG 那样逐
  根转：桌宠和操作台头像用的是同一套已批准的做法——整图按走路节拍
  起伏（f0/f1 两帧交替）＋脚下淡影跟着缩胀＋位置真的在桌面上移动；
  只有图片加载失败的 SVG 兜底（操作台）才有逐根摆动的手脚。这一点
  交卷报告里老实写，不假装原图的手脚能逐根动。
* 反应堆永远蓝色：亮度表示忙闲（干活最亮、闲着呼吸亮暗、睡觉/充电
  最暗），不全身发光；Bot 换色只换配色点与配件徽记，不动原图。

说话规矩（2026-11-14 ~08:18 的要求）
------------------------------------
* 只在有事时说：接活、进度、完成、要确认、提醒。没事不说话、不刷屏。
* 每句话一句话、说清干了什么和结果在哪；重要的话操作台里也同步一条
  ——那一条由后端 ``notice`` / ``digest`` 本身发（A2a 第 14 项），桌宠
  这里只负责自己说，不再另发一条，避免两边各说各的。
* ``proactive_speech_enabled=False`` 时一句主动话也不说（连“要你拍板”
  也不说——那条仍在操作台“等你确认”收件箱里，丢不了）。
* 所有话都来自真实后端事件：没连上后端就没有话，绝不自己编一句
  “我干完了”演给用户看。

能量（科幻十二条的“能量”）
---------------------------
* 反应堆亮度＝忙闲；连续干活耗电，闲着回电；电量低了说一句
  “我充会儿电”然后坐下充电，充够了再起来；喂能量块一次 +25。
"""

from __future__ import annotations

import json
import logging
import os
import random
import threading
import time
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

#: 语音唤醒词（与控制台 ``lib/wakeWord.ts`` 的 WAKE_WORD 一致）。
WAKE_WORD = "贾维斯"
#: 桌宠召唤操作台的快捷键（与控制台 Workbench 的 Ctrl+J 一致）。
SUMMON_SHORTCUT = "Ctrl+J"

#: 本地唤醒词引擎的接入状态：sherpa-onnx KWS 专用模型已接通（2026-11-15
#: 第 5 段）。模型按需下载（约 32MB），检出 23/24、误唤 0/31——正样本
#: 是合成语音，真人声还没验证（见 src/voice/wake.py）。常驻监听默认
#: 不开；没模型、没引擎时监听开不起来，状态里如实写原因。
WAKE_ENGINE: Dict[str, Any] = {
    "engine": "sherpa-onnx-kws",
    "status": "integrated",
    "wake_word": WAKE_WORD,
    "note": (
        "本地唤醒词引擎已接通：sherpa-onnx 关键词模型（KWS 专用，"
        "检出 23/24、误唤 0/31，正样本是合成语音，真人声待验证）。"
        "常驻监听默认不开，在语音设置里开；现在叫贾维斯也可以用 "
        "Ctrl+J、托盘菜单「叫小铁出来」或点桌宠。"
    ),
}


def wake_status() -> Dict[str, Any]:
    """语音唤醒接入状态：没接就是没接，不假装能听。"""
    return dict(WAKE_ENGINE)


# ---------------------------------------------------------------------------
# 姿态与变身
# ---------------------------------------------------------------------------

#: 桌宠姿态。``docked``＝回到操作台左上角主脑位（桌面上没有小铁）；
#: ``flying_home``＝正在飞回去的过渡态；其余都是桌面上的样子。
POSES = (
    "idle", "walking", "running", "jumping", "sitting", "sleeping",
    "waving", "celebrating", "waiting", "scanning", "typing", "meeting",
    "charging", "peeking", "somersault", "flying", "error", "docked",
    "flying_home",
)

#: 干活变身：按任务种类换行头（都是配件/姿态，不改原图身形）。
#: 查资料＝扫描面罩、写代码＝全息键盘敲键盘、开会＝戴耳机。
TASK_VARIANTS: Dict[str, Dict[str, str]] = {
    "research": {"pose": "scanning", "accessory": "扫描面罩", "line": "查资料呢，扫描面罩已戴好"},
    "code": {"pose": "typing", "accessory": "全息键盘", "line": "写代码呢，全息键盘敲起来了"},
    "meeting": {"pose": "meeting", "accessory": "耳机", "line": "开会呢，耳机戴好了"},
}
DEFAULT_VARIANT = {"pose": "walking", "accessory": "", "line": ""}


def variant_for_task(title: str = "", kind: str = "") -> Dict[str, str]:
    """按任务种类（或标题关键词）定变身；认不出来就按普通干活算，不瞎猜。"""
    text = f"{kind or ''} {title or ''}".lower()
    if "research" in text or "查资料" in text or "搜索" in text or "调研" in text:
        return dict(TASK_VARIANTS["research"])
    if "code" in text or "写代码" in text or "改代码" in text or "编程" in text:
        return dict(TASK_VARIANTS["code"])
    if "meeting" in text or "开会" in text or "会议" in text or "meet" in text:
        return dict(TASK_VARIANTS["meeting"])
    return dict(DEFAULT_VARIANT)


def time_greeting(now: Optional[datetime] = None) -> str:
    """时间感知：按钟点打招呼（只在被叫出来/早上推待办时用，不主动刷屏）。"""
    hour = (now or datetime.now()).hour
    if 5 <= hour < 9:
        return "早上好"
    if 9 <= hour < 12:
        return "上午好"
    if 12 <= hour < 14:
        return "中午好"
    if 14 <= hour < 18:
        return "下午好"
    if 18 <= hour < 23:
        return "晚上好"
    return "夜深了"


# ---------------------------------------------------------------------------
# 设置（桌宠开关：总开关 / 漫游 / 主动说话 / 动效）
# ---------------------------------------------------------------------------

PET_SETTINGS_FILE = "pet-settings.json"

#: 桌宠在桌面上巡逻的默认范围（相对屏幕工作区的边距，窗口层再换算）。
PATROL_MARGIN = 40
#: 闲多久坐下打盹（秒）：任务书定的 5 分钟。
IDLE_NAP_SECONDS = 300.0
#: 随机小剧场：闲着时至少隔这么久才来一次，不刷屏。
THEATRE_MIN_GAP_SECONDS = 90.0
#: 能量：干活每分钟掉这么多，闲着每分钟回这么多，喂一块 +25。
ENERGY_DRAIN_PER_MINUTE = 6.0
ENERGY_RECOVER_PER_MINUTE = 10.0
ENERGY_BLOCK_BOOST = 25
ENERGY_LOW = 15
ENERGY_CHARGED = 60
#: 说话队列上限：超过就丢最旧的（不攒一堆话连珠炮）。
SPEECH_QUEUE_MAX = 3
#: 走路节拍（秒/步）：桌宠档放开，操作台框里是 0.9s（见 XiaotieAvatar.css）。
STEP_CADENCE_SECONDS = 0.45
RUN_CADENCE_SECONDS = 0.25


@dataclass
class PetSettings:
    """桌宠设置。默认全开；动效关了＝全部停（走路/巡逻/小剧场/飞回）。"""

    pet_enabled: bool = True
    roam_enabled: bool = True
    proactive_speech_enabled: bool = True
    reduced_motion: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pet_enabled": self.pet_enabled,
            "roam_enabled": self.roam_enabled,
            "proactive_speech_enabled": self.proactive_speech_enabled,
            "reduced_motion": self.reduced_motion,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "PetSettings":
        if not isinstance(data, dict):
            return cls()
        return cls(
            pet_enabled=bool(data.get("pet_enabled", True)),
            roam_enabled=bool(data.get("roam_enabled", True)),
            proactive_speech_enabled=bool(data.get("proactive_speech_enabled", True)),
            reduced_motion=bool(data.get("reduced_motion", False)),
        )


def pet_settings_path(data_dir: Any) -> Path:
    return Path(data_dir) / "desktop" / PET_SETTINGS_FILE


def load_pet_settings(data_dir: Any) -> PetSettings:
    """读桌宠设置；没有/损坏按默认全开处理，不阻断启动。"""
    try:
        raw = json.loads(pet_settings_path(data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return PetSettings()
    return PetSettings.from_dict(raw)


def save_pet_settings(data_dir: Any, settings: PetSettings) -> Path:
    """写桌宠设置（写临时文件再换名，写一半断电不留坏文件）。"""
    path = pet_settings_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(settings.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(tmp, path)
    return path


# ---------------------------------------------------------------------------
# 控制器：事件 → 姿态 / 说话 / 能量 / 位置（纯逻辑，无 GUI、无网络）
# ---------------------------------------------------------------------------

@dataclass
class SpeechLine:
    text: str
    at: float = field(default_factory=time.monotonic)
    source_event: str = ""


@dataclass
class PetSnapshot:
    pose: str
    variant: str
    variant_line: str
    progress: Optional[int]
    energy: int
    reactor_brightness: float
    docked: bool
    x: int
    y: int
    direction: int  # -1 朝左，1 朝右
    step_phase: int  # 0/1：走路两帧交替
    speech: Optional[str]
    speech_queue: int
    console_should_pop: bool
    wake: Dict[str, Any]


class PetController:
    """桌宠状态机。所有输入都是真实后端事件或用户动作；没事件就不动、不说。

    线程安全：窗口线程只读快照（``snapshot()``），事件与动作走带锁方法。
    """

    def __init__(
        self,
        settings: Optional[PetSettings] = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        rng: Optional[random.Random] = None,
    ) -> None:
        self._lock = threading.Lock()
        self.settings = settings or PetSettings()
        self._clock = clock
        self._rng = rng or random.Random()
        self.pose: str = "idle"
        self.variant: str = ""
        self.variant_line: str = ""
        self.progress: Optional[int] = None
        self.energy: float = 100.0
        self.docked: bool = True  # 没打开贾维斯时才站桌面上；启动时操作台将开，先停在主脑位
        self.x: int = 0
        self.y: int = 0
        self.direction: int = 1
        self.step_phase: int = 0
        self._speech: List[SpeechLine] = []
        self._current_speech: Optional[SpeechLine] = None
        self._speech_until: float = 0.0
        self._last_active: float = self._clock()
        self._last_theatre: float = 0.0
        self._busy_streak: int = 0
        self._charged_notice_sent: bool = False
        self._low_notice_sent: bool = False
        self.console_should_pop: bool = False
        self._task_title: str = ""

    # -- 内部小工具 ------------------------------------------------------ #
    def _say(self, text: str, *, source_event: str = "") -> bool:
        """排一句主动话。关了主动说话开关就一句也不排，返回 False。"""
        text = (text or "").strip()
        if not text:
            return False
        if not self.settings.proactive_speech_enabled:
            return False
        if len(text) > 60:
            text = text[:60]
        with self._lock:
            if len(self._speech) >= SPEECH_QUEUE_MAX:
                self._speech.pop(0)
            self._speech.append(SpeechLine(text=text, source_event=source_event))
        return True

    def _set_pose(self, pose: str, *, variant: str = "", variant_line: str = "") -> None:
        assert pose in POSES, pose
        self.pose = pose
        if variant or variant_line or pose in ("scanning", "typing", "meeting"):
            self.variant = variant
            self.variant_line = variant_line
        elif pose not in ("scanning", "typing", "meeting"):
            self.variant = ""
            self.variant_line = ""

    def _touch(self) -> None:
        self._last_active = self._clock()

    # -- 真实后端事件 ---------------------------------------------------- #
    def apply_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """把一条 A2a/A2b 推送事件翻成姿态＋说话。返回本次的动作摘要。

        认不得的事件类型直接忽略（不猜、不演）。``event`` 形如
        ``{"type": "task.lifecycle", "data": {...}}``，与推送合同一致。
        """
        if not isinstance(event, dict):
            return {"applied": False, "reason": "not-an-event"}
        etype = str(event.get("type") or "")
        data = event.get("data") or {}
        if not isinstance(data, dict):
            data = {}
        self._touch()
        if etype == "task.lifecycle":
            return self._on_task_lifecycle(str(data.get("event") or ""), data)
        if etype == "approval.changed":
            return self._on_approval(str(data.get("status") or ""), data)
        if etype == "bot.status":
            return self._on_bot_status(str(data.get("status") or ""), data)
        if etype == "reminder.due":
            text = str(data.get("text") or "").strip()
            self._set_pose("waving")
            if text:
                self._say(text, source_event=etype)
            return {"applied": True, "pose": self.pose, "said": bool(text)}
        if etype == "digest.ready":
            kind = str(data.get("kind") or "")
            text = str(data.get("text") or "").strip()
            self._set_pose("waving")
            if text:
                self._say(text, source_event=etype)
            if kind == "morning":
                self.console_should_pop = True
            return {"applied": True, "pose": self.pose, "said": bool(text)}
        if etype == "notice":
            kind = str(data.get("kind") or "")
            title = str(data.get("title") or "").strip()
            body = str(data.get("body") or "").strip()
            line = body or title
            if kind == "done":
                self._set_pose("celebrating")
                self._busy_streak = 0
            elif kind == "stuck":
                self._set_pose("error")
            elif kind == "needs_decision":
                self._set_pose("waiting")
                self.console_should_pop = True
            if line:
                self._say(line, source_event=etype)
            return {"applied": True, "pose": self.pose, "said": bool(line)}
        if etype in ("overview.snapshot", "overview.updated",
                     "team.snapshot", "team.updated", "heartbeat"):
            return {"applied": False, "reason": "snapshot-no-speech"}
        return {"applied": False, "reason": f"unknown-event:{etype}"}

    def _on_task_lifecycle(self, ev: str, data: Dict[str, Any]) -> Dict[str, Any]:
        title = str(data.get("title") or "").strip()
        self._task_title = title or self._task_title
        if ev == "started":
            variant = variant_for_task(title)
            self._set_pose(variant["pose"] if variant["pose"] != "walking" else "walking",
                           variant=variant["accessory"], variant_line=variant["line"])
            self._busy_streak += 1
            said = self._say(f"接了：{title}" if title else "接了新任务", source_event="task.lifecycle")
            return {"applied": True, "pose": self.pose, "said": said}
        if ev == "done":
            self._set_pose("celebrating")
            self.progress = 100
            self._busy_streak = 0
            said = self._say(f"干完了：{title}" if title else "干完了", source_event="task.lifecycle")
            return {"applied": True, "pose": self.pose, "said": said}
        if ev == "stuck":
            self._set_pose("error")
            said = self._say(f"卡住了：{title}" if title else "卡住了", source_event="task.lifecycle")
            return {"applied": True, "pose": self.pose, "said": said}
        if ev in ("waiting_human", "taken_over"):
            self._set_pose("waiting")
            said = self._say("这步得你定一下" if ev == "waiting_human" else f"我来接手：{title}",
                             source_event="task.lifecycle")
            return {"applied": True, "pose": self.pose, "said": said}
        if ev == "created":
            return {"applied": True, "pose": self.pose, "said": False}
        return {"applied": False, "reason": f"unknown-task-event:{ev}"}

    def _on_approval(self, status: str, data: Dict[str, Any]) -> Dict[str, Any]:
        operation = str(data.get("operation") or "").strip()
        if status == "pending":
            self._set_pose("waiting")
            self.console_should_pop = True
            said = self._say(f"要你拍板：{operation}" if operation else "有个事要你拍板",
                             source_event="approval.changed")
            return {"applied": True, "pose": self.pose, "said": said}
        if status in ("approved", "rejected", "expired"):
            self._set_pose("idle")
            word = {"approved": "批了", "rejected": "没批", "expired": "过期了"}[status]
            said = self._say(f"{operation}{word}" if operation else word,
                             source_event="approval.changed")
            return {"applied": True, "pose": self.pose, "said": said}
        return {"applied": False, "reason": f"unknown-approval:{status}"}

    def _on_bot_status(self, status: str, data: Dict[str, Any]) -> Dict[str, Any]:
        progress = data.get("progress")
        self.progress = progress if isinstance(progress, int) else None
        if status in ("thinking", "working", "researching"):
            variant = variant_for_task("", "research" if status == "researching" else "")
            if status == "researching":
                self._set_pose("scanning", variant="扫描面罩", variant_line="查资料呢，扫描面罩已戴好")
            else:
                self._set_pose("walking")
            return {"applied": True, "pose": self.pose, "said": False}
        if status == "waiting_confirmation":
            self._set_pose("waiting")
            return {"applied": True, "pose": self.pose, "said": False}
        if status == "done":
            self._set_pose("celebrating")
            self.progress = 100
            self._busy_streak = 0
            return {"applied": True, "pose": self.pose, "said": False}
        if status == "error":
            self._set_pose("error")
            return {"applied": True, "pose": self.pose, "said": False}
        return {"applied": False, "reason": f"unknown-bot-status:{status}"}

    # -- 用户动作 -------------------------------------------------------- #
    def on_double_click(self) -> Dict[str, Any]:
        """双击桌宠：挥手打招呼（问候按钟点来，不编天气/日程）。"""
        self._touch()
        self._set_pose("waving")
        said = self._say(f"{time_greeting()}，我在", source_event="double-click")
        return {"applied": True, "pose": self.pose, "said": said}

    def on_drag(self, dx: int, dy: int) -> Dict[str, Any]:
        """拖着走：朝拖的方向跑（dx 定朝向，dy 不改朝向）。"""
        self._touch()
        if dx > 0:
            self.direction = 1
        elif dx < 0:
            self.direction = -1
        if self.settings.reduced_motion:
            return {"applied": True, "pose": self.pose, "running": False}
        self._set_pose("running")
        return {"applied": True, "pose": self.pose, "running": True}

    def on_drag_end(self) -> Dict[str, Any]:
        self._touch()
        self._set_pose("idle")
        return {"applied": True, "pose": self.pose}

    def feed_energy_block(self) -> Dict[str, Any]:
        """喂能量块：+25 电，说一句，不多不少（不编“吃了三块”这种假账）。"""
        self._touch()
        before = self.energy
        self.energy = min(100.0, self.energy + ENERGY_BLOCK_BOOST)
        if self.pose == "charging" and self.energy >= ENERGY_CHARGED:
            self._set_pose("idle")
            self._low_notice_sent = False
        said = self._say("谢谢，能量块好吃", source_event="feed")
        return {"applied": True, "energy_before": round(before, 1),
                "energy": round(self.energy, 1), "said": said}

    def summon(self, reason: str = "") -> Dict[str, Any]:
        """随叫随到：桌宠飞出来、操作台弹出来（窗口层执行飞行动画与弹窗）。"""
        self._touch()
        self.docked = False
        self.console_should_pop = True
        if self.pose in ("docked", "flying_home", "sleeping"):
            self._set_pose("flying")
        return {"applied": True, "pose": self.pose,
                "console_should_pop": True, "reason": reason}

    def dock(self) -> Dict[str, Any]:
        """回到操作台左上角主脑位：先飞回去，到了再算停好（窗口层做动画）。"""
        self._touch()
        if self.settings.reduced_motion:
            self.docked = True
            self._set_pose("idle")
            return {"applied": True, "pose": self.pose, "docked": True, "animated": False}
        self._set_pose("flying_home")
        return {"applied": True, "pose": self.pose, "docked": False, "animated": True}

    def dock_arrived(self) -> Dict[str, Any]:
        """飞回动画走完：桌面上不再有小铁（只有一个小铁的规矩）。"""
        self.docked = True
        self._set_pose("idle")
        self.console_should_pop = False
        return {"applied": True, "pose": self.pose, "docked": True}

    def undock(self) -> Dict[str, Any]:
        """操作台关了/藏起来了：桌宠站回桌面上巡逻。"""
        self._touch()
        self.docked = False
        self._set_pose("idle")
        return {"applied": True, "pose": self.pose, "docked": False}

    # -- 时间推进：巡逻 / 打盹 / 能量 / 小剧场 --------------------------- #
    def tick(self, now: Optional[float] = None) -> Dict[str, Any]:
        """推进一次（窗口层每 0.5 秒调一次）。返回本轮变了什么。"""
        now = self._clock() if now is None else now
        changed: Dict[str, Any] = {"pose": self.pose}
        idle_for = now - self._last_active
        if self.pose in ("walking", "running"):
            self.step_phase = 1 - self.step_phase
        if self.pose == "charging":
            self.energy = min(100.0, self.energy + ENERGY_RECOVER_PER_MINUTE / 120.0)
            if self.energy >= ENERGY_CHARGED and not self._charged_notice_sent:
                self._charged_notice_sent = True
                self._set_pose("idle")
                self._low_notice_sent = False
                self._say("充好了，继续干活", source_event="charged")
                changed["pose"] = self.pose
            return changed
        busy = self.pose in ("walking", "running", "scanning", "typing", "meeting")
        if busy:
            self.energy = max(0.0, self.energy - ENERGY_DRAIN_PER_MINUTE / 120.0)
        else:
            self.energy = min(100.0, self.energy + ENERGY_RECOVER_PER_MINUTE / 120.0)
        if self.energy <= ENERGY_LOW and not self._low_notice_sent:
            self._low_notice_sent = True
            self._charged_notice_sent = False
            self._set_pose("charging")
            self._say("我充会儿电", source_event="low-energy")
            changed["pose"] = self.pose
            return changed
        if self.pose == "sleeping":
            return changed
        if idle_for >= IDLE_NAP_SECONDS and self.pose in ("idle", "sitting"):
            if self._busy_streak >= 3:
                self._set_pose("charging")
                self._say("连干好几件，充会儿电", source_event="low-energy")
            else:
                self._set_pose("sleeping")
            changed["pose"] = self.pose
            return changed
        if (
            self.pose == "idle"
            and self.settings.roam_enabled
            and not self.settings.reduced_motion
            and not self.docked
            and idle_for >= THEATRE_MIN_GAP_SECONDS
            and now - self._last_theatre >= THEATRE_MIN_GAP_SECONDS
        ):
            trick = self._rng.choice(["peeking", "somersault", "flying"])
            self._last_theatre = now
            self._set_pose(trick)
            changed["pose"] = self.pose
            return changed
        if self.pose in ("peeking", "somersault", "flying", "celebrating",
                         "jumping", "waving"):
            # 小动作演完就回闲着（窗口层按节拍调 tick，两拍后复位）。
            # 注意：waiting（等你确认）不在这里——没拍板之前一直等着，不自己回闲着。
            if idle_for >= 4.0:
                self._set_pose("idle")
                changed["pose"] = self.pose
        return changed

    def patrol_step(self, screen_w: int, screen_h: int) -> Dict[str, int]:
        """巡逻走一步（只在桌面上、漫游开着、没减动效时才真的挪位置）。"""
        if self.docked or not self.settings.roam_enabled or self.settings.reduced_motion:
            return {"x": self.x, "y": self.y, "moved": 0}
        if self.pose not in ("idle", "walking"):
            return {"x": self.x, "y": self.y, "moved": 0}
        self._set_pose("walking")
        step = 14 if self.pose == "walking" else 0
        self.x += self.direction * step
        if self.x > screen_w - 180:
            self.x = screen_w - 180
            self.direction = -1
        elif self.x < PATROL_MARGIN:
            self.x = PATROL_MARGIN
            self.direction = 1
        if self.y < screen_h - 220:
            self.y = screen_h - 220
        return {"x": self.x, "y": self.y, "moved": 1}

    def place(self, x: int, y: int) -> None:
        """窗口层把桌宠放到屏幕某处（初次出现停在右下角、拖动跟手）。"""
        self.x = int(x)
        self.y = int(y)

    def update_settings(self, settings: "PetSettings") -> None:
        """换一整份设置（后端同步下来时调；带锁，窗口线程读到的是整份）。"""
        with self._lock:
            self.settings = settings

    # -- 读数 ------------------------------------------------------------ #
    def reactor_brightness(self) -> float:
        """反应堆亮度 0–1：干活最亮、闲着呼吸亮暗、睡觉/充电最暗。"""
        if self.pose in ("walking", "running", "scanning", "typing", "meeting"):
            return 1.0
        if self.pose == "celebrating":
            return 0.95
        if self.pose in ("sleeping", "charging"):
            return 0.25
        if self.pose == "error":
            return 0.45
        return 0.7

    def current_speech(self) -> Optional[str]:
        """当前要显示的一句话（每句最多显示 6 秒，到点换下一句）。"""
        now = self._clock()
        with self._lock:
            if self._current_speech is not None and now < self._speech_until:
                return self._current_speech.text
            if self._speech:
                self._current_speech = self._speech.pop(0)
                self._speech_until = now + 6.0
                return self._current_speech.text
            self._current_speech = None
            return None

    def snapshot(self) -> PetSnapshot:
        with self._lock:
            queued = len(self._speech)
        return PetSnapshot(
            pose=self.pose,
            variant=self.variant,
            variant_line=self.variant_line,
            progress=self.progress,
            energy=int(round(self.energy)),
            reactor_brightness=self.reactor_brightness(),
            docked=self.docked,
            x=self.x,
            y=self.y,
            direction=self.direction,
            step_phase=self.step_phase,
            speech=self.current_speech(),
            speech_queue=queued,
            console_should_pop=self.console_should_pop,
            wake=dict(WAKE_ENGINE),
        )

    def pop_consumed(self) -> None:
        """操作台弹出来了：清掉“该弹”标记，不重复弹。"""
        self.console_should_pop = False


# ---------------------------------------------------------------------------
# 事件来源：WebSocket 主用，断了按合同回落轮询
# ---------------------------------------------------------------------------

def parse_push_event(raw: Any) -> Optional[Dict[str, Any]]:
    """解析一条推送原始消息；不是事件就返回 None（心跳 pong 也算 None）。"""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None
    if not isinstance(raw, dict):
        return None
    if raw.get("pong") is True or raw.get("type") == "heartbeat":
        return None
    etype = str(raw.get("type") or "")
    if not etype:
        return None
    data = raw.get("data")
    return {"type": etype, "at": raw.get("at") or "", "data": data if isinstance(data, dict) else {}}


class PetFeed:
    """桌宠的事件订阅：WS 主用，断线按 A2a 合同回落轮询。

    ``ws_connect`` / ``http_get`` 可注入（测试用假源，不连真网关）；
    默认走 ``websockets.sync.client``（随包分发，requirements 有），
    没装就只走轮询——轮询也是合同写明的回落口，不算降级造假。
    """

    WS_PATH = "/v1/a2a/ws/status"
    POLL_PATH = "/v1/a2a/overview"
    RECONNECT_DELAY_SECONDS = 5.0
    MAX_WS_RECONNECTS = 3
    POLL_INTERVAL_SECONDS = 30.0
    HEARTBEAT_SECONDS = 30.0

    def __init__(
        self,
        base_url: str,
        token: Callable[[], str],
        on_event: Callable[[Dict[str, Any]], None],
        *,
        ws_connect: Optional[Callable[..., Any]] = None,
        http_get: Optional[Callable[[str, str], Dict[str, Any]]] = None,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self._token = token
        self._on_event = on_event
        self._ws_connect = ws_connect
        self._http_get = http_get
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.mode: str = "idle"  # websocket | polling | idle

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="jarvis-pet-feed", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)

    def _run(self) -> None:
        fails = 0
        while not self._stop.is_set():
            if self._try_websocket():
                fails = 0
                continue
            fails += 1
            if fails > self.MAX_WS_RECONNECTS:
                self.mode = "polling"
                self._poll_loop()
                return
            self._stop.wait(self.RECONNECT_DELAY_SECONDS)

    def _try_websocket(self) -> bool:
        """连一次 WS，收到 close/error 就返回 False（由 _run 决定重连/回落）。"""
        connect = self._ws_connect or _default_ws_connect
        url = self.base_url.replace("http://", "ws://").replace("https://", "wss://") + self.WS_PATH
        try:
            ws = connect(url, self._token() or "")
        except Exception:  # noqa: BLE001 - 没连上就走重连/轮询，不抛
            logger.warning("桌宠推送 WS 连不上，稍后重试", exc_info=True)
            return False
        self.mode = "websocket"
        try:
            while not self._stop.is_set():
                try:
                    raw = ws.recv(timeout=self.HEARTBEAT_SECONDS)
                except Exception:  # noqa: BLE001 - 超时发一次 ping
                    try:
                        ws.send(json.dumps({"ping": True}))
                    except Exception:  # noqa: BLE001 - 发不出去就是断了
                        return False
                    continue
                event = parse_push_event(raw)
                if event is not None:
                    self._on_event(event)
        finally:
            with suppress(Exception):
                ws.close()
        return False

    def _poll_loop(self) -> None:
        """轮询回落：每 30 秒拿一次快照，只推“变了”的那部分，不编数据。"""
        seen: Dict[str, str] = {}
        while not self._stop.is_set():
            try:
                body = self._http_get_json(self.POLL_PATH)
            except Exception:  # noqa: BLE001 - 轮询失败就等下一轮
                logger.warning("桌宠推送轮询失败，等下一轮", exc_info=True)
                self._stop.wait(self.POLL_INTERVAL_SECONDS)
                continue
            for key in ("overview", "team"):
                snap = body.get(key) if isinstance(body, dict) else None
                if isinstance(snap, dict):
                    stamp = json.dumps(snap, ensure_ascii=False, sort_keys=True)
                    if seen.get(key) != stamp:
                        seen[key] = stamp
                        self._on_event({"type": snap.get("type") or f"{key}.snapshot",
                                        "data": snap.get("data") or {}})
            self._stop.wait(self.POLL_INTERVAL_SECONDS)

    def _http_get_json(self, path: str) -> Dict[str, Any]:
        if self._http_get is not None:
            return self._http_get(self.base_url + path, self._token() or "")
        from urllib.request import Request, urlopen

        req = Request(self.base_url + path)
        token = self._token() or ""
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        with urlopen(req, timeout=5) as resp:  # noqa: S310 - 只连本机网关
            return json.loads(resp.read().decode("utf-8"))


def _default_ws_connect(url: str, token: str):  # pragma: no cover - 真 WS 由集成验
    """默认 WS 连接（websockets 同步客户端；没装就抛给调用方走轮询）。"""
    from websockets.sync.client import connect

    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return connect(url, additional_headers=headers)


__all__ = [
    "POSES", "TASK_VARIANTS", "WAKE_WORD", "SUMMON_SHORTCUT", "WAKE_ENGINE",
    "PetSettings", "PetController", "PetFeed", "SpeechLine", "PetSnapshot",
    "load_pet_settings", "save_pet_settings", "pet_settings_path",
    "variant_for_task", "time_greeting", "wake_status", "parse_push_event",
    "IDLE_NAP_SECONDS", "ENERGY_LOW", "ENERGY_BLOCK_BOOST",
]
