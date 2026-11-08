"""Projects 产品面 —— 把"目标/任务"组织成真实可管理的项目容器。

为什么要有这个模块
------------------
内核审计（KERNEL-PRODUCT-CAPABILITY.md §19/§23）把 "Projects" 列为 Top-3 #1 产品缺口：
LIUHAO 能下目标、AI 员工能跑任务，但**没有任何"项目"概念把这些目标归类、归属、呈现给
人类**。一个声称"AI OS"却连"我的项目"都没有的产品，谈不上可组织、可管理、可交付。

这里补齐的正是那一层：
* 项目是人类主权创建的（挂人类令牌门禁，绝不匿名建项目），并按
  `owner_account_id` 做归属隔离：主账号可读整棵子树，成员只读/删自己的；
  越权一律中性 404（与"资源不存在或无权访问"同文案）；
* 目标在创建时可挂到某个项目（project_id），真实落盘到两处：
  - 目标的执行记录（AIStateManager._goals）写入 project_id；
  - 项目的 goal_ids 列表（ProjectStore）追加该 goal_id；
* 读取面如实返回项目下的真实目标摘要（state / 任务计数），没有目标就返回 count=0、
  goals=[]，绝不伪装成"有进展"。

诚实边界
--------
* 本模块只做"项目容器 + 目标归类"，不发明任何执行能力；目标的真实执行仍由
  src/gateway/ai_management.py 的执行内核负责。
* 项目元数据存储为受 LIUHAO_WORKSPACE_ROOT 约束的 JSON（与 _goals 同源），fail-closed。
"""

from __future__ import annotations

import copy
import json
import logging
import os
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from ..common.atomic_write import (
    atomic_write_text,
    exclusive_file_lock,
    read_text_with_retry,
)
from ..kernels.identity import _accounts
from .account_context import deny
from .policy import require_bearer_payload

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/projects", tags=["projects"])


# ── 项目存储（JSON，工作区根约束，fail-closed） ─────────────────


class ProjectNameConflict(Exception):
    """同一账号下已存在同名项目（按去空白、大小写不敏感比较）。"""


def _name_key(name: Any) -> str:
    return str(name or "").strip().casefold()


