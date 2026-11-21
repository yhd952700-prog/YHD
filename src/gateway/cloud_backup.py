"""云备份快照：口令加密、可带走、带滚动保留的本地快照层（大升级第二季 V10）。

与既有两套备份的分工（只许这一套口径，不另起炉灶）
----------------------------------------------------
* ``src/backup/``（每日自动备份）：本机文件级归档，密钥是**机器绑定**的随机
  密钥（``backup.key``，DPAPI 包裹）——同机回滚用，**带不走**：换机解不开。
* ``src/gateway/account_backup.py``（账号凭据包）：口令加密的整机迁移格式，
  用户手动导出一个文件、到新机导入认领；一次性、防重放、限次。
* **本模块（云备份快照）**：补的是两者之间的缺口——「定期把整份数据加密
  存成快照放在本地（将来可上传云盘），机器丢了/坏了也能在新机恢复」。
  实现上**完全复用凭据包的数据面与加密信封**：

  - 数据面 = ``export_bundle`` 的收集口径（与打磨第四轮迁移面逐项一致：
    身份/凭据哈希/模型配置元数据/工作区文件/员工/目标/知识库/工作台分组与
    Bot/企业知识文档/记忆/会话/内核记忆/个人画像），**模型 Key 一律不随包**
    这条老规矩原样继承；
  - 加密 = ``src.kernels.identity._backup`` 的信封（口令 →
    PBKDF2-HMAC-SHA256 派生 256-bit 密钥 → AES-256-GCM），快照文件本身
    就是一份合法的凭据包，恢复直接走既有两条恢复路径，不新造解密口径；
  - 本模块只多做三件事：**快照落盘管理**（编号/清单/校验和）、**滚动保留**
    （默认 5 份，见 ``DEFAULT_RETENTION`` 的理由）、**恢复分发**
    （全新实例走认领 ``import_bundle``；已初始化实例走合并
    ``import_bundle_for_account``——只合不覆盖，与既有口径一致）。

硬约束
------
* 口令只来自调用方参数：**不落盘、不进日志、不进审计 details、不进快照
  元数据**；元数据（sidecar JSON）只有计数、时间、校验和这类可公开信息。
* 快照默认只存本地；**云端上传默认关**，本阶段只留接口与人话指引，
  不实际上传任何用户数据。
* 每份快照的 ``bundle_id`` 唯一：恢复沿用凭据包的防重放语义——同一份快照
  在同一实例上只能成功恢复一次（认领或合并都会写回执）。要再次恢复，
  用另一份快照，或先重新做一份快照。这是既有安全口径，不在本模块放宽。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from ..kernels.identity import _accounts, get_identity_manager
from . import account_backup as _ab
from .account_context import (
    AccountContext,
)
from .account_context import (
    deny as _deny,
)
from .account_context import (
    from_account_error as _from_account_error,
)
from .account_context import (
    require_account_principal as _require_account_principal,
)

logger = logging.getLogger(__name__)

#: 快照元数据（sidecar）格式标记；信封本体沿用凭据包格式，不新造。
SNAPSHOT_META_FORMAT = "liuhao.cloud.snapshot.meta.v1"
#: 快照目录环境变量（测试/迁移可覆盖）；缺省在数据目录下 cloud-backups/。
SNAPSHOT_DIR_ENV = "LIUHAO_CLOUD_BACKUP_DIR"
#: 保留份数环境变量，优先级高于 settings 文件。
RETENTION_ENV = "LIUHAO_CLOUD_BACKUP_RETENTION"
#: 默认保留份数。理由：每份快照是全量包（工作区文件也随包，体积可达数十
#: MB），5 份够覆盖「最近一周内若干个回滚点」又不至于把磁盘吃满；本机文件
#: 级自动备份（保留 7 天）是第一层，云备份快照是可带走的第二层，不需要密。
DEFAULT_RETENTION = 5
MIN_RETENTION = 1
MAX_RETENTION = 100

_SETTINGS_FILE = "settings.json"
_META_SUFFIX = ".meta.json"
_BUNDLE_SUFFIX = ".bundle.json"
_lock = threading.Lock()

_SNAPSHOT_ID_RE = re.compile(r"^snap-\d{8}T\d{6}Z-[0-9a-f]{6}$")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _error(status: int, code: str, message: str) -> _accounts.AccountError:
    return _accounts.AccountError(status, code, message)


# --------------------------------------------------------------------------- #
# 目录与设置
# --------------------------------------------------------------------------- #


def snapshots_dir() -> Path:
    """快照目录：环境变量优先，否则 ``<数据目录>/cloud-backups``。"""
    explicit = (os.environ.get(SNAPSHOT_DIR_ENV) or "").strip()
    if explicit:
        return Path(explicit).expanduser()
    from src.common.engines import data_dir

    return data_dir() / "cloud-backups"


def _settings_path() -> Path:
    return snapshots_dir() / _SETTINGS_FILE


def _read_settings() -> Dict[str, Any]:
    try:
        raw = json.loads(_settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _write_settings(settings: Dict[str, Any]) -> None:
    directory = snapshots_dir()
    directory.mkdir(parents=True, exist_ok=True)
    target = _settings_path()
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    tmp.replace(target)


def effective_retention() -> int:
    """保留份数：环境变量 > settings 文件 > 默认 5；越界钳制到 [1, 100]。"""
    raw_env = (os.environ.get(RETENTION_ENV) or "").strip()
    value: Any = None
    if raw_env:
        try:
            value = int(raw_env)
        except ValueError:
            value = None
    if value is None:
        value = _read_settings().get("retention")
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = DEFAULT_RETENTION
    return max(MIN_RETENTION, min(MAX_RETENTION, number))


def set_retention(count: int) -> int:
    """把保留份数写入 settings（聊天/设置页共用这一条路径）。"""
    try:
        number = int(count)
    except (TypeError, ValueError):
        raise _error(400, "CLOUD_BACKUP_RETENTION_INVALID", "保留份数必须是整数")
    number = max(MIN_RETENTION, min(MAX_RETENTION, number))
    with _lock:
        settings = _read_settings()
        settings["retention"] = number
        _write_settings(settings)
    return number


def cloud_upload_enabled() -> bool:
    """云端上传开关（默认关）。开启也只是允许调用上传接口，不代表已接通。"""
    return bool(_read_settings().get("cloud_upload_enabled", False))


# --------------------------------------------------------------------------- #
# 快照清单（sidecar 元数据：只有可公开信息，绝不含口令与内容）
# --------------------------------------------------------------------------- #


def _meta_path(snapshot_id: str) -> Path:
    return snapshots_dir() / f"{snapshot_id}{_META_SUFFIX}"


def _bundle_path(snapshot_id: str) -> Path:
    return snapshots_dir() / f"{snapshot_id}{_BUNDLE_SUFFIX}"


def _check_snapshot_id(snapshot_id: str) -> str:
    text = str(snapshot_id or "").strip()
    if not _SNAPSHOT_ID_RE.match(text):
        raise _error(400, "CLOUD_BACKUP_SNAPSHOT_INVALID", "快照编号无效")
    return text


def _read_meta(snapshot_id: str) -> Dict[str, Any]:
    path = _meta_path(snapshot_id)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise _error(404, "CLOUD_BACKUP_SNAPSHOT_NOT_FOUND", "没有这份快照")
    except (OSError, ValueError):
        raise _error(500, "CLOUD_BACKUP_META_BROKEN", "快照清单损坏，无法读取")
    if not isinstance(raw, dict) or raw.get("format") != SNAPSHOT_META_FORMAT:
        raise _error(500, "CLOUD_BACKUP_META_BROKEN", "快照清单损坏，无法读取")
    return raw


def list_snapshots() -> List[Dict[str, Any]]:
    """按时间从新到旧列出全部快照的元数据（不读包体）。"""
    directory = snapshots_dir()
    if not directory.is_dir():
        return []
    metas: List[Dict[str, Any]] = []
    for path in directory.glob(f"*{_META_SUFFIX}"):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning("cloud backup meta unreadable: %s", path.name)
            continue
        if isinstance(raw, dict) and raw.get("format") == SNAPSHOT_META_FORMAT:
            metas.append(raw)
    metas.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return metas


def _public_meta(meta: Dict[str, Any]) -> Dict[str, Any]:
    """对外清单项：只给人看得懂的字段。"""
    return {
        "snapshot_id": meta.get("snapshot_id"),
        "created_at": meta.get("created_at"),
        "label": meta.get("label") or "",
        "scope": meta.get("scope"),
        "members": meta.get("members", 0),
        "files": meta.get("files", 0),
        "bytes": meta.get("bytes", 0),
        "bundle_bytes": meta.get("bundle_bytes", 0),
    }


def cloud_backup_status() -> Dict[str, Any]:
    """云备份现状：几份快照、保留几份、云上传开没开。"""
    metas = list_snapshots()
    total = sum(int(meta.get("bundle_bytes") or 0) for meta in metas)
    return {
        "snapshots_dir": str(snapshots_dir()),
        "count": len(metas),
        "total_bytes": total,
        "retention": effective_retention(),
        "latest": _public_meta(metas[0]) if metas else None,
        "cloud_upload": {
            "enabled": cloud_upload_enabled(),
            "provider": None,
            "guidance": (
                "云端上传默认关：快照先存在本机。想放到云盘防丢，把快照目录"
                "里的 .bundle.json 文件拷进你的云盘即可（文件已加密，没口令"
                "打不开）；自动上传功能开启后会在这里显示进度。"
            ),
        },
    }


# --------------------------------------------------------------------------- #
# 创建 / 删除 / 修剪
# --------------------------------------------------------------------------- #


def create_snapshot(
    *,
    actor: str,
    scope: str,
    passphrase: str,
    is_root: bool,
    label: str = "",
) -> Dict[str, Any]:
    """做一份加密快照：收集与加密全部复用凭据包，本模块只负责落盘与保留。

    口令只在内存里传给 ``export_bundle``，不写任何文件与日志。
    """
    manager = get_identity_manager()
    subtree = manager.subtree_principals(actor)
    result = _ab.export_bundle(
        actor=actor,
        scope=scope,
        passphrase=passphrase,
        subtree=subtree,
        is_root=is_root,
    )
    bundle = result["bundle"]
    bundle_text = json.dumps(bundle, ensure_ascii=False)
    bundle_bytes = bundle_text.encode("utf-8")

    stamp = _now().strftime("%Y%m%dT%H%M%SZ")
    snapshot_id = f"snap-{stamp}-{uuid.uuid4().hex[:6]}"
    manifest = result.get("manifest") or {}
    meta = {
        "format": SNAPSHOT_META_FORMAT,
        "snapshot_id": snapshot_id,
        "bundle_id": bundle.get("bundle_id"),
        "created_at": _now_iso(),
        "label": str(label or "")[:80],
        "scope": result.get("scope"),
        "exported_by": actor,
        "members": result.get("members", 0),
        "files": manifest.get("files", 0),
        "bytes": manifest.get("bytes", 0),
        "bundle_bytes": len(bundle_bytes),
        "bundle_sha256": hashlib.sha256(bundle_bytes).hexdigest(),
    }
    with _lock:
        directory = snapshots_dir()
        directory.mkdir(parents=True, exist_ok=True)
        _bundle_path(snapshot_id).write_text(bundle_text, encoding="utf-8")
        _meta_path(snapshot_id).write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        pruned = _prune_locked()
    manager.record_event(
        action="account.cloud_backup.snapshot_created",
        actor=actor,
        target=snapshot_id,
        outcome="allowed",
        details={
            "scope": meta["scope"],
            "members": meta["members"],
            "files": meta["files"],
            "bytes": meta["bytes"],
            "bundle_bytes": meta["bundle_bytes"],
            "pruned": pruned,
        },
    )
    return {
        "ok": True,
        "snapshot": _public_meta(meta),
        "pruned": pruned,
        "retention": effective_retention(),
        "notice": result.get("notice", ""),
    }


def _prune_locked() -> List[str]:
    """滚动保留：超出保留份数的最旧快照连包带清单一起删。调用方须持锁。"""
    retention = effective_retention()
    metas = list_snapshots()
    pruned: List[str] = []
    for meta in metas[retention:]:
        snapshot_id = str(meta.get("snapshot_id") or "")
        if not _SNAPSHOT_ID_RE.match(snapshot_id):
            continue
        for path in (_bundle_path(snapshot_id), _meta_path(snapshot_id)):
            try:
                path.unlink()
            except OSError:
                logger.warning("cloud backup prune failed: %s", path.name)
        pruned.append(snapshot_id)
    return pruned


def delete_snapshot(*, actor: str, snapshot_id: str) -> Dict[str, Any]:
    """删一份快照（包体 + 清单）。"""
    snapshot_id = _check_snapshot_id(snapshot_id)
    _read_meta(snapshot_id)  # 不存在直接 404
    with _lock:
        for path in (_bundle_path(snapshot_id), _meta_path(snapshot_id)):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
    get_identity_manager().record_event(
        action="account.cloud_backup.snapshot_deleted",
        actor=actor,
        target=snapshot_id,
        outcome="allowed",
        details={},
    )
    return {"ok": True, "snapshot_id": snapshot_id}


# --------------------------------------------------------------------------- #
# 恢复：分发到既有两条恢复路径，语义与凭据包完全一致
# --------------------------------------------------------------------------- #


def load_snapshot_bundle(snapshot_id: str) -> Dict[str, Any]:
    """读出快照包体并核对校验和；对不上直接拒绝，绝不把坏包送进恢复路径。"""
    snapshot_id = _check_snapshot_id(snapshot_id)
    meta = _read_meta(snapshot_id)
    try:
        raw = _bundle_path(snapshot_id).read_bytes()
    except FileNotFoundError:
        raise _error(404, "CLOUD_BACKUP_SNAPSHOT_NOT_FOUND", "快照文件不见了")
    digest = hashlib.sha256(raw).hexdigest()
    if meta.get("bundle_sha256") and digest != meta["bundle_sha256"]:
        raise _error(
            400,
            "CLOUD_BACKUP_SNAPSHOT_CORRUPT",
            "快照文件校验对不上，可能已损坏；请换一份快照恢复",
        )
    try:
        bundle = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise _error(
            400, "CLOUD_BACKUP_SNAPSHOT_CORRUPT", "快照文件已损坏，无法读取"
        )
    if not isinstance(bundle, dict):
        raise _error(
            400, "CLOUD_BACKUP_SNAPSHOT_CORRUPT", "快照文件已损坏，无法读取"
        )
    return bundle


def restore_snapshot(
    *,
    snapshot_id: str,
    passphrase: str,
    actor: Optional[str] = None,
) -> Dict[str, Any]:
    """从一份本地快照恢复。

    * 本机还没有账号树 → 走认领（``import_bundle``）：账号树、凭据、全部
      数据一次恢复，等同换机导入；
    * 本机已初始化 → 走合并（``import_bundle_for_account``，须给 ``actor``）：
      只把快照里属于本人的数据合回来，按主键去重、绝不覆盖现场数据、
      绝不碰账号树与登录密码。

    防重放沿用凭据包口径：同一份快照在同一实例只能成功恢复一次。
    """
    bundle = load_snapshot_bundle(snapshot_id)
    manager = get_identity_manager()
    if manager.account_root() is None:
        result = _ab.import_bundle(bundle=bundle, passphrase=passphrase)
        result["snapshot_id"] = snapshot_id
        result["restore_mode"] = "fresh_claim"
        return result
    if not actor:
        raise _error(
            400,
            "CLOUD_BACKUP_ACTOR_REQUIRED",
            "本机已有账号，合并恢复需要先登录",
        )
    result = _ab.import_bundle_for_account(
        bundle=bundle, passphrase=passphrase, actor=actor
    )
    result["snapshot_id"] = snapshot_id
    result["restore_mode"] = "existing_account_merge"
    return result


# --------------------------------------------------------------------------- #
# 云端上传：只留接口与指引，默认关，本阶段不实际上传
# --------------------------------------------------------------------------- #


def upload_snapshot(snapshot_id: str, *, provider: Any = None) -> Dict[str, Any]:
    """把一份快照上传到云端（接口占位）。

    默认关：开关没开直接返回人话指引；开关开了但还没有接入任何云盘
    提供方（``provider`` 为空）时如实返回「暂未接通」，绝不假报已上传。
    真正的提供方接入后，只需实现 ``provider.upload(path, meta)`` 并在
    这里注入，快照格式与本地管理都不用动。
    """
    snapshot_id = _check_snapshot_id(snapshot_id)
    meta = _read_meta(snapshot_id)
    if not cloud_upload_enabled():
        return {
            "ok": False,
            "status": "disabled",
            "snapshot_id": snapshot_id,
            "message": (
                "云端上传还没开启。快照已经安全存在本机；想先防丢，"
                "把这份快照文件拷进你的云盘就行（已加密，没口令打不开）："
                + str(_bundle_path(snapshot_id))
            ),
        }
    if provider is None:
        return {
            "ok": False,
            "status": "unavailable",
            "snapshot_id": snapshot_id,
            "message": "云端上传已开启，但还没有接通任何云盘，暂时传不了；快照仍在本机。",
        }
    result = provider.upload(_bundle_path(snapshot_id), meta)
    return {"ok": True, "status": "uploaded", "snapshot_id": snapshot_id, "result": result}


# --------------------------------------------------------------------------- #
# 已登录接口：状态/清单/创建/恢复/删除/保留份数
# --------------------------------------------------------------------------- #

router = APIRouter(prefix="/v1/accounts/cloud-backup", tags=["accounts"])


class _CreateSnapshotRequest(BaseModel):
    passphrase: str = Field(..., min_length=8, max_length=256)
    scope: str = "all"
    label: str = ""


class _RestoreSnapshotRequest(BaseModel):
    snapshot_id: str
    passphrase: str = Field(..., min_length=8, max_length=256)


class _RetentionRequest(BaseModel):
    retention: int


@router.get("")
def cloud_backup_overview(
    context: AccountContext = Depends(_require_account_principal),
) -> Dict[str, Any]:
    """云备份现状 + 快照清单（已登录）。"""
    status = cloud_backup_status()
    status["snapshots"] = [_public_meta(meta) for meta in list_snapshots()]
    return status


@router.post("/snapshots")
def cloud_backup_create(
    request: Request,
    payload: _CreateSnapshotRequest,
    context: AccountContext = Depends(_require_account_principal),
) -> Dict[str, Any]:
    """做一份加密快照。敏感度与凭据包导出相同：默认只接受本机来源。"""
    from .accounts import request_is_local  # 延迟导入，避免模块级循环

    if not request_is_local(request) and not _ab.remote_export_enabled():
        raise _deny(
            403,
            "CLOUD_BACKUP_LOCAL_ONLY",
            "做快照只允许在本机操作：请在这台机器的控制台重试。",
        )
    try:
        return create_snapshot(
            actor=context.principal,
            scope=payload.scope,
            passphrase=payload.passphrase,
            is_root=context.is_root,
            label=payload.label,
        )
    except _accounts.AccountError as exc:
        raise _from_account_error(exc) from exc
    except Exception:  # noqa: BLE001 - 账号面不得 500 裸奔
        logger.exception("cloud backup snapshot creation failed")
        raise _deny(500, "CLOUD_BACKUP_INTERNAL_ERROR", "服务暂时不可用") from None


@router.post("/restore")
def cloud_backup_restore(
    payload: _RestoreSnapshotRequest,
    context: AccountContext = Depends(_require_account_principal),
) -> Dict[str, Any]:
    """从本地快照合并恢复到当前账号（全新实例认领走登录页的导入入口）。"""
    try:
        return restore_snapshot(
            snapshot_id=payload.snapshot_id,
            passphrase=payload.passphrase,
            actor=context.principal,
        )
    except _accounts.AccountError as exc:
        raise _from_account_error(exc) from exc
    except Exception:  # noqa: BLE001
        logger.exception("cloud backup restore failed")
        raise _deny(500, "CLOUD_BACKUP_INTERNAL_ERROR", "服务暂时不可用") from None


@router.delete("/snapshots/{snapshot_id}")
def cloud_backup_delete(
    snapshot_id: str,
    context: AccountContext = Depends(_require_account_principal),
) -> Dict[str, Any]:
    try:
        return delete_snapshot(actor=context.principal, snapshot_id=snapshot_id)
    except _accounts.AccountError as exc:
        raise _from_account_error(exc) from exc
    except Exception:  # noqa: BLE001
        logger.exception("cloud backup delete failed")
        raise _deny(500, "CLOUD_BACKUP_INTERNAL_ERROR", "服务暂时不可用") from None


@router.put("/retention")
def cloud_backup_retention(
    payload: _RetentionRequest,
    context: AccountContext = Depends(_require_account_principal),
) -> Dict[str, Any]:
    try:
        value = set_retention(payload.retention)
    except _accounts.AccountError as exc:
        raise _from_account_error(exc) from exc
    return {"ok": True, "retention": value}
