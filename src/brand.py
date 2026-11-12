"""品牌字面单一来源。

所有对外主名、中文名与品类都从 ``src/brand.json`` 读取；前端
``apps/console/console/src/brand.ts`` 引用同一份 JSON。
禁止在代码或文档里另写一套主名。

工作台名字可改（2026-11-12 用户要求：Joseph 这份叫“贾维斯”，
别人用的时候自己起名字；改完整个界面都显示新名字）：
``src/brand.json`` 只提供**出厂默认值**（Jarvis · 贾维斯），运行时
生效的名字存在数据目录的 ``workspace_settings.json`` 里
（与 ``account_settings.json`` / ``privacy_settings.json`` 同一套
小 JSON 配置机制：临时文件 + ``os.replace`` 原子写，环境变量
``LIUHAO_WORKSPACE_SETTINGS_FILE`` 可覆盖路径、供测试隔离）。

读取口径（全站唯一）：
``resolve_brand()`` —— 有用户改名就返回改后的名字，没有就返回
``brand.json`` 的默认值。后端所有要显示产品名的地方（网关标题、
助手 system prompt、桌面关于页）都应走它；模块级常量
``BRAND_NAME_EN`` / ``BRAND_NAME_ZH`` / ``BRAND_LOCKUP`` 只保留
出厂默认值，供构建期门禁与测试钉住默认值使用。
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from src.common.atomic_write import (
    atomic_write_text,
    exclusive_file_lock,
    read_text_with_retry,
)

_DATA: dict[str, Any] = json.loads(
    Path(__file__).with_name("brand.json").read_text(encoding="utf-8")
)

BRAND_NAME_EN: str = str(_DATA["name_en"])
BRAND_NAME_ZH: str = str(_DATA["name_zh"])
BRAND_LOCKUP: str = f"{BRAND_NAME_EN} · {BRAND_NAME_ZH}"
BRAND_CATEGORY: str = str(_DATA["category"])

#: 工作台名字配置文件的显式覆盖路径；测试用隔离实例时指向临时目录。
WORKSPACE_SETTINGS_ENV = "LIUHAO_WORKSPACE_SETTINGS_FILE"
_WORKSPACE_SETTINGS_FILE = "workspace_settings.json"
_NAME_ZH_KEY = "name_zh"
_NAME_EN_KEY = "name_en"

_lock = threading.Lock()


def _workspace_settings_path() -> str:
    from src.common.data_paths import resolve_store_path

    return resolve_store_path(
        _WORKSPACE_SETTINGS_FILE,
        os.path.join("data", _WORKSPACE_SETTINGS_FILE),
        env_var=WORKSPACE_SETTINGS_ENV,
    )


def load_workspace_settings() -> dict[str, Any]:
    """读工作台名字配置文件；不存在/损坏时返回空字典（走默认值）。"""
    try:
        raw = json.loads(read_text_with_retry(_workspace_settings_path()))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _clean_name(value: Any, *, field: str, max_len: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} 必须是文字")
    cleaned = " ".join(value.split()).strip()
    if not cleaned:
        raise ValueError(f"{field} 不能为空")
    if len(cleaned) > max_len:
        raise ValueError(f"{field} 最多 {max_len} 个字")
    return cleaned


def resolve_brand() -> dict[str, Any]:
    """当前生效的品牌名：用户改名优先，出厂默认兜底。

    返回 ``name_en`` / ``name_zh`` / ``lockup`` / ``category`` /
    ``customized``。中英文相同时 lockup 只写一遍（例如用户只起了一
    个中文名“阿强”，界面显示“阿强”而不是“阿强 · 阿强”）。
    """
    settings = load_workspace_settings()
    name_zh = str(settings.get(_NAME_ZH_KEY) or BRAND_NAME_ZH)
    name_en = str(settings.get(_NAME_EN_KEY) or BRAND_NAME_EN)
    customized = bool(settings.get(_NAME_ZH_KEY) or settings.get(_NAME_EN_KEY))
    lockup = name_zh if name_en == name_zh else f"{name_en} · {name_zh}"
    return {
        "name_en": name_en,
        "name_zh": name_zh,
        "lockup": lockup,
        "category": BRAND_CATEGORY,
        "customized": customized,
    }


def set_workspace_name(name_zh: str, name_en: str | None = None) -> dict[str, Any]:
    """保存用户起的工作台名字，返回保存后 ``resolve_brand()`` 的结果。

    ``name_en`` 不填/留空时与中文名相同（lockup 只显示一遍）。
    校验失败抛 ``ValueError``，不写文件。
    """
    cleaned_zh = _clean_name(name_zh, field="中文名", max_len=40)
    cleaned_en = (
        _clean_name(name_en, field="英文名", max_len=80)
        if name_en is not None and str(name_en).strip()
        else cleaned_zh
    )
    path = _workspace_settings_path()
    with _lock:
        with exclusive_file_lock(f"{path}.lock", description="工作台名字"):
            settings = load_workspace_settings()
            settings[_NAME_ZH_KEY] = cleaned_zh
            settings[_NAME_EN_KEY] = cleaned_en
            atomic_write_text(
                path,
                json.dumps(settings, ensure_ascii=False, indent=2, sort_keys=True),
            )
    return resolve_brand()
