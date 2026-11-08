"""日志占用与保留设置：让用户在设置页看得见、改得了、能立即清理。

对应 ``src/logging_config.py`` 的保留策略（默认 14 天 / 256 MiB）。这里的
三个端点只暴露数字与可读文案，不回传绝对路径：

* ``GET    /v1/settings/logs``        当前占用、生效中的保留值、上次清理结果
* ``PUT    /v1/settings/logs``        保存保留天数 / 总量上限（重启仍生效）
* ``POST   /v1/settings/logs/prune``  立即清理一轮，并返回本次保留/删除清单

环境变量仍然优先于界面保存值：``LIUHAO_LOG_RETENTION_DAYS`` /
``LIUHAO_LOG_RETENTION_BYTES``。被环境变量覆盖时，界面会收到 ``env_override``
标记并提示「改了不生效」。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..logging_config import (
    log_usage,
    prune_log_retention,
    read_retention_report,
    resolve_log_file,
    retention_env_overrides,
    retention_limits,
    save_retention_limits,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/settings/logs", tags=["settings"])

#: 界面上的「上限」以 MiB 输入，服务端按字节校验。
_MAX_BYTES = 1024 * 1024 * 1024 * 1024


def _log_dir() -> Path:
    return resolve_log_file(Path("logs") / "liuhao.log").parent


def _state() -> dict[str, Any]:
    days, cap = retention_limits()
    usage = log_usage(_log_dir())
    usage.pop("log_dir", None)
    usage.update(
        {
            "retention_days": days,
            "max_total_bytes": cap,
            "env_override": retention_env_overrides(),
            "last_report": read_retention_report(_log_dir()),
            "note": "只清理旧的日志备份（*.log.N），正在写入的日志不会删除。",
        }
    )
    return usage


class RetentionUpdate(BaseModel):
    retention_days: int = Field(ge=0, le=3650)
    max_total_bytes: int = Field(ge=0, le=_MAX_BYTES)


@router.get("")
def get_log_settings() -> dict[str, Any]:
    return _state()


@router.put("")
def update_log_settings(payload: RetentionUpdate) -> dict[str, Any]:
    save_retention_limits(payload.retention_days, payload.max_total_bytes)
    logger.info(
        "日志保留设置已更新：%s 天 / %s 字节", payload.retention_days, payload.max_total_bytes
    )
    return _state()


@router.post("/prune")
def prune_logs() -> dict[str, Any]:
    report = prune_log_retention(_log_dir())
    return {"state": _state(), "report": report}
