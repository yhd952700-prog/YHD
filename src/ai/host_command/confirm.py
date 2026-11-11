"""「不可逆动作一次确认」的开关（默认开，可关，关闭必须留痕）。

老板 2026-11-11 的决定：全权模式下 ``format`` / ``diskpart`` / ``bcdedit`` /
递归强删 / 关机重启这类命令归入**一次确认**；用户可以在设置里把这一层也关掉，
但关闭动作本身要记审计（谁、何时）—— 那是 G3。

**默认值必须是开**：读不到配置、文件损坏、JSON 形状不对，一律按「要确认」
处理。权限开关的失败方向只能更严。

环境变量 ``LIUHAO_HOST_COMMAND_CONFIRM=off`` 可强制关闭（运维/测试用，优先级
最高）；除此之外由设置页写入的数据目录文件决定。
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from ...common.atomic_write import atomic_write_text, read_text_with_retry

#: 环境变量：``off`` / ``0`` / ``false`` / ``no`` 表示关闭确认。
ENV_VAR = "LIUHAO_HOST_COMMAND_CONFIRM"

#: 设置页落盘文件的显式覆盖变量（测试用）。
SETTINGS_FILE_ENV = "LIUHAO_HOST_COMMAND_CONFIRM_FILE"

_DEFAULT_SETTINGS_PATH = (
    Path(__file__).resolve().parents[3] / "data" / "host_command_confirm.json"
)

_OFF_VALUES = frozenset({"off", "0", "false", "no", "disabled"})

_lock = threading.RLock()
_cache: bool | None = None


def _settings_path() -> Path:
    from ...common.data_paths import resolve_store_path

    return Path(
        resolve_store_path(
            "host_command_confirm.json",
            _DEFAULT_SETTINGS_PATH,
            env_var=SETTINGS_FILE_ENV,
        )
    )


def _env_override() -> bool | None:
    """环境变量显式关闭时返回 ``False``；未设置/空值返回 ``None``（交给文件）。"""
    raw = os.environ.get(ENV_VAR)
    if raw is None or not raw.strip():
        return None
    return (raw or "").strip().lower() not in _OFF_VALUES


def stored_enabled() -> bool:
    """设置页保存的值；缺失/损坏一律按「开」。"""
    try:
        payload = json.loads(read_text_with_retry(_settings_path()))
    except (FileNotFoundError, OSError, ValueError):
        return True
    if not isinstance(payload, dict):
        return True
    value = payload.get("confirmation_enabled")
    return True if value is None else bool(value)


def reload() -> None:
    global _cache
    with _lock:
        _cache = None


def is_confirmation_enabled() -> bool:
    """危险命令当前是否需要一次确认（默认 ``True``）。"""
    global _cache
    override = _env_override()
    if override is not None:
        return override
    with _lock:
        if _cache is not None:
            return _cache
    enabled = stored_enabled()
    with _lock:
        _cache = enabled
    return enabled


def set_confirmation_enabled(enabled: bool, *, updated_by: str = "") -> dict[str, Any]:
    """持久化开关；**立即生效**（确认不是权限放宽，不需要重启）。

    审计（G3/G4）由调用方写：这里拿不到可信的主体，也不该假装知道是谁。
    """
    import time

    target = bool(enabled)
    path = _settings_path()
    payload: dict[str, Any] = {}
    try:
        loaded = json.loads(read_text_with_retry(path))
        if isinstance(loaded, dict):
            payload = loaded
    except (FileNotFoundError, OSError, ValueError):
        payload = {}
    payload["confirmation_enabled"] = target
    payload["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    if updated_by:
        payload["updated_by"] = updated_by
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))
    global _cache
    with _lock:
        _cache = target
    return describe()


def describe() -> dict[str, Any]:
    enabled = is_confirmation_enabled()
    return {
        "confirmation_enabled": enabled,
        "stored_enabled": stored_enabled(),
        "env_override": _env_override() is not None,
        "env_var": ENV_VAR,
        "env_value": os.environ.get(ENV_VAR, ""),
        "categories": ["dangerous_command", "irreversible_delete"],
        "note": (
            "开启时，危险命令（格式化/分区/关机重启/递归强删）执行前要一次确认；"
            "关闭后不再询问，关闭动作本身会记入审计。"
        ),
    }
