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
   "被哪些员工使用"来自员工真实的 ``default_capabilities`` 交集，读不到就
   返回 ``null``（前端据此显示"未提供"），绝不假装"没人用"。

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


# ---------------------------------------------------------------------- #
# 序列化：风险 / 权限 / 占用一律在读取时现算
# ---------------------------------------------------------------------- #
def _evaluate(
    record: Dict[str, Any],
    capability_index: Dict[str, Dict[str, Any]],
    capability_error: Optional[str],
    assignments: Optional[List[Dict[str, Any]]],
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

    if assignments is None:
        used_by: Optional[List[str]] = None
    else:
        caps = set(capabilities)
        used_by = sorted(
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
        "used_by": used_by,
        "origin": record.get("origin"),
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
    items = [
        _evaluate(record, capability_index, capability_error, assignments)
        for record in records
    ]
    return {
        "total": len(items),
        "enabled": sum(1 for item in items if item["enabled"]),
        "skills": items,
        "capability_catalog_error": capability_error,
        "assignments_error": assignment_error,
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
    capability_index, capability_error = _load_capability_index()
    assignments, _ = _load_assignments(strict=False)
    logger.info("skills: created %s by %s", record.get("id"), principal)
    return _evaluate(record, capability_index, capability_error, assignments)


@router.post("/import", status_code=201)
async def import_skill(
    file: UploadFile = File(...),
    principal: str = Depends(require_admin_principal),
) -> Dict[str, Any]:
    """从技能文件导入（``.md`` / ``.json``）。

    压缩包一律拒绝：解包任意 zip 会引入 zip-slip 与"导入即执行"风险，而本版
    并没有沙箱化的技能运行时。拒绝比假装支持诚实。
    """
    filename = (file.filename or "").strip()
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if suffix not in ("md", "json"):
        raise HTTPException(
            status_code=415,
            detail=(
                "本版只支持 .md 与 .json 技能文件"
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

    if suffix == "json":
        spec = _parse_json_manifest(text)
        content = None
    else:
        spec = _parse_markdown_skill(text, filename)
        content = text

    try:
        capabilities = normalize_capabilities(spec["capabilities"])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    capability_index, _ = _load_capability_index()
    missing = [cap for cap in capabilities if cap not in capability_index]
    if missing:
        # 允许导入但风险记为未知且不默认启用：先让人看见"它要的能力本机没有"。
        risk = "unknown"
    else:
        risk = "low"
        for capability in capabilities:
            risk = _max_risk(risk, str(capability_index[capability]["risk"]))

    enabled = risk == "low"
    store = _store()
    try:
        with bind_request_principal(principal, kind="human", source="http-request"):
            record = store.create(
                name=spec["name"],
                description=spec["description"],
                source="imported",
                version=spec["version"],
                capabilities=capabilities,
                declared_permissions=spec["permissions"],
                declared_risk=spec["declared_risk"],
                content=content,
                origin=filename,
                enabled=enabled,
            )
    except SkillConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SkillStoreError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    assignments, _ = _load_assignments(strict=False)
    logger.info(
        "skills: imported %s from %s by %s (enabled=%s)",
        record.get("id"),
        filename,
        principal,
        enabled,
    )
    return _evaluate(record, capability_index, None, assignments)


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
    capability_index, capability_error = _load_capability_index()
    assignments, _ = _load_assignments(strict=False)
    return _evaluate(record, capability_index, capability_error, assignments)


@router.delete("/{skill_id}")
def delete_skill(
    skill_id: str,
    principal: str = Depends(require_admin_principal),
) -> Dict[str, Any]:
    """删除技能。**有员工在用时返回 409 并点名**，不静默级联删除。"""
    record = _get_or_404(skill_id)
    capabilities = set(record.get("capabilities") or ())
    if capabilities:
        assignments, error = _load_assignments(strict=True)
        if assignments is None:
            # 读不出占用关系时宁可不删：静默删除可能把员工正在用的技能抽走。
            raise HTTPException(
                status_code=503,
                detail=(
                    "读取员工占用情况失败，为避免误删正在被员工使用的技能，"
                    f"本次删除没有执行（{error}）。"
                ),
            )
        in_use = sorted(
            {
                str(row.get("name"))
                for row in assignments
                if capabilities & set(row.get("default_capabilities") or ())
            }
        )
        if in_use:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"技能「{record.get('name')}」仍被 {len(in_use)} 位员工使用："
                    f"{'、'.join(in_use)}。请先在员工设置里移除对应能力，"
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
    logger.info("skills: deleted %s by %s", skill_id, principal)
    return {"deleted": True, "skill_id": removed.get("id"), "name": removed.get("name")}


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
    capability_index, capability_error = _load_capability_index()
    assignments, _ = _load_assignments(strict=False)
    evaluated = _evaluate(record, capability_index, capability_error, assignments)
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
    return _evaluate(updated, capability_index, capability_error, assignments)


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
    capability_index, capability_error = _load_capability_index()
    assignments, _ = _load_assignments(strict=False)
    logger.info("skills: disabled %s by %s", skill_id, principal)
    return _evaluate(updated, capability_index, capability_error, assignments)


# ---------------------------------------------------------------------- #
# 技能文件解析
# ---------------------------------------------------------------------- #
def _parse_json_manifest(text: str) -> Dict[str, Any]:
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=400, detail=f"技能 JSON 解析失败：{exc.msg}（第 {exc.lineno} 行）。"
        ) from exc
    if not isinstance(document, dict):
        raise HTTPException(status_code=400, detail="技能 JSON 顶层必须是对象。")
    name = document.get("name")
    if not isinstance(name, str) or not name.strip():
        raise HTTPException(status_code=400, detail="技能 JSON 缺少 name 字段。")
    description = document.get("description") or ""
    if not isinstance(description, str):
        raise HTTPException(status_code=400, detail="技能 JSON 的 description 必须是字符串。")
    version = document.get("version") or ""
    if not isinstance(version, (str, int, float)):
        raise HTTPException(status_code=400, detail="技能 JSON 的 version 必须是字符串。")
    capabilities = document.get("capabilities") or []
    permissions = document.get("permissions") or []
    declared_risk = document.get("risk") or ""
    if not isinstance(declared_risk, str):
        raise HTTPException(status_code=400, detail="技能 JSON 的 risk 必须是字符串。")
    return {
        "name": name,
        "description": description,
        "version": str(version),
        "capabilities": capabilities,
        "permissions": permissions,
        "declared_risk": declared_risk,
    }


def _parse_markdown_skill(text: str, filename: str) -> Dict[str, Any]:
    """从 Markdown 里取名称与说明：首个 ``# 标题`` 作名称，首段正文作说明。"""
    lines = text.splitlines()
    name = ""
    body: List[str] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not name and stripped.startswith("#"):
            candidate = stripped.lstrip("#").strip()
            if candidate:
                name = candidate
                body = lines[index + 1:]
                break
    if not name:
        stem = filename.rsplit("/", 1)[-1].rsplit(".", 1)[0] if filename else ""
        name = stem or "未命名技能"
        body = lines
    paragraph: List[str] = []
    for line in body:
        stripped = line.strip()
        if not stripped:
            if paragraph:
                break
            continue
        if stripped.startswith("#"):
            continue
        paragraph.append(stripped)
    description = " ".join(paragraph).strip()
    return {
        "name": name,
        "description": description,
        "version": "",
        "capabilities": [],
        "permissions": [],
        "declared_risk": "",
    }
