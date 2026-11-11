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
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from .policy import require_admin_principal, require_human_principal
from .skill_bindings import (
    SkillBindingConflict,
    SkillBindingError,
    SkillBindingStore,
)
from .skill_import import McpConfigDetected, SkillImportError, parse_upload
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
        # 拍板：本版不执行脚本，恒为 false；前端据此标「含脚本，本机未启用」。
        "execution_allowed": False,
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
    """从技能文件导入（``.md`` / ``.json``）。

    解析规则见 ``skill_import``：Agent Skills 的 ``SKILL.md`` 按 YAML
    frontmatter 读 ``name``/``description``/``license``/``allowed-tools``/
    ``metadata``，映射不了的字段存原文并写进 ``import_report``。

    **行为变更**：导入的技能一律落为「未启用」，由用户显式启用；旧版本会在
    低风险时自动启用。旧数据不做迁移 —— 已存在的记录保留原有 ``enabled``，
    新导入才走新规则。

    压缩包与 MCP 配置都拒绝：前者会引入 zip-slip 与"导入即执行"，后者是
    工具源而不是技能。
    """
    filename = (file.filename or "").strip()
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if suffix not in ("md", "json"):
        raise HTTPException(
            status_code=415,
            detail=(
                "本版只支持 .md（Agent Skills）与 .json（本机技能清单）"
                f"（收到「{filename or '未命名文件'}」）。压缩包导入尚未提供，"
                "请解出单个技能文件后再导入。"
            ),
        )

    raw = await file.read(_IMPORT_LIMIT_BYTES + 1)
    if len(raw) > _IMPORT_LIMIT_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"技能文件超过 {_IMPORT_LIMIT_BYTES // 1024} KiB 上限，已拒绝导入。",
        )
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=400, detail="技能文件不是 UTF-8 文本，无法解析。"
        ) from exc
    if not text.strip():
        raise HTTPException(status_code=400, detail="技能文件是空的。")

    try:
        spec = parse_upload(filename, text)
    except McpConfigDetected as exc:
        # MCP 是工具源，不是技能：明确 415，并告诉用户正确的入口。
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    except SkillImportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        capabilities = normalize_capabilities(spec.capabilities)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    capability_index, _ = _load_capability_index()
    missing = [cap for cap in capabilities if cap not in capability_index]
    if missing:
        # 允许导入但风险记为未知：先让人看见"它要的能力本机没有"。
        risk = "unknown"
    else:
        risk = "low"
        for capability in capabilities:
            risk = _max_risk(risk, str(capability_index[capability]["risk"]))

    store = _store()
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
                origin=filename,
                # 行为变更：导入不再自动启用（无论风险高低）。
                enabled=False,
                fmt=spec.format,
                license=spec.license,
                allowed_tools=spec.allowed_tools,
                metadata=spec.metadata,
                raw_frontmatter=spec.raw_frontmatter,
                has_scripts=spec.has_scripts,
                script_refs=spec.script_refs,
                files=spec.files,
                name_source=spec.name_source,
                import_report=spec.import_report,
                execution_allowed=False,
            )
    except SkillConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SkillStoreError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    assignments, _ = _load_assignments(strict=False)
    bindings, bindings_error = _load_bindings()
    logger.info(
        "skills: imported %s from %s by %s (format=%s, risk=%s, enabled=False)",
        record.get("id"),
        filename,
        principal,
        spec.format,
        risk,
    )
    payload = _evaluate(
        record,
        capability_index,
        None,
        assignments,
        bindings=bindings,
        bindings_error=bindings_error,
    )
    payload["import_note"] = (
        "导入已成功，但不会自动启用（行为变更）：请检查导入报告后手动启用。"
        if not record.get("has_scripts")
        else "导入已成功；该技能含脚本引用，本机未启用脚本执行，正文可作为提示词使用。"
    )
    return payload


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

