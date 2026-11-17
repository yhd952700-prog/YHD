"""语音对话调参设置（7-5 §5.1 / §5.2）。

静音阈值（说完多久算说完）与打断阈值不能写死在代码里：真机实测
（安静房间 / 开电视 / 外放）后才能定默认值。本文件只管存取与校验，
默认值仍是设计稿的起步值：静音 800ms、插话持续 300ms、打断冷却 500ms。

落盘：``<数据目录>/voice/settings.json``（只含数字，不含音频、不含密钥）。
环境变量 ``LIUHAO_VAD_MIN_SILENCE_MS`` 仍可覆盖静音阈值（只认
400/600/800/1200 四档，其余值忽略并当作没设）；被环境变量覆盖时，
界面会收到 ``env_override`` 标记并提示「改了不生效」。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

from src.common.engines import data_dir

#: 7-5 §5.1 的调参办法：min_silence_duration_ms 只在这四档里跑，
#: 数“切早了”和“切晚了”各多少次，取两者都最少的那档写进设置。
SILENCE_OPTIONS_MS = (400, 600, 800, 1200)

#: 起步默认值（设计稿 §5.1 / §4.2）：对话场景下 800ms 起步。
DEFAULT_MIN_SILENCE_MS = 800
DEFAULT_INTERRUPT_MIN_MS = 300
DEFAULT_INTERRUPT_COOLDOWN_MS = 500

_ENV_MIN_SILENCE = "LIUHAO_VAD_MIN_SILENCE_MS"


def settings_path() -> Path:
    return data_dir() / "voice" / "settings.json"


def _env_min_silence_ms() -> int | None:
    raw = (os.environ.get(_ENV_MIN_SILENCE) or "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value in SILENCE_OPTIONS_MS else None


def load_voice_settings() -> Dict[str, Any]:
    """读当前生效的语音调参（含来源标记；读不到文件时返回默认值）。"""
    stored: Dict[str, Any] = {}
    path = settings_path()
    try:
        if path.is_file():
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                stored = loaded
    except (OSError, ValueError):
        stored = {}
    silence = stored.get("min_silence_duration_ms", DEFAULT_MIN_SILENCE_MS)
    if silence not in SILENCE_OPTIONS_MS:
        silence = DEFAULT_MIN_SILENCE_MS
    interrupt_min = stored.get("interrupt_min_ms", DEFAULT_INTERRUPT_MIN_MS)
    if not isinstance(interrupt_min, int) or not 100 <= interrupt_min <= 1000:
        interrupt_min = DEFAULT_INTERRUPT_MIN_MS
    cooldown = stored.get("interrupt_cooldown_ms", DEFAULT_INTERRUPT_COOLDOWN_MS)
    if not isinstance(cooldown, int) or not 0 <= cooldown <= 2000:
        cooldown = DEFAULT_INTERRUPT_COOLDOWN_MS
    env_silence = _env_min_silence_ms()
    return {
        "min_silence_duration_ms": env_silence if env_silence is not None else silence,
        "interrupt_min_ms": interrupt_min,
        "interrupt_cooldown_ms": cooldown,
        "env_override": {"min_silence_duration_ms": env_silence is not None},
        "silence_options_ms": list(SILENCE_OPTIONS_MS),
        "note": (
            "静音满这么久算说完，自动停录送转写；400/600/800/1200 四档，"
            "按真机实测（切早/切晚次数）选，默认 800ms。"
        ),
    }


def save_voice_settings(
    *,
    min_silence_duration_ms: int,
    interrupt_min_ms: int = DEFAULT_INTERRUPT_MIN_MS,
    interrupt_cooldown_ms: int = DEFAULT_INTERRUPT_COOLDOWN_MS,
) -> Dict[str, Any]:
    """保存语音调参（只认合法值；非法值抛 ValueError，不落盘）。"""
    if min_silence_duration_ms not in SILENCE_OPTIONS_MS:
        raise ValueError(
            "静音阈值只能是 400 / 600 / 800 / 1200（毫秒），"
            f"收到 {min_silence_duration_ms}。"
        )
    if not isinstance(interrupt_min_ms, int) or not 100 <= interrupt_min_ms <= 1000:
        raise ValueError(f"插话持续时长只能是 100–1000 毫秒，收到 {interrupt_min_ms}。")
    if not isinstance(interrupt_cooldown_ms, int) or not 0 <= interrupt_cooldown_ms <= 2000:
        raise ValueError(f"打断冷却只能是 0–2000 毫秒，收到 {interrupt_cooldown_ms}。")
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "min_silence_duration_ms": min_silence_duration_ms,
        "interrupt_min_ms": interrupt_min_ms,
        "interrupt_cooldown_ms": interrupt_cooldown_ms,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return load_voice_settings()


def vad_min_silence_ms() -> int:
    """转写用的 min_silence_duration_ms：设置值（环境变量优先）。"""
    return int(load_voice_settings()["min_silence_duration_ms"])
