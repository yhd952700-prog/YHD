"""技能中心的产品面接口 —— ``/v1/skills``。

为什么是**新接口**而不是复用 ``/v1/plugins`` / ``/v1/employees/capabilities``
--------------------------------------------------------------------------
* ``/v1/plugins`` 的实体是内核插件：激活 = importlib 导入一个真实 Python 模块
  （``src/kernels/plugin/load_plugin``），登记项由代码写入，没有权限/风险/使用
  员工字段，也没有创建/导入/编辑/删除。把"创建技能"映射成它，只能靠伪造成功。
* ``/v1/employees/capabilities`` 是**执行器工具目录**（id/name/tool_id/risk），
  只读、无技能包、无来源、无员工关联 —— 它是"工具"，不是"技能"。
* 仓库内不存在任何技能注册表或技能包格式（``organization`` 里的 ``skills``
  只是成员标签）。因此技能是新概念，需要新接口。

但下面三件事完全复用既有约定，不另起一套：
1. 落盘：``skill_store.SkillStore`` 走 ``resolve_store_path``，写工作区数据目录。
2. 鉴权：读 = ``require_human_principal``，写 = ``require_admin_principal``，
   与 ``src/gateway/plugins.py`` 完全一致。
3. 风险/权限/占用：由**现有数据**推导，不采信前端与导入清单的自报 ——
   风险与所需权限来自已 ACTIVE 执行器工具的 ``risk`` / ``permission``；
   "被哪些员工绑定"来自真实绑定表 ``liuhao_skill_bindings.json``（读不到就
   返回 ``null``，前端据此显示"未提供"，绝不假装"没人用"）。旧的"能力交集"
   只保留为 ``related_employees``，并明确标注是推导。

挂载位置（本模块**不**自己改 ``main.py``，那一行留给统一集成）：

    from .skills import router as skills_router
    app.include_router(skills_router)
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    Response,
    UploadFile,
)
from pydantic import BaseModel

from .policy import require_admin_principal, require_human_principal
from .skill_bindings import (
    SkillBindingConflict,
    SkillBindingError,
    SkillBindingStore,
)
from .skill_import import McpConfigDetected, SkillImportError, ImportedSkill, parse_upload
from .skill_install import (
    MAX_TOTAL_BYTES,
    SkillInstallError,
    export_zip,
    installed_dir,
    stage_path,
    stage_upload,
    uninstall,
)
from .skill_runtime import (
    SkillApprovalStore,
    SkillRunRefused,
    SkillRuntime,
    SkillRuntimeError,
    read_audit,
    read_run,
    resolve_interpreter,
    sha256_file,
)
from .skill_store import (
    SkillConflict,
    SkillNotFound,
    SkillStore,
    SkillStoreError,
    normalize_capabilities,
)
from src.kernels._crosscutting import bind_request_principal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/skills", tags=["skills"])

#: 导入文件的体积上限（256 KiB）。技能包是文本，超出多半是误传了大文件。
_IMPORT_LIMIT_BYTES = 256 * 1024

#: 风险等级排序。``unknown`` 最高：无法核对时按最保守处理。
_RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "unknown": 3}
_RISK_LABEL = {
    "low": "低",
    "medium": "中",
    "high": "高",
    "unknown": "未知",
}

#: 启用前必须显式确认的风险档位。
_CONFIRM_REQUIRED = ("high", "unknown")


# ---------------------------------------------------------------------- #
# 真实能力目录（风险 / 所需权限的唯一来源）
# ---------------------------------------------------------------------- #
def _load_capability_index() -> Tuple[Dict[str, Dict[str, Any]], Optional[str]]:
    """读已 ACTIVE 的执行器工具，按能力聚合出 ``risk`` 与 ``permission``。

    返回 ``(index, error)``：``error`` 非空表示目录不可用（不是"没有能力"），
    调用方必须据此把风险降级为 ``unknown`` 并如实告知，不能当作"低风险"。
    """
    index: Dict[str, Dict[str, Any]] = {}
    try:
        from src.ai.lcore import LCore
        from src.ai.tool_registry import ToolStatus

        lcore = LCore(scope="L1", register_local_tools=True)
        for tool in lcore.tools._tools.values():
            if lcore.tools._status.get(tool.tool_id) != ToolStatus.ACTIVE:
                continue
            capability = str(getattr(tool, "capability", "") or "")
            if not capability:
                continue
            entry = index.setdefault(
                capability, {"risk": "low", "permissions": set(), "tools": []}
            )
            entry["risk"] = _max_risk(entry["risk"], str(tool.risk or "low"))
            permission = str(getattr(tool, "permission", "") or "").strip()
            if permission:
                entry["permissions"].add(permission)
            entry["tools"].append(tool.tool_id)
    except Exception as exc:  # noqa: BLE001 - 目录不可用时不伪造能力
        logger.warning("skills: capability catalog unavailable: %s", exc)
        return {}, f"{type(exc).__name__}: {exc}"
    return index, None


def _max_risk(left: str, right: str) -> str:
    a = str(left or "").strip().lower()
    b = str(right or "").strip().lower()
    a = a if a in _RISK_ORDER else "unknown"
    b = b if b in _RISK_ORDER else "unknown"
    return a if _RISK_ORDER[a] >= _RISK_ORDER[b] else b


def _load_assignments(strict: bool) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
    """员工 → 默认能力。``None`` 表示读不出来（与"确实没人用"严格区分）。"""
    try:
        from src.ai.employee_store import EmployeeStore

        return EmployeeStore().capability_assignments(strict=strict), None
    except Exception as exc:  # noqa: BLE001 - 读不到就返回 None，由调用方如实说明
        logger.warning("skills: employee assignments unavailable: %s", exc)
        return None, f"{type(exc).__name__}: {exc}"


def _binding_store() -> SkillBindingStore:
    return SkillBindingStore()


def _load_bindings() -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
    """读真实绑定表。``None`` 表示读不出来，不能当成"没人绑定"。"""
    try:
        return _binding_store().list_all(), None
    except SkillBindingError as exc:
        logger.warning("skills: bindings unavailable: %s", exc)
        return None, str(exc)


def _evaluate_current(record: Dict[str, Any]) -> Dict[str, Any]:
    """按当前能力目录 / 员工默认能力 / 绑定表重新评估一条技能。"""
    capability_index, capability_error = _load_capability_index()
    assignments, _ = _load_assignments(strict=False)
    bindings, bindings_error = _load_bindings()
    return _evaluate(
        record,
        capability_index,
        capability_error,
        assignments,
        bindings=bindings,
        bindings_error=bindings_error,
    )


# ---------------------------------------------------------------------- #
# 序列化：风险 / 权限 / 占用一律在读取时现算
# ---------------------------------------------------------------------- #
def _runtime_view(record: Dict[str, Any]) -> Dict[str, Any]:
    """运行时真实状态：有没有包、声明了哪些入口、哪些已授权。

    读不出来时 ``state="unavailable"`` 且 ``allowed=False``，不假装可运行。
    """
    try:
        runtime = SkillRuntime()
        status = runtime.execution_status(record)
        package = runtime.package(str(record.get("id") or "")) or {}
    except Exception as exc:  # noqa: BLE001 - 运行时坏了要如实告知
        logger.warning("skills: runtime unavailable for %s: %s", record.get("id"), exc)
        return {
            "declared": False,
            "installed": False,
            "state": "unavailable",
            "allowed": False,
            "approved_entrypoints": [],
            "entrypoints": [],
            "limits": {},
            "package_hash": "",
            "detail": f"技能运行时不可用：{type(exc).__name__}: {exc}",
        }
    declared = (package.get("runtime") or {}).get("entrypoints") or []
    approved = set(status.get("approved_entrypoints") or [])
    entrypoints = [
        {
            "id": entry.get("id"),
            "label": entry.get("label") or entry.get("id"),
            "interpreter": entry.get("interpreter"),
            "script": entry.get("script"),
            "network": bool(entry.get("network")),
            "approved": str(entry.get("id") or "") in approved,
        }
        for entry in declared
    ]
    return {
        "declared": bool(entrypoints),
        "installed": bool(installed_dir(str(record.get("id") or ""))),
        "state": status.get("state"),
        "allowed": bool(status.get("allowed")),
        "approved_entrypoints": sorted(approved),
        "entrypoints": entrypoints,
        "limits": dict((package.get("runtime") or {}).get("limits") or {}),
        "package_hash": package.get("package_hash") or "",
        "installed_at": package.get("installed_at") or "",
        "detail": status.get("detail") or "",
    }


def _evaluate(
    record: Dict[str, Any],
    capability_index: Dict[str, Dict[str, Any]],
    capability_error: Optional[str],
    assignments: Optional[List[Dict[str, Any]]],
    bindings: Optional[List[Dict[str, Any]]] = None,
    bindings_error: Optional[str] = None,
) -> Dict[str, Any]:
    capabilities = [str(c) for c in (record.get("capabilities") or [])]
    missing: List[str] = []
    derived_permissions: List[str] = []
    risk = "low"
    for capability in capabilities:
        entry = capability_index.get(capability)
        if entry is None:
            missing.append(capability)
            continue
        risk = _max_risk(risk, str(entry.get("risk") or "low"))
        derived_permissions.extend(sorted(entry.get("permissions") or ()))
    if missing:
        # 声明了目录里不存在的能力：无法核对它能做什么，按最保守的档位处理。
        risk = "unknown"
    elif capabilities and capability_error:
        risk = "unknown"
    declared_risk = str(record.get("declared_risk") or "").strip().lower()
    if declared_risk:
        risk = _max_risk(risk, declared_risk)

    permissions = sorted(
        set(derived_permissions) | set(record.get("declared_permissions") or ())
    )

    skill_id = str(record.get("id") or "")
    if bindings is None:
        used_by: Optional[List[str]] = None
    else:
        used_by = sorted(
            {
                str(row.get("employee") or "").strip()
                for row in bindings
                if str(row.get("skill_id") or "") == skill_id
                and str(row.get("employee") or "").strip()
            }
        )

    # 能力交集只是"可能相关"，不是绑定关系。曾经它被当成 used_by 使用，
    # 会把"员工有 file_write 能力"误读成"他在用这个技能"。
    if assignments is None:
        related: Optional[List[str]] = None
    else:
        caps = set(capabilities)
        related = sorted(
            {
                str(row.get("name"))
                for row in assignments
                if caps & set(row.get("default_capabilities") or ())
            }
        )

    runtime_view = _runtime_view(record)

    return {
        "id": record.get("id"),
        "name": record.get("name"),
        "description": record.get("description") or "",
        "source": record.get("source") or "user",
        "version": record.get("version") or "",
        "risk": risk,
        "risk_note": (
            "所需能力不在本机已注册的执行器目录中，风险无法核对"
            if missing
            else ("能力目录暂时不可用，风险无法核对" if (capabilities and capability_error) else "")
        ),
        "permissions": permissions,
        "capabilities": capabilities,
        "missing_capabilities": missing,
        "enabled": bool(record.get("enabled")),
        # 真实绑定（员工 ↔ 技能表）；null 表示绑定表读不出来。
        "used_by": used_by,
        "bindings_error": bindings_error,
        # 推导结果：仅表示"能力上有交集，可能相关"，不是绑定。
        "related_employees": related,
        "related_employees_derived": True,
        "related_employees_note": "由员工默认能力与技能声明能力求交集推导，仅表示可能相关；实际绑定见 used_by。",
        "origin": record.get("origin"),
        "format": record.get("format") or "manual",
        "license": record.get("license") or "",
        "allowed_tools": list(record.get("allowed_tools") or ()),
        "metadata": dict(record.get("metadata") or {}),
        "files": list(record.get("files") or ()),
        "name_source": record.get("name_source") or "",
        "has_scripts": bool(record.get("has_scripts")),
        "script_refs": list(record.get("script_refs") or ()),
        # 只有"已安装 + 已启用 + 该入口已被 Boss 授权"才为 true。
        "execution_allowed": runtime_view["allowed"],
        "runtime": runtime_view,
        "import_report": list(record.get("import_report") or ()),
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
    }


def _store() -> SkillStore:
    return SkillStore()


def _read_or_503(action: str) -> List[Dict[str, Any]]:
    try:
        return _store().list_records()
    except SkillStoreError as exc:
        logger.error("skills: %s failed: %s", action, exc)
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _get_or_404(skill_id: str) -> Dict[str, Any]:
    try:
        return _store().get(skill_id)
    except SkillNotFound:
        raise HTTPException(
            status_code=404, detail=f"没有找到技能「{skill_id}」。请刷新列表后重试。"
        )
    except SkillStoreError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


# ---------------------------------------------------------------------- #
# 请求体
# ---------------------------------------------------------------------- #
class SkillCreateRequest(BaseModel):
    name: str
    description: str = ""


class SkillUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


class SkillEnableRequest(BaseModel):
    #: 高危/风险未知时必须显式确认，并复述当前风险档位（见 ``enable_skill``）。
    confirm: bool = False
    acknowledge_risk: Optional[str] = None


class SkillBindRequest(BaseModel):
    #: 绑定目标员工（真实员工名，必须存在于员工库）。
    employee: str


class SkillLocalInstallRequest(BaseModel):
    """从本机路径安装技能包（目录 / .zip / .md / .json）。"""

    path: str


class SkillRunRequest(BaseModel):
    """运行一个已声明的脚本入口。

    ``params`` 会以 JSON 写入运行目录的 ``input.json`` 并通过 stdin 交给脚本；
    只接受 JSON 值，不接受命令行拼接。
    """

    entrypoint: str
    params: Dict[str, Any] = {}
    employee: Optional[str] = None


class SkillApprovalRequest(BaseModel):
    """处理首次运行申请：``once`` 只放行本次，``always`` 记住（默认 90 天）。"""

    mode: str = "once"


# ---------------------------------------------------------------------- #
# 路由
# ---------------------------------------------------------------------- #
@router.get("")
def list_skills(
    principal: str = Depends(require_human_principal),
) -> Dict[str, Any]:
    """技能清单：风险、所需权限、使用员工都在这里现算，不读缓存、不读死值。"""
    records = _read_or_503("list")
    capability_index, capability_error = _load_capability_index()
    assignments, assignment_error = _load_assignments(strict=False)
    bindings, bindings_error = _load_bindings()
    items = [
        _evaluate(
            record,
            capability_index,
            capability_error,
            assignments,
            bindings=bindings,
            bindings_error=bindings_error,
        )
        for record in records
    ]
    return {
        "total": len(items),
        "enabled": sum(1 for item in items if item["enabled"]),
        "skills": items,
        "capability_catalog_error": capability_error,
        "assignments_error": assignment_error,
        "bindings_error": bindings_error,
    }


@router.post("", status_code=201)
def create_skill(
    payload: SkillCreateRequest,
    principal: str = Depends(require_admin_principal),
) -> Dict[str, Any]:
    """登记一个新技能（名称 + 说明）。

    新建技能不声明任何执行器能力，因此不会凭空获得执行权限；它的风险由服务端
    在读取时按"没有能力 → 低风险"判定，绝不从前端取值。
    """
    store = _store()
    try:
        with bind_request_principal(principal, kind="human", source="http-request"):
            record = store.create(
                name=payload.name,
                description=payload.description,
                source="user",
            )
    except SkillConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SkillStoreError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    logger.info("skills: created %s by %s", record.get("id"), principal)
    return _evaluate_current(record)


@router.post("/import", status_code=201)
async def import_skill(
    file: UploadFile = File(...),
    principal: str = Depends(require_admin_principal),
) -> Dict[str, Any]:
    """从技能包安装：``.zip`` / ``.md`` / ``.json``。

    zip 走 ``skill_install`` 的安全解包：路径穿越、符号链接、盘符、``..``、
    可执行文件、条目数/单文件/总量超限一律**拒绝整包**；先解到临时目录校验，
    通过后才整体移入 ``<data>/skills/<skill_id>``，失败回滚到安装前状态。

    导入的技能一律落为「未启用」，且脚本**不会**因为安装了就能跑：第一次运行
    还需要 Boss 单独授权。
    """
    filename = (file.filename or "").strip()
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if suffix not in ("zip", "md", "json"):
        raise HTTPException(
            status_code=415,
            detail=(
                "技能安装接受 .zip（技能包）、.md（Agent Skills）或 .json（技能清单）"
                f"（收到「{filename or '未命名文件'}」）。"
            ),
        )

    limit = MAX_TOTAL_BYTES + 4 * 1024 * 1024 if suffix == "zip" else _IMPORT_LIMIT_BYTES
    raw = await file.read(limit + 1)
    if len(raw) > limit:
        raise HTTPException(
            status_code=413,
            detail=(
                f"「{filename}」超过 {limit // (1024 * 1024) if limit > _IMPORT_LIMIT_BYTES else limit // 1024}"
                f" {'MB' if limit > _IMPORT_LIMIT_BYTES else 'KiB'} 上限，已拒绝安装。"
            ),
        )
    if not raw:
        raise HTTPException(status_code=400, detail="技能文件是空的。")

    try:
        with stage_upload(filename, raw) as staged:
            text = staged.read_text()
            if suffix != "zip" and not text.strip():
                raise HTTPException(status_code=400, detail="技能文件是空的。")
            try:
                spec = parse_upload(staged.manifest_name, text)
            except McpConfigDetected as exc:
                raise HTTPException(status_code=415, detail=str(exc)) from exc
            except SkillImportError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            materialize = suffix == "zip" or bool((spec.runtime or {}).get("entrypoints"))
            return _install_parsed(
                spec,
                staged if materialize else None,
                principal=principal,
                origin=filename,
                overwrite=False,
            )
    except SkillInstallError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _install_parsed(
    spec: ImportedSkill,
    staged: Any,
    *,
    principal: str,
    origin: str,
    overwrite: bool,
) -> Dict[str, Any]:
    """把解析结果落成技能记录（必要时连技能包一起落盘）。"""
    try:
        capabilities = normalize_capabilities(spec.capabilities)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    capability_index, _ = _load_capability_index()
    missing = [cap for cap in capabilities if cap not in capability_index]
    risk = "low" if not missing else "unknown"
    if not missing:
        for capability in capabilities:
            risk = _max_risk(risk, str(capability_index[capability]["risk"]))

    files = list(staged.files) if staged is not None else list(spec.files)
    store = _store()
    record: Optional[Dict[str, Any]] = None
    try:
        with bind_request_principal(principal, kind="human", source="http-request"):
            record = store.create(
                name=spec.name,
                description=spec.description,
                source="imported",
                version=spec.version,
                capabilities=capabilities,
                declared_permissions=spec.permissions,
                declared_risk=spec.declared_risk,
                content=spec.content,
                origin=origin,
                enabled=False,
                fmt=spec.format,
                license=spec.license,
                allowed_tools=spec.allowed_tools,
                metadata=spec.metadata,
                raw_frontmatter=spec.raw_frontmatter,
                has_scripts=spec.has_scripts,
                script_refs=spec.script_refs,
                files=files,
                name_source=spec.name_source,
                import_report=spec.import_report,
                execution_allowed=spec.execution_allowed,
            )
        skill_id = str(record.get("id") or "")
        if staged is not None:
            entrypoints = list((spec.runtime or {}).get("entrypoints") or [])
            staged.finalize(entrypoints)
            install_dir = staged.commit(skill_id, overwrite=overwrite)
            runtime = {
                "limits": dict((spec.runtime or {}).get("limits") or {}),
                "entrypoints": entrypoints,
            }
            SkillRuntime().save_package(
                skill_id,
                {
                    "skill_id": skill_id,
                    "install_dir": install_dir,
                    "origin": origin,
                    "format": spec.format,
                    "package_hash": staged.package_hash,
                    "files": staged.hashes,
                    "runtime": runtime,
                    "installed_at": record.get("updated_at") or record.get("created_at"),
                },
            )
    except SkillConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SkillInstallError as exc:
        if record is not None:
            _rollback_record(store, str(record.get("id") or ""))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        if record is not None:
            _rollback_record(store, str(record.get("id") or ""))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SkillStoreError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    assignments, _ = _load_assignments(strict=False)
    bindings, bindings_error = _load_bindings()
    logger.info(
        "skills: installed %s from %s by %s (format=%s, risk=%s, runtime=%s)",
        record.get("id"),
        origin,
        principal,
        spec.format,
        risk,
        bool((spec.runtime or {}).get("entrypoints")),
    )
    payload = _evaluate(
        record,
        capability_index,
        None,
        assignments,
        bindings=bindings,
        bindings_error=bindings_error,
    )
    if staged is not None and payload["runtime"]["declared"]:
        payload["import_note"] = (
            "安装完成，技能仍为「未启用」。启用后第一次运行脚本时，"
            "还需要你单独点一次确认；确认后可按需记住该入口。"
        )
    elif spec.has_scripts:
        payload["import_note"] = (
            "安装完成，但清单里没有 runtime.entrypoints，脚本不会被运行；"
            "正文仍会作为资料注入已绑定的员工。"
        )
    else:
        payload["import_note"] = "安装完成，技能仍为「未启用」：请检查导入报告后手动启用。"
    return payload


def _rollback_record(store: SkillStore, skill_id: str) -> None:
    """安装包失败时把刚建的技能记录删掉，避免留下半装好的技能。"""
    if not skill_id:
        return
    try:
        store.delete(skill_id)
    except Exception:  # noqa: BLE001 - 回滚尽力而为，但要留日志
        logger.exception("skills: rollback record %s failed", skill_id)
    try:
        SkillRuntime().remove_package(skill_id)
    except Exception:  # noqa: BLE001
        logger.exception("skills: rollback package %s failed", skill_id)


@router.post("/install/local", status_code=201)
def install_local(
    payload: "SkillLocalInstallRequest",
    principal: str = Depends(require_admin_principal),
) -> Dict[str, Any]:
    """从本机路径安装（目录 / .zip / .md / .json）。

    Windows 没法用浏览器上传一个目录，所以目录安装走这个接口；路径必须由
    Boss 自己在界面上填，服务端不做任何路径猜测。
    """
    try:
        with stage_path(payload.path) as staged:
            text = staged.read_text()
            try:
                spec = parse_upload(os.path.basename(staged.main_file), text)
            except McpConfigDetected as exc:
                raise HTTPException(status_code=415, detail=str(exc)) from exc
            except SkillImportError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return _install_parsed(
                spec,
                staged,
                principal=principal,
                origin=payload.path,
                overwrite=False,
            )
    except SkillInstallError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/{skill_id}")
def update_skill(
    skill_id: str,
    payload: SkillUpdateRequest,
    principal: str = Depends(require_admin_principal),
) -> Dict[str, Any]:
    """改名称 / 说明。能力、风险、来源与占用关系不可通过本接口篡改。"""
    if payload.name is None and payload.description is None:
        raise HTTPException(status_code=400, detail="没有需要修改的字段。")
    store = _store()
    try:
        with bind_request_principal(principal, kind="human", source="http-request"):
            record = store.update(
                skill_id, name=payload.name, description=payload.description
            )
    except SkillNotFound:
        raise HTTPException(status_code=404, detail=f"没有找到技能「{skill_id}」。")
    except SkillConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SkillStoreError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return _evaluate_current(record)


@router.delete("/{skill_id}")
def delete_skill(
    skill_id: str,
    principal: str = Depends(require_admin_principal),
) -> Dict[str, Any]:
    """删除技能。**有员工真实绑定时返回 409 并点名**，不静默级联删除。"""
    record = _get_or_404(skill_id)
    bindings, bindings_error = _load_bindings()
    if bindings is None:
        # 读不出绑定关系时宁可不删：静默删除可能把员工正在用的技能抽走。
        raise HTTPException(
            status_code=503,
            detail=(
                "读取员工绑定情况失败，为避免误删正在被员工使用的技能，"
                f"本次删除没有执行（{bindings_error}）。"
            ),
        )
    in_use = sorted(
        {
            str(row.get("employee") or "").strip()
            for row in bindings
            if str(row.get("skill_id") or "") == skill_id
            and str(row.get("employee") or "").strip()
        }
    )
    if in_use:
        raise HTTPException(
            status_code=409,
            detail=(
                f"技能「{record.get('name')}」仍绑定 {len(in_use)} 位员工："
                f"{'、'.join(in_use)}。请先在技能中心解除绑定，"
                "或先停用该技能，再删除。"
            ),
        )
    store = _store()
    try:
        with bind_request_principal(principal, kind="human", source="http-request"):
            removed = store.delete(skill_id)
    except SkillNotFound:
        raise HTTPException(status_code=404, detail=f"没有找到技能「{skill_id}」。")
    except SkillStoreError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    try:
        _binding_store().remove_skill(skill_id)
    except SkillBindingError as exc:
        # 技能已删、绑定残留：必须如实说，否则下次列表会指向不存在的技能。
        logger.error("skills: binding cleanup failed for %s: %s", skill_id, exc)
    # 清掉已安装的技能包与登记；删失败只记日志，不让用户以为记录还在。
    if not uninstall(skill_id):
        logger.warning("skills: package dir for %s not removed (absent or locked)", skill_id)
    try:
        SkillRuntime().remove_package(skill_id)
        SkillApprovalStore().revoke_all(skill_id=skill_id, revoked_by=principal)
    except Exception:  # noqa: BLE001 - 清理尽力而为
        logger.exception("skills: runtime cleanup failed for %s", skill_id)
    logger.info("skills: deleted %s by %s", skill_id, principal)
    return {"deleted": True, "skill_id": removed.get("id"), "name": removed.get("name")}


@router.get("/bindings")
def list_bindings(
    employee: Optional[str] = None,
    principal: str = Depends(require_human_principal),
) -> Dict[str, Any]:
    """全部绑定（可按员工过滤）。读不出来时 503，不返回空表假装没绑定。"""
    bindings, error = _load_bindings()
    if bindings is None:
        raise HTTPException(status_code=503, detail=error or "技能绑定表读取失败。")
    target = str(employee or "").strip().casefold()
    rows = bindings
    if target:
        rows = [
            row
            for row in bindings
            if str(row.get("employee") or "").strip().casefold() == target
        ]
    return {"total": len(rows), "bindings": rows}


@router.get("/{skill_id}/bindings")
def list_skill_bindings(
    skill_id: str,
    principal: str = Depends(require_human_principal),
) -> Dict[str, Any]:
    _get_or_404(skill_id)
    bindings, error = _load_bindings()
    if bindings is None:
        raise HTTPException(status_code=503, detail=error or "技能绑定表读取失败。")
    rows = [
        row for row in bindings if str(row.get("skill_id") or "") == skill_id
    ]
    return {"skill_id": skill_id, "total": len(rows), "bindings": rows}


@router.post("/{skill_id}/bind", status_code=201)
def bind_skill(
    skill_id: str,
    payload: SkillBindRequest,
    principal: str = Depends(require_admin_principal),
) -> Dict[str, Any]:
    """把技能绑定给一位真实员工（幂等）。"""
    record = _get_or_404(skill_id)
    employee = str(payload.employee or "").strip()
    if not employee:
        raise HTTPException(status_code=400, detail="employee 不能为空。")
    try:
        from src.ai.employee_store import EmployeeStore

        known = set(EmployeeStore().list_employees())
    except Exception as exc:  # noqa: BLE001 - 读不到员工库时不能假装员工存在
        raise HTTPException(
            status_code=503,
            detail=f"员工库暂时不可用，无法核对绑定对象（{type(exc).__name__}: {exc}）。",
        ) from exc
    if employee not in known:
        raise HTTPException(
            status_code=404,
            detail=f"本机没有名为「{employee}」的员工，未建立绑定。",
        )
    try:
        binding = _binding_store().bind(
            skill_id=skill_id, employee=employee, actor=principal
        )
    except SkillBindingConflict as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SkillBindingError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    logger.info(
        "skills: bound %s -> %s by %s", skill_id, employee, principal
    )
    capability_index, capability_error = _load_capability_index()
    assignments, _ = _load_assignments(strict=False)
    bindings, bindings_error = _load_bindings()
    return {
        "binding": binding,
        "skill": _evaluate(
            record,
            capability_index,
            capability_error,
            assignments,
            bindings=bindings,
            bindings_error=bindings_error,
        ),
    }


@router.delete("/{skill_id}/bind/{employee}")
def unbind_skill(
    skill_id: str,
    employee: str,
    principal: str = Depends(require_admin_principal),
) -> Dict[str, Any]:
    _get_or_404(skill_id)
    try:
        removed = _binding_store().unbind(skill_id=skill_id, employee=employee)
    except SkillBindingError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if removed is None:
        raise HTTPException(
            status_code=404,
            detail=f"技能「{skill_id}」没有绑定员工「{employee}」。",
        )
    logger.info("skills: unbound %s -/-> %s by %s", skill_id, employee, principal)
    return {"unbound": True, "binding": removed}


@router.post("/{skill_id}/test")
def test_skill(
    skill_id: str,
    principal: str = Depends(require_human_principal),
) -> Dict[str, Any]:
    """核对技能声明的能力在本机是否真的注册且可用。

    这是**真实校验**（读执行器目录 + 状态），不是跑一段演示脚本：没声明能力的
    技能返回 ``passed=null`` 并说明"没有可验证的执行面"，绝不返回"通过"。
    """
    record = _get_or_404(skill_id)
    view = _runtime_view(record)
    if view["declared"]:
        return _test_runtime(skill_id, view)
    capabilities = [str(c) for c in (record.get("capabilities") or [])]
    capability_index, capability_error = _load_capability_index()
    if capability_error:
        return {
            "skill_id": skill_id,
            "passed": None,
            "detail": f"本机能力目录读取失败，无法核对所需能力（{capability_error}）。",
            "checked_capabilities": [],
            "missing_capabilities": [],
        }
    if not capabilities:
        return {
            "skill_id": skill_id,
            "passed": None,
            "detail": (
                "该技能没有声明任何执行器能力，本机没有可验证的执行面；"
                "它目前只是一条名称 + 说明的登记记录。"
            ),
            "checked_capabilities": [],
            "missing_capabilities": [],
        }
    missing = [cap for cap in capabilities if cap not in capability_index]
    if missing:
        return {
            "skill_id": skill_id,
            "passed": False,
            "detail": (
                "本机没有注册这些能力：" + "、".join(missing) + "，技能无法按要求执行。"
            ),
            "checked_capabilities": capabilities,
            "missing_capabilities": missing,
        }
    risks = "、".join(
        f"{cap}({_RISK_LABEL.get(str(capability_index[cap]['risk']), '未知')})"
        for cap in capabilities
    )
    return {
        "skill_id": skill_id,
        "passed": True,
        "detail": f"{len(capabilities)} 项所需能力均已注册且处于可用状态：{risks}。",
        "checked_capabilities": capabilities,
        "missing_capabilities": [],
    }


@router.post("/{skill_id}/enable")
def enable_skill(
    skill_id: str,
    payload: Optional[SkillEnableRequest] = None,
    principal: str = Depends(require_admin_principal),
) -> Dict[str, Any]:
    """启用技能。

    高危与风险未知的技能必须显式确认：``confirm=true`` 且 ``acknowledge_risk``
    复述当前风险档位（``high`` / ``unknown``）。**刻意没有批量启用接口** ——
    不允许一键把所有技能打开。
    """
    record = _get_or_404(skill_id)
    evaluated = _evaluate_current(record)
    risk = str(evaluated["risk"])
    request = payload or SkillEnableRequest()
    if risk in _CONFIRM_REQUIRED:
        acknowledged = str(request.acknowledge_risk or "").strip().lower()
        if not request.confirm or acknowledged != risk:
            required_body = json.dumps(
                {"confirm": True, "acknowledge_risk": risk}, ensure_ascii=False
            )
            raise HTTPException(
                status_code=409,
                detail=(
                    f"技能「{record.get('name')}」的风险为「{_RISK_LABEL[risk]}」，"
                    f"启用前必须明确确认：请提交 {required_body}。本版不提供一键全开。"
                ),
            )
    store = _store()
    try:
        with bind_request_principal(principal, kind="human", source="http-request"):
            updated = store.set_enabled(skill_id, True)
    except SkillNotFound:
        raise HTTPException(status_code=404, detail=f"没有找到技能「{skill_id}」。")
    except SkillStoreError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    logger.info("skills: enabled %s (risk=%s) by %s", skill_id, risk, principal)
    return _evaluate_current(updated)


@router.post("/{skill_id}/disable")
def disable_skill(
    skill_id: str,
    principal: str = Depends(require_admin_principal),
) -> Dict[str, Any]:
    """停用技能。停用永远不需要确认：收紧权限不该有摩擦。"""
    _get_or_404(skill_id)
    store = _store()
    try:
        with bind_request_principal(principal, kind="human", source="http-request"):
            updated = store.set_enabled(skill_id, False)
    except SkillNotFound:
        raise HTTPException(status_code=404, detail=f"没有找到技能「{skill_id}」。")
    except SkillStoreError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    logger.info("skills: disabled %s by %s", skill_id, principal)
    return _evaluate_current(updated)


# ---------------------------------------------------------------------- #
# 脚本运行时：检查 / 运行 / 授权 / 审计 / 导出
# ---------------------------------------------------------------------- #
def _test_runtime(skill_id: str, view: Dict[str, Any]) -> Dict[str, Any]:
    """核对已安装脚本：解释器在不在、文件在不在、哈希和安装时是否一致。

    只做静态核对，**不执行脚本**；真正的执行必须走 ``/{id}/run`` 并由 Boss 授权。
    """
    root = installed_dir(skill_id)
    package = SkillRuntime().package(skill_id) or {}
    files = dict(package.get("files") or {})
    if not root:
        return {
            "skill_id": skill_id,
            "passed": False,
            "detail": "技能声明了脚本入口，但本机没有已安装的包目录，请重新安装。",
            "entrypoints": [],
        }
    checks: List[Dict[str, Any]] = []
    passed = True
    for entry in view["entrypoints"]:
        interpreter = str(entry.get("interpreter") or "")
        rel = str(entry.get("script") or "")
        exe = resolve_interpreter(interpreter)
        path = os.path.join(root, *rel.split("/"))
        exists = os.path.isfile(path)
        digest = sha256_file(path) if exists else ""
        intact = bool(digest) and digest == str(files.get(rel) or "")
        ok = bool(exe) and exists and intact
        passed = passed and ok
        if not exe:
            reason = f"本机没有可用的 {interpreter} 解释器"
        elif not exists:
            reason = "脚本文件不存在"
        elif not intact:
            reason = "脚本与安装时记录的哈希不一致（可能被改动）"
        else:
            reason = "可以运行；首次运行仍需授权"
        checks.append(
            {
                "id": entry.get("id"),
                "interpreter": interpreter,
                "interpreter_path": exe or "",
                "script": rel,
                "exists": exists,
                "script_sha256": digest,
                "hash_matches": intact,
                "passed": ok,
                "detail": reason,
            }
        )
    return {
        "skill_id": skill_id,
        "passed": passed,
        "detail": (
            "脚本入口静态核对通过；这是「受限执行」，不是沙箱（读和网络拦不住）。"
            if passed
            else "有脚本入口未通过静态核对，见 entrypoints。"
        ),
        "entrypoints": checks,
    }


@router.get("/{skill_id}/files")
def list_skill_files(
    skill_id: str,
    principal: str = Depends(require_human_principal),
) -> Dict[str, Any]:
    """已安装包的文件清单与哈希。没安装包时返回空清单并说明。"""
    _get_or_404(skill_id)
    package = SkillRuntime().package(skill_id) or {}
    files = package.get("files") or {}
    return {
        "skill_id": skill_id,
        "installed": bool(installed_dir(skill_id)),
        "package_hash": package.get("package_hash") or "",
        "installed_at": package.get("installed_at") or "",
        "origin": package.get("origin") or "",
        "files": [
            {"path": rel, "sha256": digest}
            for rel, digest in sorted(files.items())
        ],
    }


@router.get("/{skill_id}/export")
def export_skill(
    skill_id: str,
    principal: str = Depends(require_human_principal),
) -> Response:
    """把已安装的技能包导出成 zip。"""
    _get_or_404(skill_id)
    try:
        blob, name = export_zip(skill_id)
    except SkillInstallError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(
        content=blob,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{skill_id}.zip"'},
    )


@router.post("/{skill_id}/upgrade", status_code=200)
async def upgrade_skill(
    skill_id: str,
    file: UploadFile = File(...),
    principal: str = Depends(require_admin_principal),
) -> Dict[str, Any]:
    """用新包覆盖同一个技能（保留技能 id 与员工绑定）。

    失败时回滚到升级前的包目录 —— 不会留下"半新半旧"的技能。升级后包的哈希
    会变，之前"记住的授权"自动失效，第一次运行需要重新确认。
    """
    record = _get_or_404(skill_id)
    filename = (file.filename or "").strip()
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if suffix not in ("zip", "md", "json"):
        raise HTTPException(
            status_code=415,
            detail=f"升级只接受 .zip / .md / .json（收到「{filename or '未命名文件'}」）。",
        )
    limit = MAX_TOTAL_BYTES + 4 * 1024 * 1024 if suffix == "zip" else _IMPORT_LIMIT_BYTES
    raw = await file.read(limit + 1)
    if len(raw) > limit:
        raise HTTPException(status_code=413, detail=f"「{filename}」超过安装上限，已拒绝。")
    try:
        with stage_upload(filename, raw) as staged:
            try:
                spec = parse_upload(filename, staged.read_text())
            except McpConfigDetected as exc:
                raise HTTPException(status_code=415, detail=str(exc)) from exc
            except SkillImportError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            entrypoints = list((spec.runtime or {}).get("entrypoints") or [])
            staged.finalize(entrypoints)
            install_dir = staged.commit(skill_id, overwrite=True)
            SkillRuntime().save_package(
                skill_id,
                {
                    "skill_id": skill_id,
                    "install_dir": install_dir,
                    "origin": filename,
                    "format": spec.format,
                    "package_hash": staged.package_hash,
                    "files": staged.hashes,
                    "runtime": {
                        "limits": dict((spec.runtime or {}).get("limits") or {}),
                        "entrypoints": entrypoints,
                    },
                    "installed_at": record.get("updated_at") or record.get("created_at"),
                },
            )
    except SkillInstallError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.info("skills: upgraded %s from %s by %s", skill_id, filename, principal)
    payload = _evaluate_current(_get_or_404(skill_id))
    payload["upgrade_note"] = (
        "升级完成，包哈希已变化：之前记住的运行授权自动失效，下次运行需要重新确认。"
    )
    return payload


@router.post("/{skill_id}/run")
def run_skill(
    skill_id: str,
    payload: SkillRunRequest,
    principal: str = Depends(require_human_principal),
) -> Dict[str, Any]:
    """运行一个脚本入口。

    没授权时返回 ``202 + state=pending_approval``，由 Boss 调
    ``/v1/skills/approvals/{approval_id}/approve`` 后才真正执行。
    输出会同时写进运行记录，并由 ``skill_prompt`` 注入已绑定员工的上下文。
    """
    record = _get_or_404(skill_id)
    entrypoint = str(payload.entrypoint or "").strip()
    if not entrypoint:
        raise HTTPException(status_code=400, detail="entrypoint 不能为空。")
    try:
        state, body = SkillRuntime().run(
            record=record,
            entrypoint_id=entrypoint,
            params=dict(payload.params or {}),
            requester=principal,
            employee=payload.employee,
        )
    except SkillRunRefused as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SkillRuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    logger.info("skills: run %s/%s -> %s by %s", skill_id, entrypoint, state, principal)
    if state == "pending_approval":
        return {
            "state": state,
            "skill_id": skill_id,
            "approval": {
                "approval_id": body.get("approval_id"),
                "skill_name": body.get("skill_name"),
                "entrypoint": body.get("entrypoint"),
                "interpreter": body.get("interpreter"),
                "script": body.get("script"),
                "network": bool(body.get("network")),
                "limits": body.get("limits") or {},
                "requested_at": body.get("requested_at"),
            },
            "notice": (
                "首次运行需要你确认。确认前不会启动任何进程。"
                "注意：这是受限执行，不是沙箱，读文件与联网拦不住。"
            ),
        }
    return {"state": state, "skill_id": skill_id, "run": _public_run(body)}


def _public_run(result: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "run_id": result.get("run_id"),
        "skill_id": result.get("skill_id"),
        "entrypoint_id": result.get("entrypoint_id"),
        "status": result.get("status"),
        "exit_code": result.get("exit_code"),
        "timed_out": result.get("timed_out"),
        "duration_ms": result.get("duration_ms"),
        "peak_memory_bytes": result.get("peak_memory_bytes"),
        "cpu_ms": result.get("cpu_ms"),
        "isolation": result.get("isolation"),
        "limits": result.get("limits") or {},
        "summary": result.get("summary") or "",
        "result": result.get("result"),
        "stdout": result.get("stdout") or "",
        "stderr": result.get("stderr") or "",
        "truncated_bytes": result.get("truncated_bytes") or 0,
        "finished_at": result.get("finished_at"),
    }


@router.get("/runs/{run_id}")
def get_run(
    run_id: str,
    principal: str = Depends(require_human_principal),
) -> Dict[str, Any]:
    result = read_run(run_id)
    if not result:
        raise HTTPException(status_code=404, detail=f"没有找到运行记录「{run_id}」。")
    return _public_run(result)


@router.get("/{skill_id}/approvals")
def list_approvals(
    skill_id: str,
    principal: str = Depends(require_human_principal),
) -> Dict[str, Any]:
    """列出待确认的申请与已记住的授权（可在这里逐条清除）。"""
    _get_or_404(skill_id)
    store = SkillApprovalStore()
    remembered = [
        {
            "approval_id": row.get("approval_id"),
            "entrypoint": row.get("entrypoint"),
            "script": row.get("script"),
            "mode": row.get("mode"),
            "approved_by": row.get("approved_by"),
            "approved_at": row.get("approved_at"),
            "expires_at": row.get("expires_at"),
        }
        for row in store.remembered(skill_id)
    ]
    pending = [
        {
            "approval_id": row.get("approval_id"),
            "entrypoint": row.get("entrypoint"),
            "script": row.get("script"),
            "requested_by": row.get("requested_by"),
            "requested_at": row.get("requested_at"),
        }
        for row in store.pending(skill_id)
    ]
    return {"skill_id": skill_id, "remembered": remembered, "pending": pending}


@router.post("/approvals/{approval_id}/approve")
def approve_run(
    approval_id: str,
    payload: Optional[SkillApprovalRequest] = None,
    principal: str = Depends(require_admin_principal),
) -> Dict[str, Any]:
    """批准一次运行申请。``mode=once`` 只放行这次；``always`` 记住（90 天）。"""
    request = payload or SkillApprovalRequest()
    try:
        row = SkillApprovalStore().approve(
            approval_id, mode=request.mode, approved_by=principal
        )
    except SkillRunRefused as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    logger.info(
        "skills: approval %s for %s mode=%s by %s",
        approval_id,
        row.get("skill_id"),
        row.get("mode"),
        principal,
    )
    return {
        "approved": True,
        "approval": {
            "approval_id": row.get("approval_id"),
            "skill_id": row.get("skill_id"),
            "entrypoint": row.get("entrypoint"),
            "mode": row.get("mode"),
            "expires_at": row.get("expires_at"),
        },
        "next": "请再点一次运行；这次会真的执行脚本。",
    }


@router.post("/approvals/{approval_id}/reject")
def reject_run(
    approval_id: str,
    principal: str = Depends(require_admin_principal),
) -> Dict[str, Any]:
    try:
        row = SkillApprovalStore().reject(approval_id, rejected_by=principal)
    except SkillRunRefused as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"rejected": True, "approval_id": row.get("approval_id")}


@router.delete("/approvals/{approval_id}")
def revoke_approval(
    approval_id: str,
    principal: str = Depends(require_admin_principal),
) -> Dict[str, Any]:
    """清除一条"记住的授权"：之后该入口再次运行需要重新确认。"""
    try:
        row = SkillApprovalStore().revoke(approval_id, revoked_by=principal)
    except SkillRunRefused as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    logger.info("skills: revoked approval %s by %s", approval_id, principal)
    return {"revoked": True, "approval_id": row.get("approval_id")}


@router.delete("/{skill_id}/approvals")
def revoke_skill_approvals(
    skill_id: str,
    principal: str = Depends(require_admin_principal),
) -> Dict[str, Any]:
    """一次清掉某个技能所有"记住的授权"。"""
    cleared = SkillApprovalStore().revoke_all(skill_id=skill_id, revoked_by=principal)
    logger.info("skills: revoked %d approvals for %s by %s", cleared, skill_id, principal)
    return {"revoked": cleared, "skill_id": skill_id}


@router.get("/{skill_id}/audit")
def get_audit(
    skill_id: str,
    limit: int = Query(50, ge=1, le=500),
    principal: str = Depends(require_human_principal),
) -> Dict[str, Any]:
    try:
        rows = read_audit(skill_id, limit=limit)
    except Exception as exc:  # noqa: BLE001 - 审计读不出来要如实说
        raise HTTPException(
            status_code=503, detail=f"读取审计记录失败：{type(exc).__name__}: {exc}"
        ) from exc
    return {"skill_id": skill_id, "total": len(rows), "events": rows}
