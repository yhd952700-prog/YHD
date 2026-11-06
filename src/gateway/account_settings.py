"""账号面本机策略开关（当前只有"允许自助注册"）。

为什么单独一个 JSON 文件，而不是塞进账号树
------------------------------------------
账号树描述"谁在什么位置、持有什么作用域"；本机开关描述"这台机器开不开放
自助注册"，两者生命周期不同（备份/迁移凭据包不应带走策略位）。单独存一份
小文件可以在 file / sqlite 两种身份后端下保持一致行为，且不需要改账号树
schema 与备份格式。

写入走"临时文件 + ``os.replace``"：要么旧内容，要么新内容，不会留下半截 JSON。
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from typing import Any, Dict

#: 产品默认值：自助注册默认开启（主账号可随时关闭）。
DEFAULT_ALLOW_SELF_REGISTRATION = True

#: 显式覆盖路径；测试用隔离实例时指向临时目录，避免污染仓库数据。
SETTINGS_FILE_ENV = "LIUHAO_ACCOUNT_SETTINGS_FILE"
_KEY = "allow_self_registration"

_lock = threading.Lock()


def _settings_path() -> str:
    explicit = (os.environ.get(SETTINGS_FILE_ENV) or "").strip()
    if explicit:
        return explicit
    base = (
        (os.environ.get("LIUHAO_DATA_DIR") or "").strip()
        or (os.environ.get("LIUHAO_WORKSPACE_ROOT") or "").strip()
        or "data"
    )
    return os.path.join(base, "account_settings.json")


def load_settings() -> Dict[str, Any]:
    """读策略文件；文件不存在/损坏时返回空字典（调用方走默认值）。"""
    try:
        with open(_settings_path(), "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def allow_self_registration() -> bool:
    return bool(load_settings().get(_KEY, DEFAULT_ALLOW_SELF_REGISTRATION))


def set_allow_self_registration(allow: bool) -> Dict[str, Any]:
    """持久化开关，返回写入后的完整策略。"""
    path = _settings_path()
    with _lock:
        settings = load_settings()
        settings[_KEY] = bool(allow)
        parent = os.path.dirname(os.path.abspath(path)) or "."
        os.makedirs(parent, exist_ok=True)
        handle, temp = tempfile.mkstemp(dir=parent, prefix=".account-settings-", suffix=".json")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(settings, stream, ensure_ascii=False, indent=2, sort_keys=True)
            os.replace(temp, path)
        except Exception:
            try:
                os.unlink(temp)
            except OSError:
                pass
            raise
    return settings