class ProjectStore:
    """项目元数据存储单例（与 AIStateManager._goals 同源、同约束）。

    线程安全，供 FastAPI 端点与执行内核调用。项目是人类主权创建、可读可删的
    轻量容器；真正的"工作"仍是目标（goal），挂在 project_id 之下。
    """

    _instance: Optional["ProjectStore"] = None
    _lock = threading.Lock()

    def __new__(cls) -> "ProjectStore":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._initialized = True
        self._projects: Dict[str, Dict[str, Any]] = {}
        self._inner_lock = threading.Lock()
        self._load()

    @staticmethod
    def _default_path() -> str:
        """默认项目库位置（与 _goals 同源，REDIR-able 以便隔离验证）。"""
        root = os.environ.get("LIUHAO_WORKSPACE_ROOT")
        if root:
            return os.path.join(root, "liuhao_projects.json")
        repo_root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        return os.path.join(repo_root, ".liuhao_projects.json")

    def _read_disk(self, path: str) -> Optional[Dict[str, Dict[str, Any]]]:
        """锁内重读磁盘；文件不存在返回 ``{}``，读不动/不是对象返回 ``None``。"""
        if not os.path.exists(path):
            return {}
        try:
            data = json.loads(read_text_with_retry(path))
        except Exception as exc:
            logger.warning("ProjectStore: load failed: %s", exc)
            return None
        if not isinstance(data, dict):
            return None
        return {
            str(pid): entry
            for pid, entry in data.items()
            if isinstance(entry, dict)
        }

    def _mutate_and_persist(self, mutate) -> Tuple[Any, bool]:
        """在进程锁 + OS 文件锁内「重读磁盘 → 改 → 原子整份写回」。

        只拿进程内锁时，两个网关进程同时建项目会各自持有旧快照，后写者把
        先写者的项目整份覆盖掉（经典 lost update）。这里用
        ``exclusive_file_lock`` 跨进程串行化，并在锁内**重新读盘合并**，
        保证每个进程都基于最新状态做增量；``atomic_write_text`` 提供唯一
        temp + fsync + 重试 replace，半截文件不会出现在读者眼里。
        """
        path = self._default_path()
        with self._inner_lock:
            state: Optional[Dict[str, Any]] = None
            outcome = None
            try:
                with exclusive_file_lock(path + ".lock", description="项目库"):
                    disk = self._read_disk(path)
                    if disk is None:
                        # 磁盘损坏时沿用内存态，绝不把"读不懂"当成"空库"清空。
                        state = copy.deepcopy(self._projects)
                    else:
                        state = {
                            pid: copy.deepcopy(entry)
                            for pid, entry in disk.items()
                        }
                    outcome = mutate(state)
                    serialized: Dict[str, Any] = {}
                    for pid, entry in state.items():
                        try:
                            json.dumps(entry, ensure_ascii=False)
                            serialized[pid] = entry
                        except (TypeError, ValueError):
                            serialized[pid] = {"__unserializable__": str(entry)}
                    atomic_write_text(
                        path, json.dumps(serialized, indent=2, ensure_ascii=False)
                    )
                    persisted = True
            except Exception as exc:
                # 落盘（含拿不到 OS 锁/目录不可建）失败绝不打断请求，但如实返回。
                logger.error("ProjectStore: persist failed (%s): %s", path, exc)
                persisted = False
                if state is None:
                    state = copy.deepcopy(self._projects)
                    outcome = mutate(state)
            self._projects = state
            return outcome, persisted

    def _load(self) -> None:
        """从持久化 JSON 重新加载（不存在则 no-op）。"""
        data = self._read_disk(self._default_path())
        if data is not None:
            self._projects = data

    def create(
        self,
        name: str,
        description: str,
        creator: Optional[str] = None,
        owner_account_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """创建一个项目（人类主权操作）。

        ``owner_account_id`` 是授权归属（缺省回退到 ``creator``，兼容老调用方）；
        读写可见性一律以它为准，绝不由客户端请求体决定。
        """
        pid = str(uuid.uuid4())[:8]
        owner = str(owner_account_id or creator or "").strip()
        created = time.time()
        project = {
            "project_id": pid,
            "name": name,
            "description": description or "",
            "created_at": created,
            "updated_at": created,
            "archived_at": None,
            "goal_ids": [],
            "creator": creator,
            "owner_account_id": owner,
        }

        def mutate(state: Dict[str, Any]) -> str:
            # 同名判定必须在锁内、基于磁盘最新状态做：两个并发进程同时建
            # 同名项目时，只有先落盘的那个成功，后写者拿到冲突而不是静默重复。
            wanted = _name_key(name)
            for existing in state.values():
                if not isinstance(existing, dict):
                    continue
                if project_owner(existing) != owner:
                    continue
                if _name_key(existing.get("name")) == wanted:
                    return "conflict"
            state[pid] = project
            return "created"

        outcome, persisted = self._mutate_and_persist(mutate)
        if outcome == "conflict":
            raise ProjectNameConflict(name)
        # 落盘结果只标注在响应副本上，不进内存态（避免 list/get 带出陈旧值）。
        result = dict(project)
        result["persisted"] = persisted
        return result

    def get(self, project_id: str) -> Optional[Dict[str, Any]]:
        with self._inner_lock:
            p = self._projects.get(project_id)
            return dict(p) if p is not None else None

    def list_projects(self) -> List[Dict[str, Any]]:
        with self._inner_lock:
            return [dict(p) for p in self._projects.values()]

    def exists(self, project_id: str) -> bool:
        with self._inner_lock:
            return project_id in self._projects

    def add_goal(self, project_id: str, goal_id: str) -> bool:
        """把一个目标挂到项目下（执行内核在创建目标时调用）。"""

        def mutate(state: Dict[str, Any]) -> bool:
            project = state.get(project_id)
            if project is None:
                return False
            goal_ids = project.setdefault("goal_ids", [])
            if goal_id not in goal_ids:
                goal_ids.append(goal_id)
                project["updated_at"] = time.time()
            return True

        ok, _persisted = self._mutate_and_persist(mutate)
        return bool(ok)

    def remove_goal(self, project_id: str, goal_id: str) -> bool:
        def mutate(state: Dict[str, Any]) -> bool:
            project = state.get(project_id)
            if project is None:
                return False
            goal_ids = project.setdefault("goal_ids", [])
            if goal_id in goal_ids:
                goal_ids.remove(goal_id)
                project["updated_at"] = time.time()
            return True

        ok, _persisted = self._mutate_and_persist(mutate)
        return bool(ok)

    def archive(self, project_id: str) -> Tuple[bool, bool]:
        """归档项目（仅隐藏于默认列表，不删任何数据）。返回 (存在, 落盘成功)。"""

        def mutate(state: Dict[str, Any]) -> bool:
            project = state.get(project_id)
            if project is None:
                return False
            now = time.time()
            project["archived_at"] = now
            project["updated_at"] = now
            return True

        return self._mutate_and_persist(mutate)

    def restore(self, project_id: str) -> Tuple[bool, bool]:
        """恢复归档项目（清空 ``archived_at``）。返回 (存在, 落盘成功)。"""

        def mutate(state: Dict[str, Any]) -> bool:
            project = state.get(project_id)
            if project is None:
                return False
            now = time.time()
            project["archived_at"] = None
            project["updated_at"] = now
            return True

        return self._mutate_and_persist(mutate)

    def delete(self, project_id: str) -> Tuple[bool, bool]:
        """删除项目。返回 (existed, persisted)：项目是否存在、落盘是否成功。"""

        def mutate(state: Dict[str, Any]) -> bool:
            if project_id not in state:
                return False
            del state[project_id]
            return True

        existed, persisted = self._mutate_and_persist(mutate)
        return bool(existed), bool(persisted)


# ── 账号归属与可见性（越权一律中性 404） ─────────────────────

def _default_account_id() -> str:
    """本机默认账号（仅用于解释没有归属字段的历史项目）。"""
    from .ai_management import default_account_id

    return default_account_id()


def project_owner(project: Optional[Dict[str, Any]]) -> str:
    """项目的归属账号：显式 owner 优先，其次创建者，最后本机默认账号。"""
    if not isinstance(project, dict):
        return _default_account_id()
    raw = str(project.get("owner_account_id") or project.get("creator") or "").strip()
    return raw or _default_account_id()


def _visibility_entry(project: Dict[str, Any]) -> Dict[str, Any]:
    """包成 GoalViewer 认识的形状（它按 owner_account_id 判定可见性）。"""
    return {"owner_account_id": project_owner(project)}


def _projects_viewer(
    x_liuhao_token: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
) -> Any:
    """FastAPI 依赖：经核验令牌 -> 账号视角（懒导入避免循环依赖）。"""
    from .ai_management import viewer_from_payload

    return viewer_from_payload(
        require_bearer_payload(x_liuhao_token, authorization)
    )


def _not_found() -> HTTPException:
    """统一中性 404：不存在与越权在响应上完全不可区分。"""
    return deny(404, _accounts.CODE_SUBTREE_DENIED, _accounts.NEUTRAL_NOT_FOUND_MESSAGE)


def account_sees_project(project: Dict[str, Any], account: str) -> bool:
    """账号是否可见该项目（自己 + 主账号的整棵子树；老数据归本机默认账号）。"""
    owner = project_owner(project)
    if owner == account:
        return True
    try:
        from src.kernels.identity import get_identity_manager

        manager = get_identity_manager()
        node = manager.get_account(account)
    except Exception:  # 账号内核不可用：只收窄到"自己"
        return owner == _default_account_id()
    if node is None:  # 老部署/单机令牌：历史项目归本机默认账号
        return owner == _default_account_id()
    if owner in tuple(manager.subtree_principals(account)):
        return True
    return False


def exists_for(project_id: str, account: str) -> bool:
    """挂目标前的归属校验：项目必须在该账号的可见范围内。"""
    project = ProjectStore().get(project_id)
    return project is not None and account_sees_project(project, account)


# ── 目标↔项目 关联（读取真实目标，不发明） ─────────────────────

def _goals_for_project(
    project_id: str, viewer: Any = None
) -> List[Dict[str, Any]]:
    """返回挂在某项目下的真实目标条目（热集合 + 已归档分片）。

    项目的 ``goal_ids`` 是"曾经挂上来过"的事实；但目标进入终态后会被移出内存
    热集合、写入归档分片（``AIStateManager`` 的 bounded hot set）。只读 ``_goals``
    会让"项目里的产出"在目标归档后凭空消失 —— 这里把归档索引一并合并，保证
    项目视图始终反映真实历史。
    """
    try:
        from .ai_management import AIStateManager

        mgr = AIStateManager()
    except Exception as exc:
        logger.warning("projects: cannot read goals for %s: %s", project_id, exc)
        return []
    items: List[Dict[str, Any]] = []
    with mgr._goal_lock:
        for entry in mgr._goals.values():
            if isinstance(entry, dict) and entry.get("project_id") == project_id:
                items.append(dict(entry))
    active_ids = {item.get("goal_id") for item in items}
    try:
        archived = mgr._load_archived_summaries()
    except Exception as exc:  # 归档索引读不动时如实退化，不丢热集合
        logger.warning("projects: archived goals unavailable: %s", exc)
        archived = {}
    for goal_id, summary in archived.items():
        if goal_id in active_ids or not isinstance(summary, dict):
            continue
        if summary.get("project_id") != project_id:
            continue
        entry = dict(summary)
        if "artifacts" not in entry:
            # 老归档索引没有产物字段：按需回查完整条目（只有老数据才会走到）。
            try:
                full = mgr._find_archived_goal(goal_id) or {}
            except Exception:
                full = {}
            entry["artifacts"] = full.get("artifacts") or []
            entry["artifact_owners"] = full.get("artifact_owners") or {}
            entry["updated_at"] = full.get("updated_at")
        items.append(entry)

    if viewer is not None:
        # 项目详情里的目标摘要同样按账号可见性过滤：分享一个项目容器，
        # 不等于把别人挂在里面的目标内容一起公开。
        items = [item for item in items if viewer.sees(item)]
    summaries = []
    for item in items:
        tasks = item.get("tasks", []) or []
        # ARM1 decision (A): a ``completed_unattributed`` task DID complete -- only
        # its agent attribution could not be established. This reads the same
        # ``AIStateManager._goals`` task list as ``ai_management.list_goals``, so
        # the two counters must agree; counting it as anything other than
        # completed would under-report real finished work here while the goals
        # endpoint reports it.
        completed = sum(
            1 for t in tasks
            if t.get("status") in ("completed", "completed_unattributed")
        )
        failed = sum(1 for t in tasks if t.get("status") == "failed")
        summaries.append({
            "goal_id": item["goal_id"],
            "state": item["state"],
            "natural_language": item.get("natural_language", ""),
            "created_at": item.get("created_at"),
            "updated_at": item.get("updated_at"),
            "task_count": len(tasks),
            "completed_tasks": completed,
            "failed_tasks": failed,
            "owner_account_id": project_owner(item),
            "artifacts": [
                path for path in (item.get("artifacts") or [])
                if isinstance(path, str) and path.strip()
            ],
            "artifact_owners": (
                item.get("artifact_owners")
                if isinstance(item.get("artifact_owners"), dict) else {}
            ),
        })
    return summaries


def _outputs_for_goals(goals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把项目下目标真实写出的产物路径摊平成 ``{path, owner, goal_id}`` 列表。"""
    outputs: List[Dict[str, Any]] = []
    seen: set = set()
    for goal in goals:
        owners = goal.get("artifact_owners")
        if not isinstance(owners, dict):
            owners = {}
        for raw in goal.get("artifacts") or []:
            if not isinstance(raw, str):
                continue
            path = raw.replace("\\", "/").strip().strip("/")
            if not path or path in seen:
                continue
            seen.add(path)
            outputs.append({
                "path": path,
                "owner": str(owners.get(path) or project_owner(goal)).strip()
                or project_owner(goal),
                "goal_id": goal.get("goal_id"),
            })
    return outputs


def _last_activity_at(project: Dict[str, Any], goals: List[Dict[str, Any]]) -> Optional[float]:
    """项目最近一次活动：项目自身更新时间 ∪ 其下目标的创建/更新时间。"""
    stamps: List[float] = []
    for raw in (project.get("updated_at"), project.get("created_at")):
        try:
            if raw is not None:
                stamps.append(float(raw))
        except (TypeError, ValueError):
            continue
    for goal in goals:
        for raw in (goal.get("updated_at"), goal.get("created_at")):
            try:
                if raw is not None:
                    stamps.append(float(raw))
            except (TypeError, ValueError):
                continue
    return max(stamps) if stamps else None


# ── 请求/响应模型 ──────────────────────────────────────────────

MAX_PROJECT_NAME = 200
MAX_PROJECT_DESCRIPTION = 2000


class ProjectCreateRequest(BaseModel):
    """创建项目的请求体（中文校验文案，超长与空名都不会变成 500）。"""
    name: str = Field(..., description="项目名称")
    description: str = Field("", description="项目描述")

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        cleaned = str(value or "").strip()
        if not cleaned:
            raise ValueError("项目名称不能为空")
        if len(cleaned) > MAX_PROJECT_NAME:
            raise ValueError(f"项目名称最多 {MAX_PROJECT_NAME} 个字")
        return cleaned

    @field_validator("description")
    @classmethod
    def _validate_description(cls, value: str) -> str:
        cleaned = str(value or "")
        if len(cleaned) > MAX_PROJECT_DESCRIPTION:
            raise ValueError(f"项目描述最多 {MAX_PROJECT_DESCRIPTION} 个字")
        return cleaned


# ── REST 端点 ─────────────────────────────────────────────────

@router.post("")
def create_project(
    req: ProjectCreateRequest,
    viewer: Any = Depends(_projects_viewer),
) -> Dict[str, Any]:
    """人类主权创建一个项目（容器），归属固定为令牌主体。"""
    store = ProjectStore()
    try:
        project = store.create(
            req.name,
            req.description,
            creator=viewer.principal,
            owner_account_id=viewer.principal,
        )
    except ProjectNameConflict:
        raise deny(
            409,
            "project_name_taken",
            "同一账号下已有同名项目。请换一个名称，或先归档/删除旧项目。",
        )
    logger.info(
        "Project created by %s: %s (%s)",
        viewer.principal,
        project["project_id"],
        req.name,
    )
    return project


#: 列表里最多预览多少条产出路径（完整列表在项目详情里）。
OUTPUT_PREVIEW_LIMIT = 20


def _enrich_summary(
    project: Dict[str, Any], viewer: Any, preview: bool
) -> Dict[str, Any]:
    """给项目补上"可用性三件套"：关联目标数、最近活动、产出在哪里。"""
    enriched = dict(project)
    goals = _goals_for_project(enriched["project_id"], viewer)
    outputs = _outputs_for_goals(goals)
    enriched["goal_count"] = len(goals)
    enriched["last_activity_at"] = _last_activity_at(enriched, goals)
    enriched["output_count"] = len(outputs)
    if preview:
        enriched["outputs"] = outputs[:OUTPUT_PREVIEW_LIMIT]
        enriched["outputs_truncated"] = len(outputs) > OUTPUT_PREVIEW_LIMIT
    else:
        enriched["outputs"] = outputs
        enriched["outputs_truncated"] = False
    return enriched


@router.get("")
def list_projects(
    include_archived: bool = Query(False, description="是否包含已归档项目"),
    view_account: Optional[str] = Query(
        None, description="主账号可按某个成员账号的视角过滤（成员只能看自己）"
    ),
    viewer: Any = Depends(_projects_viewer),
) -> Dict[str, Any]:
    """列出**当前账号可见**的项目：主账号看整棵子树，成员只看自己。"""
    store = ProjectStore()
    target = (view_account or "").strip() or None
    if target is not None:
        # 视角切换只允许在可见范围内发生；越权与"账号不存在"同为中性 404。
        allowed = target == viewer.principal or (
            viewer.is_root and target in viewer.subtree
        )
        if not allowed:
            raise _not_found()

    visible = [
        p for p in store.list_projects() if viewer.sees(_visibility_entry(p))
    ]
    if target is not None:
        visible = [p for p in visible if project_owner(p) == target]
    if not include_archived:
        visible = [p for p in visible if not p.get("archived_at")]

    enriched = [_enrich_summary(p, viewer, preview=True) for p in visible]
    # 最近有活动的排前面；没有活动时间的排最后（不发明顺序）。
    enriched.sort(
        key=lambda p: p.get("last_activity_at") or p.get("created_at") or 0,
        reverse=True,
    )
    for item in enriched:
        item["view_account"] = target
    return {"projects": enriched, "count": len(enriched), "view_account": target}


@router.get("/{project_id}")
def get_project(
    project_id: str,
    viewer: Any = Depends(_projects_viewer),
) -> Dict[str, Any]:
    """获取项目详情，并附带其下的真实目标与产出（越权与不存在同为中性 404）。"""
    store = ProjectStore()
    project = store.get(project_id)
    if project is None or not viewer.sees(_visibility_entry(project)):
        raise _not_found()
    project = _enrich_summary(project, viewer, preview=False)
    project["goals"] = _goals_for_project(project_id, viewer)
    project["goal_count"] = len(project["goals"])
    return project


@router.post("/{project_id}/archive")
def archive_project(
    project_id: str,
    viewer: Any = Depends(_projects_viewer),
) -> Dict[str, Any]:
    """归档项目：只是从默认列表隐藏，不删目标、不删产出；可随时恢复。"""
    store = ProjectStore()
    project = store.get(project_id)
    if project is None or not viewer.owns(_visibility_entry(project)):
        raise _not_found()
    existed, persisted = store.archive(project_id)
    if not existed:
        raise _not_found()
    updated = store.get(project_id) or {}
    return {
        "project_id": project_id,
        "archived_at": updated.get("archived_at"),
        "ok": True,
        "persisted": persisted,
    }


@router.post("/{project_id}/restore")
def restore_project(
    project_id: str,
    viewer: Any = Depends(_projects_viewer),
) -> Dict[str, Any]:
    """恢复归档项目（写操作同样只作用于自己的项目）。"""
    store = ProjectStore()
    project = store.get(project_id)
    if project is None or not viewer.owns(_visibility_entry(project)):
        raise _not_found()
    existed, persisted = store.restore(project_id)
    if not existed:
        raise _not_found()
    return {"project_id": project_id, "archived_at": None, "ok": True, "persisted": persisted}


@router.delete("/{project_id}")
def delete_project(
    project_id: str,
    force: bool = Query(False, description="目标仍关联时仍强制删除（需先看过影响面）"),
    viewer: Any = Depends(_projects_viewer),
) -> Dict[str, Any]:
    """删除一个项目，并一致地清理其下目标的 project_id 归属。

    写操作只作用于**自己的**项目（主账号也不例外，避免误删子树项目）；
    越权与不存在同为中性 404。

    有目标关联时**先拒绝**（409，附影响面），只有调用方明确 ``force=true``
    才继续：删除会解除目标与项目的归属，但绝不删除目标本身。这样"删除"
    不会悄无声息地把一个装满历史项目的容器清空。
    """
    store = ProjectStore()
    project = store.get(project_id)
    if project is None or not viewer.owns(_visibility_entry(project)):
        raise _not_found()
    goal_ids = list(project.get("goal_ids", []) or [])
    linked = _goals_for_project(project_id, viewer)
    if linked and not force:
        sample = [
            str(goal.get("natural_language") or "")[:120]
            for goal in linked[:5]
        ]
        raise deny(
            409,
            "project_has_goals",
            (
                f"该项目下还有 {len(linked)} 个目标。删除项目只会解除归属，"
                "目标本身与其产出不会被删除；删除后这些目标将不再属于任何项目。"
                "可先归档保留记录，或确认影响面后强制删除。"
            ),
            goal_count=len(linked),
            goals=sample,
        )
    if goal_ids:
        try:
            from .ai_management import AIStateManager

            mgr = AIStateManager()
            with mgr._goal_lock:
                for gid in goal_ids:
                    entry = mgr._goals.get(gid)
                    if entry is not None and entry.get("project_id") == project_id:
                        entry = dict(entry)
                        entry["project_id"] = None
                        mgr._goals[gid] = entry
            mgr._save_goals()
        except Exception as exc:  # cleanup must never break the delete
            logger.warning("delete_project: goal cleanup failed: %s", exc)
    _, persisted = store.delete(project_id)
    logger.info("Project deleted by %s: %s", viewer.principal, project_id)
    return {
        "removed": project_id,
        "ok": True,
        "persisted": persisted,
        "goal_count": len(linked),
    }
