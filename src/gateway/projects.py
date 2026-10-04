"""Projects 产品面 —— 把"目标/任务"组织成真实可管理的项目容器。

为什么要有这个模块
------------------
内核审计（KERNEL-PRODUCT-CAPABILITY.md §19/§23）把 "Projects" 列为 Top-3 #1 产品缺口：
LIUHAO 能下目标、AI 员工能跑任务，但**没有任何"项目"概念把这些目标归类、归属、呈现给
人类**。一个声称"AI OS"却连"我的项目"都没有的产品，谈不上可组织、可管理、可交付。

这里补齐的正是那一层：
* 项目是人类主权创建的（挂 require_human_principal，绝不匿名建项目）；
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

import json
import logging
import os
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .policy import require_human_principal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/projects", tags=["projects"])


# ── 项目存储（JSON，工作区根约束，fail-closed） ─────────────────

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

    def _save(self) -> None:
        """原子写入项目库；持久化失败绝不打断请求。"""
        path = self._default_path()
        try:
            directory = os.path.dirname(os.path.abspath(path))
            if directory:
                os.makedirs(directory, exist_ok=True)
            serialized: Dict[str, Any] = {}
            for pid, p in self._projects.items():
                try:
                    json.dumps(p)
                    serialized[pid] = p
                except (TypeError, ValueError):
                    serialized[pid] = {"__unserializable__": str(p)}
            tmp = f"{path}.tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(serialized, fh, indent=2)
            os.replace(tmp, path)
        except Exception as exc:  # persistence must never break a request
            logger.warning("ProjectStore: persist failed: %s", exc)

    def _load(self) -> None:
        """从持久化 JSON 重新加载（不存在则 no-op）。"""
        path = self._default_path()
        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                self._projects = data
        except Exception as exc:
            logger.warning("ProjectStore: load failed: %s", exc)

    def create(
        self, name: str, description: str, creator: Optional[str] = None
    ) -> Dict[str, Any]:
        """创建一个项目（人类主权操作）。"""
        pid = str(uuid.uuid4())[:8]
        project = {
            "project_id": pid,
            "name": name,
            "description": description or "",
            "created_at": time.time(),
            "goal_ids": [],
            "creator": creator,
        }
        with self._inner_lock:
            self._projects[pid] = project
        self._save()
        return project

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
        with self._inner_lock:
            project = self._projects.get(project_id)
            if project is None:
                return False
            if goal_id not in project["goal_ids"]:
                project["goal_ids"].append(goal_id)
        self._save()
        return True

    def remove_goal(self, project_id: str, goal_id: str) -> bool:
        with self._inner_lock:
            project = self._projects.get(project_id)
            if project is None:
                return False
            if goal_id in project["goal_ids"]:
                project["goal_ids"].remove(goal_id)
        self._save()
        return True

    def delete(self, project_id: str) -> bool:
        with self._inner_lock:
            if project_id not in self._projects:
                return False
            del self._projects[project_id]
        self._save()
        return True


# ── 目标↔项目 关联（读取真实目标，不发明） ─────────────────────

def _goals_for_project(project_id: str) -> List[Dict[str, Any]]:
    """返回挂在某项目下的真实目标摘要，直接读 AIStateManager 的已执行目标。"""
    try:
        from .ai_management import AIStateManager

        mgr = AIStateManager()
    except Exception as exc:
        logger.warning("projects: cannot read goals for %s: %s", project_id, exc)
        return []
    with mgr._goal_lock:
        items = [v for v in mgr._goals.values() if v.get("project_id") == project_id]
    summaries = []
    for item in items:
        tasks = item.get("tasks", []) or []
        completed = sum(1 for t in tasks if t.get("status") == "completed")
        failed = sum(1 for t in tasks if t.get("status") == "failed")
        summaries.append({
            "goal_id": item["goal_id"],
            "state": item["state"],
            "natural_language": item.get("natural_language", ""),
            "created_at": item.get("created_at"),
            "task_count": len(tasks),
            "completed_tasks": completed,
            "failed_tasks": failed,
        })
    return summaries


# ── 请求/响应模型 ──────────────────────────────────────────────

class ProjectCreateRequest(BaseModel):
    """创建项目的请求体。"""
    name: str = Field(..., min_length=1, max_length=200, description="项目名称")
    description: str = Field("", max_length=2000, description="项目描述")


# ── REST 端点 ─────────────────────────────────────────────────

@router.post("")
def create_project(
    req: ProjectCreateRequest,
    principal: str = Depends(require_human_principal),
) -> Dict[str, Any]:
    """人类主权创建一个项目（容器）。"""
    store = ProjectStore()
    project = store.create(req.name, req.description, creator=principal)
    logger.info(
        "Project created by %s: %s (%s)", principal, project["project_id"], req.name
    )
    return project


@router.get("")
def list_projects(
    principal: str = Depends(require_human_principal),
) -> Dict[str, Any]:
    """列出所有项目（含真实 goal_count）。"""
    store = ProjectStore()
    projects = store.list_projects()
    enriched = []
    for p in projects:
        p = dict(p)
        p["goal_count"] = len(p.get("goal_ids", []))
        enriched.append(p)
    return {"projects": enriched, "count": len(enriched)}


@router.get("/{project_id}")
def get_project(
    project_id: str,
    principal: str = Depends(require_human_principal),
) -> Dict[str, Any]:
    """获取项目详情，并附带其下的真实目标摘要。"""
    store = ProjectStore()
    project = store.get(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"project {project_id} not found")
    project = dict(project)
    goals = _goals_for_project(project_id)
    project["goals"] = goals
    project["goal_count"] = len(goals)
    return project


@router.delete("/{project_id}")
def delete_project(
    project_id: str,
    principal: str = Depends(require_human_principal),
) -> Dict[str, Any]:
    """删除一个项目，并一致地清理其下目标的 project_id 归属。

    人类主权操作：删除前不必清空目标（避免"删不掉"的尴尬），但删除时会把每个
    关联目标上的 project_id 清回 None，保证目标不被悬空引用。
    """
    store = ProjectStore()
    project = store.get(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"project {project_id} not found")
    goal_ids = project.get("goal_ids", [])
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
    store.delete(project_id)
    logger.info("Project deleted by %s: %s", principal, project_id)
    return {"removed": project_id, "ok": True}
