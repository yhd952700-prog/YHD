"""AI 员工补齐能力：复制、导入/导出配置、工作质量评估、预算。

路由挂在既有 ``ai_mgmt_router`` 下（``/v1/employees/*``），不新增 main.py 挂载点。

四个能力的边界（必须如实呈现，不造数据）
----------------------------------------
* **复制**：复制的是"能带走的配置"——员工级提示词、agent 类型/数量、
  ``default_capabilities``（工具）、技能绑定。**知识库权限是账号级**（本仓库没有
  员工级知识授权），副本只能继承目标账号自己的可见范围；**模型配置是全局的**
  （本仓库没有员工级模型），因此导出/复制里不会出现任何模型 Key。
* **导入/导出**：导出为可读 JSON，导入分两步——先 dry-run 预览（校验 + 冲突 +
  会做什么），确认后才落盘。导出路径上做递归密钥剔除，且导出内容本身只取自
  配置白名单，不拼任何凭据。
* **质量评估**：全部来自 ``liuhao_tasks.sqlite`` 的真实行（状态、起止时间、
  人工重试/接管记录），不做模型打分；没有数据或字段缺失时返回 null 并说明原因。
* **预算**：判定与拦截在 ``src/ai/budget.py`` + ``AIStateManager`` 派活闸门，
  这里只提供读写接口。超限返回 402（不是提示条）。
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

EXPORT_FORMAT = "liuhao.employee-config"
EXPORT_VERSION = 1
_NAME_LIMIT = 128
_PROMPT_LIMIT = 20000
_MAX_AGENTS = 64

#: 导出/导入时一律剔除的敏感键（白名单之外的第二道保险）。
_SECRET_KEY_RE = re.compile(
    r"(api[_-]?key|apikey|secret|token|password|passwd|credential|authorization|"
    r"bearer|private[_-]?key|access[_-]?key)",
    re.IGNORECASE,
)

NOTES_BASE = [
    "导出内容只包含员工配置（提示词、agent 类型、工具能力、技能绑定），不含任何密钥。",
    "模型配置是全局设置（「我的 Key / 模型管理」），本版本没有员工级模型配置，因此配置里也没有模型字段。",
    "知识库权限是账号级：副本/导入的员工只能看到目标账号自己的知识范围，不会继承导出方的知识库权限。",
]

_TERMINAL = ("completed", "failed", "cancelled")


# ─── 请求体 ────────────────────────────────────────────────────


class CopyEmployeeRequest(BaseModel):
    name: Optional[str] = Field(default=None, max_length=_NAME_LIMIT)
    copy_skills: bool = True
    copy_capabilities: bool = True
    copy_prompts: bool = True


class ImportRequest(BaseModel):
    config: Any = Field(description="导出的员工配置（JSON 对象或 JSON 文本）")
    on_conflict: str = Field(default="skip")
    as_name: Optional[str] = Field(default=None, max_length=_NAME_LIMIT)
    confirm: bool = False


class BudgetUpdateRequest(BaseModel):
    enabled: Optional[bool] = None
    calls_limit: Optional[int] = None
    tokens_limit: Optional[int] = None
    cost_limit: Optional[float] = None
    period: Optional[str] = None
    reset_usage: bool = False


# ─── 通用小工具 ────────────────────────────────────────────────


def _http(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _http_from_goal(exc: Exception) -> HTTPException:
    from .ai_management import _http_from_goal_error  # 延迟导入，避免循环

    return _http_from_goal_error(exc)


def _as_viewer(viewer: Any) -> Any:
    from .ai_management import _as_viewer as convert

    return convert(viewer)


def _require(viewer: Any) -> Optional[str]:
    """401：没有已登录的主账号/成员身份。"""
    real = _as_viewer(viewer)
    if real is None:
        raise _http(401, "login_required", "请先登录后再管理 AI 员工。")
    return str(getattr(real, "principal", "") or "")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _clean_name(value: Any, label: str = "员工名") -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label}不能为空。")
    if len(text) > _NAME_LIMIT:
        raise ValueError(f"{label}最多 {_NAME_LIMIT} 个字符。")
    if any(ord(ch) < 32 for ch in text):
        raise ValueError(f"{label}不能包含控制字符。")
    return text


def _unique_name(manager: Any, desired: str) -> str:
    """同名时自动加后缀（``xxx-copy`` → ``xxx-copy-2`` …）。"""
    base = desired[:_NAME_LIMIT]
    store = manager._store()
    if store.load(base) is None:
        return base
    for index in range(2, 100):
        suffix = f"-{index}"
        candidate = f"{base[: _NAME_LIMIT - len(suffix)]}{suffix}"
        if store.load(candidate) is None:
            return candidate
    raise _http(409, "employee_name_exhausted", "同名副本过多，请手动指定一个名字。")


def _strip_secrets(value: Any, removed: List[str], path: str = "") -> Any:
    """递归剔除疑似凭据字段，返回干净副本；``removed`` 记录被剔除的路径。"""
    if isinstance(value, dict):
        cleaned: Dict[str, Any] = {}
        for key, item in value.items():
            label = f"{path}.{key}" if path else str(key)
            if _SECRET_KEY_RE.search(str(key)):
                removed.append(label)
                continue
            cleaned[str(key)] = _strip_secrets(item, removed, label)
        return cleaned
    if isinstance(value, list):
        return [
            _strip_secrets(item, removed, f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    return value


# ─── 配置抽取 / 校验 ───────────────────────────────────────────


def _employee_prompts(emp: Any) -> Dict[str, str]:
    prompts: Dict[str, str] = {}
    for agent_type, text in (getattr(emp, "system_prompts", None) or {}).items():
        if str(agent_type or "").strip() and str(text or "").strip():
            prompts[str(agent_type)] = str(text)
    for agent in (getattr(emp, "agents", None) or {}).values():
        agent_type = str(getattr(agent, "agent_type", "") or "").strip()
        prompt = str(getattr(agent, "system_prompt", "") or "")
        if agent_type and prompt and agent_type not in prompts:
            prompts[agent_type] = prompt
    return prompts


def _employee_config(emp: Any, skills: List[str]) -> Dict[str, Any]:
    agents = list((getattr(emp, "agents", None) or {}).values())
    return {
        "name": emp.name,
        "employee_id": emp.stable_id(),
        "role_description": str(getattr(emp, "role_description", "") or ""),
        "agent_count": len(agents) or 3,
        "agent_types": [str(getattr(agent, "agent_type", "") or "") for agent in agents],
        "system_prompts": _employee_prompts(emp),
        "default_capabilities": list(getattr(emp, "default_capabilities", None) or []),
        "employment_status": str(getattr(emp, "employment_status", "") or ""),
        "skills": list(skills),
    }


def _export_document(emp: Any, skills: List[str]) -> Dict[str, Any]:
    removed: List[str] = []
    config = _strip_secrets(_employee_config(emp, skills), removed)
    notes = list(NOTES_BASE)
    if removed:
        notes.append("以下疑似凭据字段已在导出时剔除：" + "、".join(removed))
    return {
        "format": EXPORT_FORMAT,
        "format_version": EXPORT_VERSION,
        "exported_at": _now_iso(),
        "source": {
            "employee_name": emp.name,
            "owner_account_id": str(getattr(emp, "owner_account_id", "") or ""),
        },
        "employee": config,
        "notes": notes,
    }


def _payload_entries(raw: Any) -> List[Dict[str, Any]]:
    """把导入载荷统一成"待导入配置列表"。"""
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            raise ValueError("导入内容是空的。")
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"导入内容不是合法 JSON：{exc}") from exc
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        if isinstance(raw.get("employees"), list):
            return list(raw["employees"])
        if isinstance(raw.get("employee"), dict):
            return [raw["employee"]]
        if raw.get("name"):
            return [raw]
        raise ValueError("导入内容里没有找到员工配置（缺少 employee / employees / name）。")
    raise ValueError("导入内容必须是 JSON 对象或 JSON 文本。")


def _known_skill_ids() -> Optional[set]:
    try:
        from .skill_store import SkillStore

        records = SkillStore().list_records()
        return {str(row.get("id") or row.get("skill_id") or "") for row in records}
    except Exception:  # noqa: BLE001 - 读不到就不做"技能不存在"的断言
        logger.warning("employee import: skill store unavailable", exc_info=True)
        return None


def _validate_config(manager: Any, item: Any, index: int) -> Dict[str, Any]:
    """单条配置校验：返回 ``{config, errors, warnings}``（不落盘）。"""
    errors: List[str] = []
    warnings: List[str] = []
    if not isinstance(item, dict):
        return {"config": {}, "errors": [f"第 {index + 1} 条不是对象。"], "warnings": []}

    removed: List[str] = []
    clean = _strip_secrets(item, removed)
    if removed:
        warnings.append("已剔除疑似凭据字段：" + "、".join(removed))

    try:
        name = _clean_name(clean.get("name"))
    except ValueError as exc:
        name = ""
        errors.append(str(exc))

    role = clean.get("role_description", "")
    if role is None:
        role = ""
    if not isinstance(role, str):
        errors.append("role_description 必须是字符串。")
        role = ""
    if len(role) > 4000:
        warnings.append("role_description 超过 4000 字，已截断。")
        role = role[:4000]

    agent_types = clean.get("agent_types") or []
    if not isinstance(agent_types, list):
        errors.append("agent_types 必须是字符串数组。")
        agent_types = []
    agent_types = [str(item).strip() for item in agent_types if str(item).strip()]
    agent_count = clean.get("agent_count", len(agent_types) or 3)
    try:
        agent_count = int(agent_count)
    except (TypeError, ValueError):
        errors.append("agent_count 必须是整数。")
        agent_count = len(agent_types) or 3
    if not 1 <= agent_count <= _MAX_AGENTS:
        errors.append(f"agent_count 必须在 1–{_MAX_AGENTS} 之间。")

    prompts_raw = clean.get("system_prompts") or {}
    prompts: Dict[str, str] = {}
    if not isinstance(prompts_raw, dict):
        errors.append("system_prompts 必须是对象（agent 类型 → 提示词）。")
    else:
        for key, value in prompts_raw.items():
            if not isinstance(value, str):
                warnings.append(f"提示词「{key}」不是字符串，已忽略。")
                continue
            text = value[: _PROMPT_LIMIT]
            if len(value) > _PROMPT_LIMIT:
                warnings.append(f"提示词「{key}」超过 {_PROMPT_LIMIT} 字，已截断。")
            if text.strip():
                prompts[str(key)] = text

    caps_raw = clean.get("default_capabilities") or []
    capabilities: List[str] = []
    if not isinstance(caps_raw, list):
        errors.append("default_capabilities 必须是字符串数组。")
    else:
        known = set()
        try:
            catalog = manager.capability_catalog()
            known = {str(entry.get("id") or entry.get("capability") or "") for entry in catalog}
        except Exception:  # noqa: BLE001
            logger.warning("employee import: capability catalog unavailable", exc_info=True)
        for cap in caps_raw:
            cap_id = str(cap).strip()
            if not cap_id:
                continue
            if known and cap_id not in known:
                warnings.append(f"未知工具能力「{cap_id}」已跳过。")
                continue
            capabilities.append(cap_id)

    skills_raw = clean.get("skills") or []
    skills: List[str] = []
    if not isinstance(skills_raw, list):
        warnings.append("skills 不是数组，已忽略。")
    else:
        known_skills = _known_skill_ids()
        for skill in skills_raw:
            skill_id = str(skill).strip()
            if not skill_id:
                continue
            if known_skills is not None and skill_id not in known_skills:
                warnings.append(f"技能「{skill_id}」在本机不存在，已跳过。")
                continue
            if skill_id not in skills:
                skills.append(skill_id)

    config = {
        "name": name,
        "employee_id": str(clean.get("employee_id") or ""),
        "role_description": role,
        "agent_count": agent_count,
        "agent_types": agent_types,
        "system_prompts": prompts,
        "default_capabilities": capabilities,
        "skills": skills,
    }
    return {"config": config, "errors": errors, "warnings": warnings}


def _existing_employee_ids(manager: Any, viewer: Any) -> Dict[str, str]:
    """employee_id → 当前名字，用于 id 冲突判定（只取当前账号可见范围）。"""
    mapping: Dict[str, str] = {}
    try:
        for summary in manager.list_all_employees(viewer):
            employee_id = str(summary.get("employee_id") or "")
            name = str(summary.get("name") or "")
            if employee_id and name:
                mapping[employee_id] = name
    except Exception:  # noqa: BLE001 - 列表不可用时退化为"不判定 id 冲突"
        logger.warning("employee import: employee listing unavailable", exc_info=True)
    return mapping


def _plan_import(
    manager: Any,
    viewer: Any,
    raw: Any,
    *,
    on_conflict: str = "skip",
    as_name: Optional[str] = None,
) -> Dict[str, Any]:
    conflict_mode = str(on_conflict or "skip").strip().lower()
    if conflict_mode not in ("skip", "rename"):
        raise ValueError("on_conflict 只能是 skip 或 rename。")
    entries = _payload_entries(raw)
    if not entries:
        raise ValueError("导入内容里没有员工配置。")
    if as_name and len(entries) != 1:
        raise ValueError("as_name 只能用于单员工导入。")

    store = manager._store()
    known_ids = _existing_employee_ids(manager, viewer)
    plan: List[Dict[str, Any]] = []
    seen_names: Dict[str, int] = {}

    for index, item in enumerate(entries):
        checked = _validate_config(manager, item, index)
        config = checked["config"]
        errors = list(checked["errors"])
        warnings = list(checked["warnings"])
        requested = config.get("name") or ""
        final_name = requested
        action = "create"
        reason = ""

        if as_name and len(entries) == 1:
            try:
                final_name = _clean_name(as_name)
            except ValueError as exc:
                errors.append(str(exc))
            else:
                config["name"] = final_name

        if not errors:
            employee_id = str(config.get("employee_id") or "")
            id_owner = known_ids.get(employee_id) if employee_id else None
            if id_owner:
                action = "skip"
                reason = f"employee_id 冲突：本机已有同 id 员工「{id_owner}」，不能重复导入。"
            elif final_name.casefold() in seen_names:
                errors.append(f"导入文件内员工名重复：「{final_name}」。")
            elif store.load(final_name) is not None:
                if conflict_mode == "rename":
                    config["name"] = _unique_name(manager, f"{final_name}-import")
                    final_name = config["name"]
                    action = "rename"
                    reason = "同名员工已存在，按冲突策略自动改名。"
                else:
                    action = "skip"
                    reason = "同名员工已存在，按冲突策略跳过（不改动现有员工）。"
            seen_names[final_name.casefold()] = index

        plan.append(
            {
                "index": index + 1,
                "requested_name": requested,
                "final_name": final_name,
                "action": "error" if errors else action,
                "reason": reason,
                "errors": errors,
                "warnings": warnings,
                "will_be_disabled": True,
                "config": config,
            }
        )

    summary = {
        "create": sum(1 for row in plan if row["action"] == "create"),
        "rename": sum(1 for row in plan if row["action"] == "rename"),
        "skip": sum(1 for row in plan if row["action"] == "skip"),
        "error": sum(1 for row in plan if row["action"] == "error"),
    }
    return {
        "dry_run": True,
        "on_conflict": conflict_mode,
        "plan": plan,
        "summary": summary,
        "notes": list(NOTES_BASE)
        + ["导入的员工一律先停用；确认无误后请在员工页手动启用。"],
    }


def _apply_plan(manager: Any, viewer: Any, plan: Dict[str, Any]) -> Dict[str, Any]:
    """按预览结果落盘；逐条返回真实结果，不把失败包装成成功。"""
    from .skill_bindings import SkillBindingStore

    actor = _require(viewer) or "unknown"
    owner = actor
    bindings = SkillBindingStore()
    results: List[Dict[str, Any]] = []

    for row in plan["plan"]:
        if row["action"] not in ("create", "rename"):
            results.append(
                {
                    "name": row["final_name"],
                    "status": row["action"],
                    "message": row["reason"] or "按冲突策略未导入。",
                }
            )
            continue
        config = row["config"]
        try:
            hired = manager.hire_employee(
                config["name"],
                agent_count=config["agent_count"],
                agent_types=config["agent_types"] or None,
                role_description=config["role_description"],
                default_capabilities=config["default_capabilities"],
                owner_account_id=owner,
            )
            if not hired.get("created"):
                results.append(
                    {
                        "name": config["name"],
                        "status": "skip",
                        "message": "员工名在导入瞬间被占用，已跳过（现有员工未被修改）。",
                    }
                )
                continue
            emp = manager._store().load(config["name"])
            if emp is None:
                results.append(
                    {"name": config["name"], "status": "failed", "message": "创建后读取失败。"}
                )
                continue
            if config["system_prompts"]:
                emp.system_prompts = dict(config["system_prompts"])
                manager._store().save(emp)
            manager.update_employee(config["name"], viewer, employment_status="disabled")
            bound: List[str] = []
            for skill_id in config["skills"]:
                bindings.bind(skill_id=skill_id, employee=config["name"], actor=actor)
                bound.append(skill_id)
            results.append(
                {
                    "name": config["name"],
                    "status": "created",
                    "message": "已创建并停用。",
                    "skills": bound,
                }
            )
        except Exception as exc:  # noqa: BLE001 - 单条失败不拖垮整批
            logger.warning("employee import failed for %r: %s", config.get("name"), exc)
            message = getattr(exc, "detail", None) or str(exc) or "导入失败。"
            results.append({"name": config.get("name") or "", "status": "failed", "message": message})

    created = sum(1 for item in results if item["status"] == "created")
    return {
        "dry_run": False,
        "results": results,
        "created_count": created,
        "skipped_count": sum(1 for item in results if item["status"] == "skip"),
        "failed_count": sum(1 for item in results if item["status"] == "failed"),
        "notes": list(NOTES_BASE),
    }


# ─── 质量评估 ──────────────────────────────────────────────────


def _ledger_connection() -> Optional[sqlite3.Connection]:
    from .task_ledger import default_ledger_path

    path = default_ledger_path()
    if not path or not _file_exists(path):
        return None
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)
        connection.row_factory = sqlite3.Row
        return connection
    except sqlite3.Error:
        logger.warning("employee quality: ledger open failed", exc_info=True)
        return None


def _file_exists(path: str) -> bool:
    import os

    try:
        return os.path.exists(path)
    except OSError:
        return False


def _quality_for(name: str) -> Dict[str, Any]:
    """基于台账真实行计算；算不出的字段返回 null 并写清原因。"""
    base: Dict[str, Any] = {
        "employee": name,
        "available": False,
        "source": "liuhao_tasks.sqlite",
        "generated_at": _now_iso(),
        "definitions": {
            "terminal": "已完成 / 失败 / 已取消",
            "completion_rate": "已完成 ÷ 终态任务数（不含执行中）",
            "failure_rate": "失败 ÷ 终态任务数",
            "avg_duration_seconds": "已完成任务的 (completed_at - started_at) 平均值；缺时间戳的任务不计入",
            "manual_rework_count": "人工重试申请 + 人工接管次数（只统计 actor=human 的真实记录）",
        },
        "sample": 0,
        "status_counts": {},
        "completed": 0,
        "failed": 0,
        "cancelled": 0,
        "in_progress": 0,
        "terminal": 0,
        "completion_rate": None,
        "failure_rate": None,
        "avg_duration_seconds": None,
        "duration_sample": 0,
        "duration_missing": 0,
        "manual_rework_count": None,
        "manual_retry_count": None,
        "takeover_count": None,
        "handed_off_count": None,
        "auto_retry_attempts": None,
        "last_task_at": None,
        "notes": [],
    }
    connection = _ledger_connection()
    if connection is None:
        base["notes"].append("台账文件不存在或无记录，无法计算质量指标。")
        return base

    pattern = f"{name}-a%"
    try:
        rows = connection.execute(
            """
            SELECT status, started_at, completed_at, attempt_count, human_hold, created_at
            FROM tasks
            WHERE archived = 0
              AND (assignee_name = ? OR assignee_id = ? OR assignee_id LIKE ?)
            """,
            (name, name, pattern),
        ).fetchall()
        takeover_rows = connection.execute(
            "SELECT task_id, detail_json, actor_id FROM task_runs WHERE event = 'takeover'"
        ).fetchall()
        retry_rows = connection.execute(
            """
            SELECT t.task_id
            FROM task_runs r JOIN tasks t ON t.task_id = r.task_id
            WHERE r.event = 'retry_requested' AND r.actor_type = 'human'
              AND t.archived = 0
              AND (t.assignee_name = ? OR t.assignee_id = ? OR t.assignee_id LIKE ?)
            """,
            (name, name, pattern),
        ).fetchall()
    except sqlite3.Error as exc:
        logger.warning("employee quality query failed: %s", exc)
        base["notes"].append(f"台账查询失败，无法计算：{exc}")
        return base
    finally:
        try:
            connection.close()
        except sqlite3.Error:  # pragma: no cover
            pass

    status_counts: Dict[str, int] = {}
    durations: List[float] = []
    duration_missing = 0
    attempt_sum = 0
    last_task_at: Optional[float] = None
    for row in rows:
        status = str(row["status"] or "")
        status_counts[status] = status_counts.get(status, 0) + 1
        attempt_sum += int(row["attempt_count"] or 0)
        created = row["created_at"]
        if created is not None:
            last_task_at = max(last_task_at or float(created), float(created))
        if status in _TERMINAL and status == "completed":
            started, completed = row["started_at"], row["completed_at"]
            if started is None or completed is None:
                duration_missing += 1
            else:
                durations.append(max(0.0, float(completed) - float(started)))

    handed_off = 0
    takeover_seen: set = set()
    for row in takeover_rows:
        try:
            detail = json.loads(row["detail_json"] or "{}")
        except (TypeError, ValueError):
            continue
        previous = (detail or {}).get("previous_assignee") or {}
        prev_name = str(previous.get("name") or "").strip()
        prev_id = str(previous.get("id") or "").strip()
        if prev_name == name or prev_id == name:
            handed_off += 1
            takeover_seen.add(str(row["task_id"]))

    sample = len(rows)
    completed = status_counts.get("completed", 0)
    failed = status_counts.get("failed", 0)
    cancelled = status_counts.get("cancelled", 0)
    terminal = completed + failed + cancelled
    in_progress = sample - terminal

    base.update(
        {
            "available": sample > 0 or handed_off > 0,
            "sample": sample,
            "status_counts": status_counts,
            "completed": completed,
            "failed": failed,
            "cancelled": cancelled,
            "in_progress": in_progress,
            "terminal": terminal,
            "completion_rate": round(completed / terminal, 4) if terminal else None,
            "failure_rate": round(failed / terminal, 4) if terminal else None,
            "avg_duration_seconds": (
                round(sum(durations) / len(durations), 2) if durations else None
            ),
            "duration_sample": len(durations),
            "duration_missing": duration_missing,
            "manual_rework_count": len(retry_rows) + handed_off,
            "manual_retry_count": len(retry_rows),
            "takeover_count": handed_off,
            "handed_off_count": handed_off,
            "auto_retry_attempts": attempt_sum,
            "last_task_at": (
                datetime.fromtimestamp(last_task_at, timezone.utc).isoformat()
                if last_task_at
                else None
            ),
        }
    )
    if not sample:
        base["notes"].append("该员工还没有可统计的历史任务。")
    if terminal and duration_missing:
        base["notes"].append(
            f"{duration_missing} 个已完成任务缺少开始/结束时间，未计入平均耗时。"
        )
    if handed_off:
        base["notes"].append(
            f"{handed_off} 个任务已被人工接管，已移出该员工的当前队列；"
            "接管计入人工返工，其完成率不再算在该员工名下。"
        )
    return base


# ─── 路由 ──────────────────────────────────────────────────────


def build_router(viewer_dependency: Optional[Callable[..., Any]] = None) -> APIRouter:
    """构造员工补齐能力路由；``viewer_dependency`` 由 ai_management 注入。"""

    def _no_viewer() -> Any:  # pragma: no cover - 仅测试/无鉴权模式
        return None

    dependency = viewer_dependency or _no_viewer
    router = APIRouter(prefix="/employees", tags=["employees"])

    def _manager():
        from .ai_management import AIStateManager

        return AIStateManager()

    def _visible_or_404(manager: Any, name: str, viewer: Any):
        emp = manager._visible_employee(name, viewer)
        if emp is None:
            raise _http(404, "employee_not_found", f"员工不存在或不在可见范围：{name}")
        return emp

    def _writable_or_404(manager: Any, name: str, viewer: Any):
        emp = manager._visible_employee(name, viewer)
        if emp is None or not manager._employee_writable(emp, viewer):
            raise _http(404, "employee_not_found", f"员工不存在或不在可见范围：{name}")
        return emp

    # ── 复制 ────────────────────────────────────────────────
    @router.post("/{name}/copy", status_code=201)
    def copy_employee(
        name: str,
        req: Optional[CopyEmployeeRequest] = Body(default=None),
        viewer: Any = Depends(dependency),
    ) -> Dict[str, Any]:
        """复制员工配置；副本默认停用，不继承任务、记忆与运行历史。"""
        from .skill_bindings import SkillBindingError, SkillBindingStore

        _require(viewer)
        manager = _manager()
        body = req or CopyEmployeeRequest()
        source = _visible_or_404(manager, name, viewer)
        owner = _require(viewer)

        try:
            requested = _clean_name(body.name) if body.name else f"{source.name}-copy"
        except ValueError as exc:
            raise _http(400, "invalid_employee_name", str(exc)) from exc
        target = _unique_name(manager, requested)

        caps = list(source.default_capabilities or []) if body.copy_capabilities else []
        try:
            hired = manager.hire_employee(
                target,
                agent_count=len(source.agents) or 3,
                agent_types=[str(getattr(a, "agent_type", "") or "") for a in source.agents.values()],
                role_description=str(getattr(source, "role_description", "") or ""),
                default_capabilities=caps,
                owner_account_id=owner,
            )
        except Exception as exc:  # noqa: BLE001 - 统一转成 HTTP
            raise _http_from_goal(exc) from exc
        if not hired.get("created"):
            raise _http(409, "employee_name_taken", f"员工名已被占用：{target}")

        prompts = _employee_prompts(source) if body.copy_prompts else {}
        if prompts:
            emp = manager._store().load(target)
            if emp is not None:
                emp.system_prompts = dict(prompts)
                manager._store().save(emp)
        manager.update_employee(target, viewer, employment_status="disabled")

        copied_skills: List[str] = []
        skill_warnings: List[str] = []
        if body.copy_skills:
            bindings = SkillBindingStore()
            try:
                source_skills = bindings.skills_for_employee(source.name)
            except SkillBindingError as exc:
                source_skills = []
                skill_warnings.append(f"技能绑定读取失败，未复制技能：{exc}")
            for skill_id in source_skills:
                try:
                    bindings.bind(skill_id=skill_id, employee=target, actor=owner or "unknown")
                    copied_skills.append(skill_id)
                except SkillBindingError as exc:
                    skill_warnings.append(f"技能「{skill_id}」绑定失败：{exc}")

        return {
            "created": True,
            "source": source.name,
            "name": target,
            "employment_status": "disabled",
            "copied": {
                "skills": copied_skills,
                "capabilities": caps,
                "prompts": sorted(prompts.keys()),
            },
            "warnings": skill_warnings,
            "notes": list(NOTES_BASE)
            + ["副本默认停用；确认配置无误后再启用，避免一复制就开跑。"],
        }

    # ── 导出 ────────────────────────────────────────────────
    @router.get("/{name}/export")
    def export_employee(name: str, viewer: Any = Depends(dependency)) -> Dict[str, Any]:
        """导出员工配置为可读 JSON：只含配置白名单字段，绝不含密钥。"""
        from .skill_bindings import SkillBindingError, SkillBindingStore

        _require(viewer)
        manager = _manager()
        emp = _visible_or_404(manager, name, viewer)
        try:
            skills = SkillBindingStore().skills_for_employee(emp.name)
        except SkillBindingError as exc:
            logger.warning("employee export: bindings unavailable: %s", exc)
            skills = []
        return _export_document(emp, skills)

    # ── 导入（预览 / 确认）──────────────────────────────────
    @router.post("/import/preview")
    def import_preview(
        req: ImportRequest,
        viewer: Any = Depends(dependency),
    ) -> Dict[str, Any]:
        """导入 dry-run：校验、冲突处理与将执行的动作；不落盘。"""
        _require(viewer)
        try:
            return _plan_import(
                _manager(),
                viewer,
                req.config,
                on_conflict=req.on_conflict,
                as_name=req.as_name,
            )
        except ValueError as exc:
            raise _http(400, "invalid_import_payload", str(exc)) from exc

    @router.post("/import")
    def import_employee(
        req: ImportRequest,
        viewer: Any = Depends(dependency),
    ) -> Dict[str, Any]:
        """确认导入：重新校验后落盘；未先预览（confirm=false）一律拒绝。"""
        _require(viewer)
        if not req.confirm:
            raise _http(
                400,
                "import_confirmation_required",
                "请先用 /v1/employees/import/preview 预览结果，确认后再带 confirm=true 导入。",
            )
        manager = _manager()
        try:
            plan = _plan_import(
                manager,
                viewer,
                req.config,
                on_conflict=req.on_conflict,
                as_name=req.as_name,
            )
        except ValueError as exc:
            raise _http(400, "invalid_import_payload", str(exc)) from exc
        if plan["summary"]["error"] == len(plan["plan"]):
            raise _http(400, "import_nothing_to_apply", "所有条目都未通过校验，没有可导入的员工。")
        return _apply_plan(manager, viewer, plan)

    # ── 质量评估 ────────────────────────────────────────────
    @router.get("/{name}/quality")
    def employee_quality(name: str, viewer: Any = Depends(dependency)) -> Dict[str, Any]:
        """基于台账真实任务行给出完成率/失败率/平均耗时/人工返工次数。"""
        _require(viewer)
        manager = _manager()
        emp = _visible_or_404(manager, name, viewer)
        return _quality_for(emp.name)

    # ── 预算 ────────────────────────────────────────────────
    @router.get("/{name}/budget")
    def read_budget(name: str, viewer: Any = Depends(dependency)) -> Dict[str, Any]:
        """当前周期消耗、剩余额度、是否已超限。"""
        from ..ai.budget import BudgetError, BudgetStoreError, get_employee_budget

        _require(viewer)
        manager = _manager()
        emp = _visible_or_404(manager, name, viewer)
        try:
            return get_employee_budget(emp.name)
        except BudgetError as exc:
            raise _http(400, "invalid_budget", str(exc)) from exc
        except BudgetStoreError as exc:
            raise _http(500, "employee_budget_store_unreadable", str(exc)) from exc

    @router.put("/{name}/budget")
    def write_budget(
        name: str,
        req: BudgetUpdateRequest,
        viewer: Any = Depends(dependency),
    ) -> Dict[str, Any]:
        """设置/修改预算；超限后下一次派活直接 402。"""
        from ..ai.budget import BudgetError, BudgetStoreError, set_employee_budget

        _require(viewer)
        manager = _manager()
        emp = _writable_or_404(manager, name, viewer)
        patch = req.model_dump(exclude_unset=True)
        try:
            return set_employee_budget(emp.name, patch)
        except BudgetError as exc:
            raise _http(400, "invalid_budget", str(exc)) from exc
        except BudgetStoreError as exc:
            raise _http(500, "employee_budget_store_write_failed", str(exc)) from exc

    @router.delete("/{name}/budget")
    def clear_budget(name: str, viewer: Any = Depends(dependency)) -> Dict[str, Any]:
        """移除预算（回到不限）。"""
        from ..ai.budget import BudgetError, BudgetStoreError, delete_employee_budget

        _require(viewer)
        manager = _manager()
        emp = _writable_or_404(manager, name, viewer)
        try:
            removed = delete_employee_budget(emp.name)
        except BudgetError as exc:
            raise _http(400, "invalid_budget", str(exc)) from exc
        except BudgetStoreError as exc:
            raise _http(500, "employee_budget_store_write_failed", str(exc)) from exc
        return {"employee": emp.name, "removed": removed, "configured": False}

    return router


__all__ = ["build_router", "EXPORT_FORMAT", "EXPORT_VERSION"]
