"""工作台名字端点：全站唯一的名字配置来源。

* ``GET  /v1/workspace/name`` —— 公开：登录页、顶栏、助手名在未登录
  时也要显示名字，所以不挂鉴权；只返回名字，不含任何敏感信息。
* ``PUT  /v1/workspace/name`` —— 仅根账号（Owner）：名字是全站共用
  的，成员只能看、不能改；与 ``/v1/settings/logs``、``/v1/network/research``
  同一个“成员可读、写归根账号”口径。

名字本身存在数据目录的 ``workspace_settings.json``（见 ``src/brand.py``
的 ``resolve_brand`` / ``set_workspace_name``），出厂默认值来自
``src/brand.json``（Jarvis · 贾维斯）。前端只读这个端点（失败时用
``brand.ts`` 的出厂默认兜底），不另写一套名字。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..brand import resolve_brand, set_workspace_name
from .account_context import deny as _deny_account
from .account_context import require_account_principal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/workspace", tags=["workspace"])


class WorkspaceNameUpdate(BaseModel):
    name_zh: str = Field(min_length=1, max_length=40)
    name_en: Optional[str] = Field(default=None, max_length=80)


@router.get("/name")
def get_workspace_name() -> dict[str, Any]:
    return resolve_brand()


@router.put("/name")
def update_workspace_name(
    payload: WorkspaceNameUpdate,
    context=Depends(require_account_principal),
) -> dict[str, Any]:
    if not context.is_root:
        raise _deny_account(
            403,
            "SETTINGS_ROOT_ONLY",
            "工作台名字由最高权限账号统一管理，当前账号只能查看；修改没有生效。",
        )
    try:
        result = set_workspace_name(payload.name_zh, payload.name_en)
    except ValueError as exc:
        raise _deny_account(400, "INVALID_WORKSPACE_NAME", str(exc)) from exc
    logger.info("工作台名字已更新为：%s", result["lockup"])
    return result
