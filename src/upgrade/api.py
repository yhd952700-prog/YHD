"""HTTP contract for the self-upgrade module.

The router is intentionally standalone: the gateway can mount it with
``app.include_router(upgrade_api.router)`` without this module importing (or
changing) ``src.gateway``. Authentication still uses the single bearer-token
validation in ``src.gateway.policy``; only the "administrator" decision lives
here, because upgrade requires an explicit human-sovereignty/admin claim.

Long-running operations (``apply``/``rollback``) are **synchronous** by
design: the HTTP response returns only after the manager finished, with
``mode: "synchronous"`` and a warning that it may take minutes. Progress and
the final failure are persisted by the manager and can be polled through
``GET /v1/upgrade/status``.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Dict, Iterator, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from src.common.redaction import redact_log_text

from .errors import RollbackError, UpgradeError, VersionReadError

if TYPE_CHECKING:  # pragma: no cover - type checking only
    from packaging.version import Version

    from .manager import UpgradeManager

# Heavy upgrade modules (manager -> selfcheck -> startup_probe, downloader,
# packaging) are imported inside the handlers instead of at module scope. The
# gateway mounts this router during cold start, and paying ~80ms of import cost
# before the first request would undo the startup work done in 4c6a7eb0.

SOURCE_NOT_CONFIGURED = (
    "未配置更新源：请设置 LIUHAO_UPGRADE_RELEASE_DIR（本地发布目录）或 "
    "LIUHAO_UPGRADE_MANIFEST_URL（HTTP 清单地址）"
)
SYNC_WARNING = (
    "同步执行，可能耗时数分钟；请前端延长请求超时，并通过 GET /v1/upgrade/status 查看进度。"
)

_ADMIN_ROLES = frozenset({"admin", "administrator", "owner"})
# L0 is the highest human sovereignty scope. L7 is explicitly the LOWEST
# privilege scope (see ``src/gateway/auth.py``), and authorisation derivation
# fails closed to L7 -- accepting it here would hand the most restricted
# account the most destructive surface (apply/rollback the running system).
_ADMIN_SCOPES = frozenset({"L0"})


class ApplyRequest(BaseModel):
    confirm: bool = Field(
        default=False,
        description="必须为 true；缺少时返回 400，防止误触升级。",
    )
    version: Optional[str] = Field(
        default=None,
        description="必须明确指定要安装的版本号；与当前可升级版本不一致时返回 400。",
    )


class RollbackRequest(BaseModel):
    confirm: bool = Field(
        default=False,
        description="必须为 true；缺少时返回 400，防止误触回滚。",
    )
    backup_id: Optional[str] = Field(
        default=None,
        description="可选；不传时回滚到最近一次成功升级前的备份。",
    )


def _bearer_payload(
    x_liuhao_token: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
):
    """Defer ``src.gateway.policy`` import until a request actually arrives.

    Importing the gateway policy at module scope pulls in ``src.gateway`` ->
    ``main`` -> ``app = get_app()``, which imports this module back — a
    circular import whenever ``src.upgrade.api`` is imported first.
    """
    from ..gateway.policy import require_bearer_payload

    return require_bearer_payload(x_liuhao_token, authorization)


def _is_admin_payload(payload: Any) -> bool:
    """L0 sovereignty or an explicit admin role = full upgrade diagnostics."""
    metadata = getattr(payload, "metadata", None)
    metadata = metadata if isinstance(metadata, dict) else {}
    scope = str(metadata.get("scope") or "")
    claims = {
        str(item).lower()
        for item in (
            (getattr(payload, "roles", None) or [])
            + (getattr(payload, "permissions", None) or [])
        )
    }
    return scope in _ADMIN_SCOPES or bool(claims & _ADMIN_ROLES)


def require_upgrade_viewer(payload=Depends(_bearer_payload)) -> tuple[str, bool]:
    """Any authenticated human may read upgrade status, at two detail levels.

    The status body carries host infrastructure (install/data/backup paths and
    drive letters), so a member only gets the conclusion-level payload built by
    :func:`_member_status_payload`. ``(subject, is_admin)`` lets the route decide
    whether the full diagnostic body is allowed. Write operations still go
    through :func:`require_upgrade_admin`.
    """
    subject = getattr(payload, "sub", None)
    if not subject:
        raise HTTPException(
            status_code=401, detail="登录状态已失效，请重新登录后再试。"
        )
    metadata = getattr(payload, "metadata", None)
    metadata = metadata if isinstance(metadata, dict) else {}
    if metadata.get("kind") != "human":
        raise HTTPException(
            status_code=403,
            detail="升级状态需要用个人账号查看，服务账号不可读取。",
        )
    return str(subject), _is_admin_payload(payload)


def require_upgrade_admin(payload=Depends(_bearer_payload)) -> str:
    """Allow only a human administrator (L0 sovereignty or admin role)."""
    subject = getattr(payload, "sub", None)
    if not subject:
        raise HTTPException(
            status_code=401, detail="登录状态已失效，请重新登录后再试。"
        )
    metadata = getattr(payload, "metadata", None)
    metadata = metadata if isinstance(metadata, dict) else {}
    if metadata.get("kind") != "human":
        raise HTTPException(
            status_code=403,
            detail="升级操作需要用管理员账号完成，服务账号无权操作。",
        )
    if _is_admin_payload(payload):
        return str(subject)
    raise HTTPException(
        status_code=403,
        detail="该操作需要管理员权限。请换用管理员账号后再试。",
    )


# No router-level admin gate: ``/status`` is read-only and open to any logged-in
# human, while apply/rollback/check carry ``require_upgrade_admin`` themselves.
router = APIRouter(prefix="/v1/upgrade", tags=["upgrade"])


def get_upgrade_manager() -> Iterator[Any]:
    """Build a manager from the documented environment variables."""
    from .update_check import build_manager_from_env

    manager = build_manager_from_env()
    try:
        yield manager
    finally:
        manager.close()


def _http_error(status_code: int, code: str, message: str, **extra: Any) -> None:
    raise HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message, **extra},
    )


def _safe_current_version(manager: UpgradeManager) -> Optional[str]:
    try:
        return manager.current_version()[0]
    except (VersionReadError, OSError):
        return None


def _classify_error(text: str) -> str:
    value = text or ""
    if "未配置更新源" in value:
        return "source_not_configured"
    if "磁盘空间不足" in value or "不可写" in value or "备份未启用" in value:
        return "disk_space"
    if "备份不可用" in value or "哈希不符" in value or "SHA256 与记录不符" in value:
        return "backup_corrupt"
    if (
        "备份已不存在" in value
        or "需要重新下载" in value
        or "没有可回滚的升级记录" in value
        or "缺少旧版本代码包" in value
    ):
        return "backup_missing"
    if "自检未通过" in value:
        return "selfcheck_failed"
    if "预检" in value or "数据校验" in value:
        return "preflight_failed"
    if "版本" in value and ("不一致" in value or "不匹配" in value):
        return "version_mismatch"
    if "签名" in value:
        return "signature_invalid"
    if "应用失败" in value or "ApplyError" in value:
        return "apply_failed"
    if "下载中断" in value or "下载未完成" in value or "网络不可用" in value:
        return "download_failed"
    if "校验" in value or "SHA256" in value:
        return "checksum_mismatch"
    return "unknown"


_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
#: 旧状态文件可能把路径 / 英文异常名写进 ``last_error``；即便夹着中文，
#: 只要命中这两类就要换成中文主文案，原文只进技术详情。
_UNSAFE_ERROR_RE = re.compile(
    r"(?i)(?:[A-Za-z]:[\\/]|/(?:Users|home|app|var|tmp|opt|etc|root|usr|mnt|srv)/"
    r"|\b[A-Za-z_][A-Za-z0-9_]*(?:Error|Exception)\b)"
)

UPGRADE_ERROR_ROLLED_BACK = "升级失败，已回滚。请重试，或导出诊断包。"
UPGRADE_ERROR_NOT_ROLLED_BACK = "升级失败，未回滚。请重试，或导出诊断包。"
ROLLBACK_ERROR_MESSAGE = "回滚失败。请重试，或导出诊断包。"
CHECK_FAILED_MESSAGE = "检查更新没有完成，请稍后重试；如果反复出现，请导出诊断包。"


def _sanitize_check_dict(data: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not data:
        return data
    sanitized = dict(data)
    message = str(sanitized.get("message") or "")
    if message and (not _CJK_RE.search(message) or _UNSAFE_ERROR_RE.search(message)):
        sanitized["message"] = CHECK_FAILED_MESSAGE
    sanitized.pop("error", None)
    return sanitized


def _public_check_result(result: Any) -> Dict[str, Any]:
    """检查结果里给界面看的那句话也过一遍：不出现路径 / 异常类名 / 英文原文。"""
    return _sanitize_check_dict(result.to_dict()) or {}


def _friendly_phase_error(phase: str, technical_detail: str = "") -> str:
    """旧状态文件 / 无中文文案时的兜底：主文案只讲结果与下一步。"""
    if phase == "rolled_back":
        return UPGRADE_ERROR_ROLLED_BACK
    return UPGRADE_ERROR_NOT_ROLLED_BACK


def _operation_view(record: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not record:
        return None
    reason = str(record.get("reason") or "")
    return {
        "id": record.get("id"),
        "kind": record.get("kind"),
        "status": record.get("status"),
        "created_at": record.get("created_at"),
        "from_version": record.get("from_version"),
        "to_version": record.get("to_version"),
        "backup_id": record.get("backup_id"),
        "reason": reason,
        "error_category": _classify_error(reason) if reason else None,
    }


def _status_payload(manager: UpgradeManager) -> Dict[str, Any]:
    from packaging.version import InvalidVersion

    from .models import CheckStatus
    from .versioning import is_newer

    state_file = manager.status()
    last_check = manager.last_check() or {}
    last_apply = _operation_view((manager.history(kind="apply", limit=1) or [None])[0])
    last_rollback = _operation_view(
        (manager.history(kind="rollback", limit=1) or [None])[0]
    )
    operations = [item for item in (last_apply, last_rollback) if item]
    last_operation = (
        max(operations, key=lambda item: str(item.get("created_at") or ""))
        if operations
        else None
    )
    current_version = _safe_current_version(manager)
    latest_version = last_check.get("latest_version")
    update_available = str(last_check.get("status") or "") == CheckStatus.UPDATE_AVAILABLE.value
    if update_available and current_version and latest_version:
        try:
            update_available = is_newer(str(latest_version), current_version)
        except (InvalidVersion, ValueError):
            update_available = False

    phase = str(state_file.get("phase") or "idle")
    if phase in ("checking", "downloading", "verifying"):
        public_state = "checking"
    elif phase in ("backing_up", "applying", "self_checking", "rolling_back"):
        public_state = "applying"
    elif phase == "rolled_back":
        public_state = "rolled_back"
    elif phase == "failed":
        public_state = "failed"
    elif phase == "completed":
        if update_available:
            public_state = "available"
        elif last_operation and last_operation.get("kind") == "apply":
            public_state = "applied"
        else:
            public_state = "idle"
    else:
        public_state = "idle"

    message = str(state_file.get("message") or "")
    error_text = str(state_file.get("error") or "")
    technical_detail = str(state_file.get("technical_detail") or "")
    if not error_text and phase == "failed":
        error_text = message
    error: Optional[Dict[str, Any]] = None
    if error_text:
        raw_error_text = error_text
        # 旧状态文件里的 last_error 可能是英文原文 / 绝对路径 / 异常类名；
        # 只要命中这些，主文案一律换成中文结论，原文只放进 technical_detail。
        if not _CJK_RE.search(error_text) or _UNSAFE_ERROR_RE.search(error_text):
            error_text = _friendly_phase_error(phase, technical_detail)
            if not technical_detail:
                technical_detail = redact_log_text(raw_error_text)
        error = {
            "category": _classify_error(technical_detail or raw_error_text),
            "message": error_text,
        }
        if technical_detail:
            error["technical_detail"] = technical_detail

    # 状态栏的 message 也不能带路径 / 异常类名（旧状态文件残留）。
    if message and _UNSAFE_ERROR_RE.search(message):
        message = _friendly_phase_error(phase, technical_detail)

    source_configured = manager.config.source is not None
    if not source_configured:
        message = SOURCE_NOT_CONFIGURED

    # 设置页三行信息的唯一来源：备份在哪、与数据是否同盘、上次备份何时。
    # 直接读真实目录，不在这里做任何推断，避免界面显示一个"看起来安全"的假位置。
    backup = manager.backup_summary()

    return {
        "ok": True,
        "source_configured": source_configured,
        "source": getattr(manager.config.source, "description", "") or "",
        "state": public_state,
        "phase": phase,
        "message": message,
        "current_version": current_version,
        "latest_version": latest_version,
        "update_available": bool(update_available),
        "last_check": _sanitize_check_dict(last_check),
        "last_operation": last_operation,
        "last_apply": last_apply,
        "last_rollback": last_rollback,
        "backup": backup,
        "error": error,
        "updated_at": state_file.get("updated_at", ""),
    }


#: 成员只看结论。任何带绝对路径 / 盘符 / 异常类名 / 纯英文技术原文的字段
#: 都不进白名单；完整诊断（路径、盘符、迁移记录、技术详情）只给管理员。
_MEMBER_CHECK_FIELDS = (
    "status",
    "message",
    "current_version",
    "latest_version",
    "checked_at",
    "offline",
)
_MEMBER_OPERATION_FIELDS = (
    "id",
    "kind",
    "status",
    "created_at",
    "from_version",
    "to_version",
    "backup_id",
    "error_category",
)


def _member_text(value: Any, fallback: str) -> str:
    """只放行中文结论；路径/异常类名/英文原文一律换成兜底文案。"""
    text = str(value or "").strip()
    if not text:
        return fallback
    if _UNSAFE_ERROR_RE.search(text) or not _CJK_RE.search(text):
        return fallback
    return text


def _member_operation(item: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not item:
        return item
    return {key: item.get(key) for key in _MEMBER_OPERATION_FIELDS}


def _member_check(check: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not check:
        return check
    view = {key: check.get(key) for key in _MEMBER_CHECK_FIELDS if key in check}
    view["message"] = _member_text(view.get("message"), "")
    return view


def _member_backup(backup: Dict[str, Any]) -> Dict[str, Any]:
    label = str(backup.get("label") or "")
    warning = _member_text(
        backup.get("warning"), label or "备份状态不可用，请联系管理员。"
    )
    return {
        "enabled": bool(backup.get("available")),
        "available": backup.get("available"),
        "configured": backup.get("configured"),
        "cross_disk": backup.get("cross_disk"),
        "same_volume": backup.get("same_volume"),
        "label": label,
        "warning": warning,
        "backup_count": backup.get("backup_count"),
        "last_backup_at": backup.get("last_backup_at"),
        "last_backup_id": backup.get("last_backup_id"),
    }


def _member_status_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """成员可见的裁剪版：版本 / 是否有更新 / 备份是否启用 / 一句话原因。

    明确不返回 ``source``、``backup.root``、``backup.data_dir``、盘符、
    迁移记录、``technical_detail`` 与原始 ``reason``（这些是本机基础设施）。
    """
    phase = str(payload.get("phase") or "")
    error_text = (
        UPGRADE_ERROR_ROLLED_BACK
        if phase == "rolled_back"
        else UPGRADE_ERROR_NOT_ROLLED_BACK
        if phase == "failed"
        else "升级状态暂不可用，请稍后重试。"
    )
    message = _member_text(payload.get("message"), "")
    if not message and phase in ("failed", "rolled_back"):
        message = error_text
    error = payload.get("error")
    if error:
        error = {
            "category": error.get("category"),
            "message": _member_text(error.get("message"), error_text),
        }
    return {
        "ok": payload.get("ok"),
        "source_configured": payload.get("source_configured"),
        "state": payload.get("state"),
        "message": message,
        "current_version": payload.get("current_version"),
        "latest_version": payload.get("latest_version"),
        "update_available": payload.get("update_available"),
        "last_check": _member_check(payload.get("last_check")),
        "last_operation": _member_operation(payload.get("last_operation")),
        "last_apply": _member_operation(payload.get("last_apply")),
        "last_rollback": _member_operation(payload.get("last_rollback")),
        "backup": _member_backup(payload.get("backup") or {}),
        "error": error,
        "updated_at": payload.get("updated_at"),
    }


def _require_source(manager: UpgradeManager) -> None:
    if manager.config.source is None:
        _http_error(
            409,
            "source_not_configured",
            SOURCE_NOT_CONFIGURED,
            source_configured=False,
        )


def _normalized_version(value: str) -> Version:
    from packaging.version import InvalidVersion, Version

    try:
        return Version(value)
    except InvalidVersion:
        _http_error(
            400,
            "invalid_version",
            f"版本号格式无效：{value!r}",
        )


@router.get("/status", summary="当前升级状态（只读）")
def upgrade_status(
    manager=Depends(get_upgrade_manager),
    viewer: tuple[str, bool] = Depends(require_upgrade_viewer),
) -> Dict[str, Any]:
    payload = _status_payload(manager)
    _principal, is_admin = viewer
    # 成员只看结论；完整诊断（本机路径 / 盘符 / 迁移记录 / 技术详情）只给
    # 主账号或管理员。写操作另有 require_upgrade_admin。
    return payload if is_admin else _member_status_payload(payload)


@router.post("/check", summary="检查更新（只检查，不下载不安装）")
def upgrade_check(
    manager=Depends(get_upgrade_manager),
    _principal: str = Depends(require_upgrade_admin),
) -> Dict[str, Any]:
    _require_source(manager)
    result = manager.check(trigger="api")
    return {
        "ok": True,
        "mode": "synchronous",
        "source_configured": True,
        "check": _public_check_result(result),
        "status": _status_payload(manager),
    }


@router.post("/apply", summary="开始升级（同步执行，需二次确认）")
def upgrade_apply(
    body: ApplyRequest,
    manager=Depends(get_upgrade_manager),
    _principal: str = Depends(require_upgrade_admin),
) -> Dict[str, Any]:
    from .downloader import cleanup_download
    from .models import CheckStatus

    if not body.confirm:
        _http_error(
            400,
            "confirmation_required",
            "开始升级需要二次确认：请传 confirm=true，并明确目标版本 version。",
        )
    if not body.version:
        _http_error(
            400,
            "version_required",
            "开始升级必须明确指定目标版本号 version。",
        )
    requested = _normalized_version(body.version)
    _require_source(manager)

    check = manager.check(trigger="api")
    if check.latest_version:
        latest = _normalized_version(check.latest_version)
        if requested != latest:
            _http_error(
                400,
                "version_mismatch",
                f"请求升级到 {body.version}，但当前可用版本是 {check.latest_version}；"
                "为防止误装，已拒绝。",
                current_version=check.current_version,
                latest_version=check.latest_version,
            )
    if check.status is not CheckStatus.UPDATE_AVAILABLE or check.release is None:
        # A failed check is not "no update available": telling the user the
        # latter would hide an unreachable source behind a false "已是最新".
        code = {
            CheckStatus.UNREACHABLE: "source_unreachable",
            CheckStatus.INVALID_MANIFEST: "invalid_manifest",
            CheckStatus.VERSION_UNKNOWN: "version_unknown",
        }.get(check.status, "no_update_available")
        _http_error(
            409,
            code,
            check.message or "当前没有可用更新，无法升级。",
            current_version=check.current_version,
            latest_version=check.latest_version,
        )

    download = None
    try:
        download = manager.download(check.release)
        result = manager.apply(download.path, check.release)
    except UpgradeError as exc:
        payload = _status_payload(manager)
        friendly = (payload.get("error") or {}).get("message") or UPGRADE_ERROR_NOT_ROLLED_BACK
        _http_error(
            409,
            _classify_error(str(exc)),
            friendly,
            status=payload,
            technical_detail=redact_log_text(str(exc)),
        )
    finally:
        if download is not None:
            cleanup_download(download)

    return {
        "ok": True,
        "mode": "synchronous",
        "warning": SYNC_WARNING,
        "apply": result.to_dict(),
        "status": _status_payload(manager),
    }


@router.post("/rollback", summary="回滚到最近一次备份（同步执行，需二次确认）")
def upgrade_rollback(
    body: RollbackRequest,
    manager=Depends(get_upgrade_manager),
    _principal: str = Depends(require_upgrade_admin),
) -> Dict[str, Any]:
    if not body.confirm:
        _http_error(
            400,
            "confirmation_required",
            "回滚需要二次确认：请传 confirm=true。",
        )
    notice = (
        None
        if manager.config.source is not None
        else "未配置更新源（回滚使用本地备份，不需要联网）"
    )
    try:
        result = manager.rollback(backup_id=body.backup_id)
    except RollbackError as exc:
        payload = _status_payload(manager)
        friendly = (payload.get("error") or {}).get("message") or ROLLBACK_ERROR_MESSAGE
        _http_error(
            409,
            _classify_error(str(exc)),
            friendly,
            status=payload,
            technical_detail=redact_log_text(str(exc)),
        )
    return {
        "ok": True,
        "mode": "synchronous",
        "warning": SYNC_WARNING,
        "notice": notice,
        "source_configured": manager.config.source is not None,
        "rollback": result.to_dict(),
        "status": _status_payload(manager),
    }


__all__ = [
    "SOURCE_NOT_CONFIGURED",
    "get_upgrade_manager",
    "require_upgrade_admin",
    "router",
]
