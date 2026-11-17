"""桌宠设置后端（C-pet 组，0.4.6）：``/v1/pet``。

只做一件事：让控制台设置页和桌面桌宠窗口读同一份桌宠开关——
总开关 / 漫游 / 主动说话 / 动效。没配好、没连上时两边都显示
“没配置”，绝不编一个默认值充数。

存储：每账号一份，存在 ``pet_settings.json``（``LIUHAO_PET_SETTINGS_FILE``
可覆盖），与 ``a2b_state.json`` 同款做法（``atomic_write_text``＋文件锁）。
登录/权限/审计/备份核心逻辑一律不动；这里只是普通设置读写。
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any, Dict

logger = logging.getLogger(__name__)

_STATE_ENV = "LIUHAO_PET_SETTINGS_FILE"
_state_lock = threading.Lock()

#: 桌宠设置的字段与默认值（与桌面 ``src/desktop/pet.py`` 的 PetSettings 一致）。
DEFAULT_SETTINGS: Dict[str, Any] = {
    "pet_enabled": True,
    "roam_enabled": True,
    "proactive_speech_enabled": True,
    "reduced_motion": False,
}


def _state_path() -> str:
    from ..common.data_paths import resolve_store_path

    return resolve_store_path(
        "pet_settings.json",
        os.path.join("data", "pet_settings.json"),
        env_var=_STATE_ENV,
    )


def _clean(raw: Any) -> Dict[str, Any]:
    """只认四个布尔字段，别的一律丢掉；缺字段按默认补，不编数据。"""
    settings = dict(DEFAULT_SETTINGS)
    if isinstance(raw, dict):
        for key in DEFAULT_SETTINGS:
            if isinstance(raw.get(key), bool):
                settings[key] = raw[key]
    return settings


def get_pet_settings(principal: str) -> Dict[str, Any]:
    """读某账号的桌宠设置；没有就返回默认（不落盘、不编“已配置”）。"""
    try:
        from ..common.atomic_write import read_text_with_retry

        raw = json.loads(read_text_with_retry(_state_path()))
    except (OSError, ValueError):
        return dict(DEFAULT_SETTINGS)
    if not isinstance(raw, dict):
        return dict(DEFAULT_SETTINGS)
    per_account = raw.get("accounts")
    if isinstance(per_account, dict) and isinstance(per_account.get(principal), dict):
        return _clean(per_account[principal])
    return dict(DEFAULT_SETTINGS)


def set_pet_settings(principal: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    """写某账号的桌宠设置（只接受四个布尔字段，别的一律丢掉）。"""
    from ..common.atomic_write import atomic_write_text, exclusive_file_lock

    path = _state_path()
    with _state_lock:
        try:
            from ..common.atomic_write import read_text_with_retry

            raw = json.loads(read_text_with_retry(path))
        except (OSError, ValueError):
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        accounts = raw.get("accounts")
        if not isinstance(accounts, dict):
            accounts = {}
        merged = _clean(accounts.get(principal))
        for key in DEFAULT_SETTINGS:
            if isinstance(patch.get(key), bool):
                merged[key] = patch[key]
        accounts[str(principal)] = merged
        raw["accounts"] = accounts
        with exclusive_file_lock(f"{path}.lock", description="桌宠设置"):
            atomic_write_text(
                path,
                json.dumps(raw, ensure_ascii=False, indent=2, sort_keys=True),
            )
    return dict(merged)


def wake_status() -> Dict[str, Any]:
    """语音唤醒接入状态：与桌面 ``pet.WAKE_ENGINE`` 同一份老实话。"""
    from ..desktop.pet import WAKE_ENGINE

    return dict(WAKE_ENGINE)


# ---------------------------------------------------------------------------
# HTTP 路由（/v1/pet；挂载见 src/gateway/main.py，与 a2a/a2b 一样要人类登录）
# ---------------------------------------------------------------------------

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from .account_context import require_account_principal

router = APIRouter(prefix="/v1/pet", tags=["pet"])


def _principal(context=Depends(require_account_principal)):
    return context


class PetSettingsBody(BaseModel):
    pet_enabled: bool | None = Field(default=None)
    roam_enabled: bool | None = Field(default=None)
    proactive_speech_enabled: bool | None = Field(default=None)
    reduced_motion: bool | None = Field(default=None)


@router.get("/settings")
def get_settings(context=Depends(_principal)) -> Dict[str, Any]:
    return get_pet_settings(context.principal)


@router.put("/settings")
def put_settings(body: PetSettingsBody, context=Depends(_principal)) -> Dict[str, Any]:
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    return set_pet_settings(context.principal, patch)


@router.get("/wake")
def get_wake() -> Dict[str, Any]:
    """语音唤醒状态：本地引擎未接入就是未接入，不假装能听。"""
    return wake_status()
