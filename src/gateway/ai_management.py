"""AI Employee / Goal / Workflow 管理 REST 端点。

提供真实可操作的 Goal 创建+执行、AI Employee 状态查看与生命周期控制。

路由前缀 ``/v1``，与 dashboard / chat / roster 等同层。
所有端点走 ``require_human_principal`` 鉴权闸门（在 main.py 挂载时声明）。

设计原则：
- **NO-FAKE**：所有状态来自真实的 AgentRuntime / Employee 实例，不造数据；
- **幂等**：GET 无副作用，POST 创建用 UUID 去重；
- **诚实失败**：异常不吞，返回 503 + 真实错误信息。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["ai-management"])


# ─── 请求/响应模型 ───────────────────────────────────────────

class GoalCreateRequest(BaseModel):
    """创建+执行 Goal 的请求体。"""
    natural_language: str = Field(..., min_length=1, max_length=2000,
                                 description="自然语言目标描述")
    scope: str = Field("L1", description="执行范围 L0-L7")
    plan_mode: str = Field("auto", description="计划模式: auto/manual")
    verification_criteria: Optional[Dict[str, Any]] = Field(
        None, description="可选验证标准"
    )


class GoalSummary(BaseModel):
    """Goal 列表摘要。"""
    goal_id: str
    state: str
    natural_language: str
    scope: str
    created_at: Optional[str] = None
    error: Optional[str] = None
    replan_suggested: bool = False
    task_count: int = 0
    completed_tasks: int = 0
    failed_tasks: int = 0


class GoalDetail(BaseModel):
    """Goal 详情。"""
    goal_id: str
    state: str
    natural_language: str
    scope: str
    correlation_id: str
    error: Optional[str] = None
    replan_count: int = 0
    replan_suggested: bool = False
    trace: List[Dict[str, Any]] = Field(default_factory=list)
    tasks: List[Dict[str, Any]] = Field(default_factory=list)
    evaluation: Optional[Dict[str, Any]] = None


class AgentSummary(BaseModel):
    """AI Employee 摘要。"""
    id: str
    agent_type: str
    name: str
    status: str
    current_task: Optional[str] = None
    completed_tasks: int = 0
    failed_tasks: int = 0
    total_latency_ms: float = 0.0


class AgentDetail(BaseModel):
    """AI Employee 详情。"""
    id: str
    agent_type: str
    name: str
    status: str
    current_task: Optional[str] = None
    completed_tasks: int = 0
    failed_tasks: int = 0
    total_latency_ms: float = 0.0
    metadata: Dict[str, Any] = Field(default_factory=dict)
    checkpoint_state: Optional[Dict[str, Any]] = None


# ─── 状态管理器（单例） ──────────────────────────────────────

class AIStateManager:
    """Goal / Employee 状态管理单例。

    线程安全，供 FastAPI 端点调用。
    - ``_runtime`` 是真实的 AgentRuntime，调用 ``run_goal`` 走完整链；
    - ``_employee`` 是真实的 Employee 实例，agent pool 可查可操作；
    - ``_goals`` 存储已执行的 AgentRunResult，供 GET 查询。
    """

    _instance: Optional["AIStateManager"] = None
    _lock = threading.Lock()

    def __new__(cls) -> "AIStateManager":
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
        self._goals: Dict[str, Dict[str, Any]] = {}
        self._runtime = None
        self._employee = None
        self._employee_store = None
        self._goal_lock = threading.Lock()
        # Recover any goals persisted across a previous process restart.
        self._load_goals()

    def _ensure_runtime(self):
        """懒初始化 AgentRuntime（延迟到首次使用，避免 import 时副作用）。"""
        if self._runtime is None:
            try:
                from src.ai.agent_runtime import AgentRuntime
                from src.ai.lcore import LCore
                # Wire the REAL local-tool executor (RestrictedPython compute +
                # workspace-contained file writes) so goals actually execute
                # instead of taking the simulated path. Fail-closed: with no
                # executor the Execution Kernel refuses (never reports success
                # for work it did not do).
                _lcore = LCore(scope="L1", register_local_tools=True)
                self._runtime = AgentRuntime(
                    scope="L1",
                    capability_executor=_lcore.capability_executor(),
                )
                logger.info("AIStateManager: AgentRuntime initialized (real executor wired)")
            except Exception as exc:
                logger.error("AIStateManager: failed to init AgentRuntime: %s", exc)
                raise
        return self._runtime

    # ── Employee persistence (restart recovery) ──

    def _ensure_employee(self):
        """懒初始化 Employee（真实 agent pool）。

        首次运行（store 为空）按默认种子配置创建一个 Employee 并落盘；后续运行
        从 EmployeeStore 加载已持久化的 Employee，使 agent/task 状态在进程重启后
        仍可恢复（此前全部为内存态，重启即丢）。
        """
        if self._employee is None:
            try:
                from src.ai.employee_store import EmployeeStore
                self._employee_store = EmployeeStore()
                self._employee = self._employee_store.create_employee(
                    name="liuhao-default",
                    agent_count=3,
                    agent_types=["planner", "executor", "critic"],
                )
                logger.info("AIStateManager: Employee ready with %d agents (store-backed)",
                            len(self._employee.agents))
            except Exception as exc:
                logger.error("AIStateManager: failed to init Employee: %s", exc)
                raise
        return self._employee

    def _persist_employee(self) -> None:
        """Best-effort persist of the current Employee (agent/task state)."""
        if self._employee_store is not None and self._employee is not None:
            try:
                self._employee_store.save(self._employee)
            except Exception as exc:  # persistence must never break a request
                logger.warning("AIStateManager: employee persist failed: %s", exc)

    # ── Goal persistence (restart recovery) ──

    @staticmethod
    def _default_goals_path() -> str:
        """Default location of the persisted goals dict.

        ``LIUHAO_WORKSPACE_ROOT/liuhao_goals.json`` when set (REDIR-able for
        isolation verifiers), else ``<repo_root>/.liuhao_goals.json``.
        """
        root = os.environ.get("LIUHAO_WORKSPACE_ROOT")
        if root:
            return os.path.join(root, "liuhao_goals.json")
        repo_root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        )
        return os.path.join(repo_root, ".liuhao_goals.json")

    def _save_goals(self) -> None:
        """Persist ``_goals`` to JSON so executed-goal history survives restart."""
        path = self._default_goals_path()
        try:
            directory = os.path.dirname(os.path.abspath(path))
            if directory:
                os.makedirs(directory, exist_ok=True)
            serialized: Dict[str, Any] = {}
            for gid, entry in self._goals.items():
                try:
                    json.dumps(entry)
                    serialized[gid] = entry
                except (TypeError, ValueError):
                    serialized[gid] = {"__unserializable__": str(entry)}
            tmp = f"{path}.tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(serialized, fh, indent=2)
            os.replace(tmp, path)
        except Exception as exc:  # persistence must never break a request
            logger.warning("AIStateManager: goals persist failed: %s", exc)

    def _load_goals(self) -> None:
        """Reload ``_goals`` from the persisted JSON (no-op if absent)."""
        path = self._default_goals_path()
        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                self._goals = data
        except Exception as exc:
            logger.warning("AIStateManager: goals load failed: %s", exc)

    # ── Goal 操作 ──

    def create_and_execute_goal(
        self,
        natural_language: str,
        scope: str = "L1",
        plan_mode: str = "auto",
        verification_criteria: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """创建并执行一个 Goal，返回执行结果。"""
        runtime = self._ensure_runtime()
        goal_id = str(uuid.uuid4())[:8]

        try:
            result = runtime.run_goal(
                goal_text=natural_language,
                goal_id=goal_id,
                scope=scope,
                plan_mode=plan_mode,
                verification_criteria=verification_criteria,
                persist=True,
            )
        except Exception as exc:
            logger.error("create_and_execute_goal failed: %s", exc, exc_info=True)
            return {
                "goal_id": goal_id,
                "state": "failed",
                "error": str(exc),
                "natural_language": natural_language,
                "scope": scope,
                "correlation_id": "",
                "replan_count": 0,
                "replan_suggested": False,
                "trace": [],
                "tasks": [],
                "evaluation": None,
                "created_at": time.time(),
            }

        entry = self._result_to_dict(result, natural_language, scope)
        with self._goal_lock:
            self._goals[goal_id] = entry
        self._save_goals()
        return entry

    def replan_goal(self, goal_id: str) -> Dict[str, Any]:
        """对已失败的 Goal 执行 replan。"""
        with self._goal_lock:
            prior_entry = self._goals.get(goal_id)
        if prior_entry is None:
            raise ValueError(f"Goal {goal_id} not found")

        runtime = self._ensure_runtime()
        # 从存储的 dict 重建 AgentRunResult 的关键字段
        from src.ai.agent_runtime import AgentRunResult, AgentRunState, ExecutionTrace
        prior = AgentRunResult(
            goal_id=goal_id,
            correlation_id=prior_entry.get("correlation_id", ""),
            state=AgentRunState(prior_entry.get("state", "failed")),
            context=None,
            evaluation=None,
            trace=ExecutionTrace(),
            error=prior_entry.get("error"),
        )
        result = runtime.replan(prior)
        entry = self._result_to_dict(result, prior_entry["natural_language"],
                                     prior_entry["scope"])
        entry["replan_count"] = prior_entry.get("replan_count", 0) + 1
        with self._goal_lock:
            self._goals[goal_id] = entry
        self._save_goals()
        return entry

    def list_goals(self) -> List[Dict[str, Any]]:
        """列出所有已执行 Goal 摘要。"""
        with self._goal_lock:
            items = list(self._goals.values())
        summaries = []
        for item in items:
            tasks = item.get("tasks", [])
            completed = sum(1 for t in tasks if t.get("status") == "completed")
            failed = sum(1 for t in tasks if t.get("status") == "failed")
            summaries.append({
                "goal_id": item["goal_id"],
                "state": item["state"],
                "natural_language": item.get("natural_language", ""),
                "scope": item.get("scope", ""),
                "created_at": item.get("created_at"),
                "error": item.get("error"),
                "replan_suggested": item.get("replan_suggested", False),
                "task_count": len(tasks),
                "completed_tasks": completed,
                "failed_tasks": failed,
            })
        return summaries

    def get_goal(self, goal_id: str) -> Dict[str, Any]:
        """获取 Goal 详情。"""
        with self._goal_lock:
            item = self._goals.get(goal_id)
        if item is None:
            raise ValueError(f"Goal {goal_id} not found")
        return item

    # ── Employee / Agent 操作 ──

    def list_agents(self) -> List[Dict[str, Any]]:
        """列出所有 AI Employee（agent pool）。"""
        emp = self._ensure_employee()
        result = []
        for agent_id, agent in emp.agents.items():
            result.append({
                "id": agent.id,
                "agent_type": agent.agent_type,
                "name": agent.name,
                "status": agent.status.value,
                "current_task": agent.current_task,
                "completed_tasks": agent.completed_tasks,
                "failed_tasks": agent.failed_tasks,
                "total_latency_ms": agent.total_latency_ms,
            })
        return result

    def get_agent(self, agent_id: str) -> Dict[str, Any]:
        """获取单个 agent 详情。"""
        emp = self._ensure_employee()
        agent = emp.agents.get(agent_id)
        if agent is None:
            raise ValueError(f"Agent {agent_id} not found")
        return {
            "id": agent.id,
            "agent_type": agent.agent_type,
            "name": agent.name,
            "status": agent.status.value,
            "current_task": agent.current_task,
            "completed_tasks": agent.completed_tasks,
            "failed_tasks": agent.failed_tasks,
            "total_latency_ms": agent.total_latency_ms,
            "metadata": dict(agent.metadata),
            "checkpoint_state": agent.checkpoint_state,
        }

    def pause_agent(self, agent_id: str) -> Dict[str, Any]:
        """暂停一个 agent。"""
        emp = self._ensure_employee()
        agent = emp.agents.get(agent_id)
        if agent is None:
            raise ValueError(f"Agent {agent_id} not found")
        ok = agent.pause()
        self._persist_employee()
        return {"agent_id": agent_id, "paused": ok, "status": agent.status.value}

    def resume_agent(self, agent_id: str) -> Dict[str, Any]:
        """恢复一个暂停的 agent。"""
        emp = self._ensure_employee()
        agent = emp.agents.get(agent_id)
        if agent is None:
            raise ValueError(f"Agent {agent_id} not found")
        ok = agent.resume()
        self._persist_employee()
        return {"agent_id": agent_id, "resumed": ok, "status": agent.status.value}

    def get_employee_stats(self) -> Dict[str, Any]:
        """获取 Employee 整体统计。"""
        emp = self._ensure_employee()
        return {
            "name": emp.name,
            "agent_count": len(emp.agents),
            "total_tasks_submitted": emp.total_tasks_submitted,
            "total_tasks_completed": emp.total_tasks_completed,
            "total_tasks_failed": emp.total_tasks_failed,
            "task_queue_length": len(emp.task_queue),
        }

    # ── 内部工具 ──

    @staticmethod
    def _result_to_dict(result, natural_language: str, scope: str) -> Dict[str, Any]:
        """把 AgentRunResult 转为可序列化的 dict。"""
        # 提取 trace
        trace_entries = []
        if result.trace:
            for entry in result.trace.entries if hasattr(result.trace, 'entries') else []:
                trace_entries.append({
                    "task_id": entry.task_id,
                    "event_type": entry.event_type,
                    "ts": str(entry.ts) if entry.ts else None,
                    "capability": entry.capability,
                    "decision": entry.decision,
                    "output": entry.output if hasattr(entry, 'output') else None,
                    "error": entry.error if hasattr(entry, 'error') else None,
                    "correlation_id": entry.correlation_id,
                })

        # 提取 context 中的 task 信息
        tasks = []
        if result.context:
            ctx = result.context
            if hasattr(ctx, 'tasks') and ctx.tasks:
                for t in ctx.tasks:
                    tasks.append({
                        "id": t.id,
                        "name": t.name,
                        "description": t.description,
                        "status": t.status.value if hasattr(t.status, 'value') else str(t.status),
                        "capability_id": t.capability_id,
                        "assigned_agent": t.assigned_agent,
                        "result": str(t.result)[:500] if t.result else None,
                        "error": t.error,
                        "started_at": str(t.started_at) if t.started_at else None,
                        "completed_at": str(t.completed_at) if t.completed_at else None,
                    })

        # 提取 evaluation
        evaluation = None
        if result.evaluation:
            ev = result.evaluation
            evaluation = {
                "outcome": str(ev.outcome) if hasattr(ev, 'outcome') else None,
                "replan_required": getattr(ev, 'replan_required', False),
                "replan_triggered": getattr(ev, 'replan_triggered', False),
                "summary": getattr(ev, 'summary', ''),
            }

        return {
            "goal_id": result.goal_id,
            "state": result.state.value if hasattr(result.state, 'value') else str(result.state),
            "natural_language": natural_language,
            "scope": scope,
            "correlation_id": result.correlation_id,
            "error": result.error,
            "replan_count": result.replan_count,
            "replan_suggested": result.replan_suggested,
            "trace": trace_entries,
            "tasks": tasks,
            "evaluation": evaluation,
            "created_at": time.time(),
        }


# ─── REST 端点 ───────────────────────────────────────────────

@router.get("/goals")
def list_goals() -> Dict[str, Any]:
    """列出所有已执行的 Goal 及其状态。"""
    mgr = AIStateManager()
    return {"goals": mgr.list_goals(), "count": len(mgr._goals)}


@router.post("/goals")
def create_goal(req: GoalCreateRequest) -> Dict[str, Any]:
    """创建并执行一个新 Goal。

    调用 AgentRuntime.run_goal，走完整链：
    plan → execute → evaluate → persist。
    """
    mgr = AIStateManager()
    try:
        return mgr.create_and_execute_goal(
            natural_language=req.natural_language,
            scope=req.scope,
            plan_mode=req.plan_mode,
            verification_criteria=req.verification_criteria,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Goal execution failed: {exc}")


@router.get("/goals/{goal_id}")
def get_goal(goal_id: str) -> Dict[str, Any]:
    """获取 Goal 执行详情。"""
    mgr = AIStateManager()
    try:
        return mgr.get_goal(goal_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/goals/{goal_id}/replan")
def replan_goal(goal_id: str) -> Dict[str, Any]:
    """对失败的 Goal 执行 replan。"""
    mgr = AIStateManager()
    try:
        return mgr.replan_goal(goal_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Replan failed: {exc}")


@router.get("/employees")
def list_employees() -> Dict[str, Any]:
    """列出所有 AI Employee（agent pool 真实状态）。"""
    mgr = AIStateManager()
    try:
        agents = mgr.list_agents()
        stats = mgr.get_employee_stats()
        return {"agents": agents, "count": len(agents), "stats": stats}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Employee listing failed: {exc}")


@router.get("/employees/{agent_id}")
def get_employee(agent_id: str) -> Dict[str, Any]:
    """获取单个 AI Employee 详情。"""
    mgr = AIStateManager()
    try:
        return mgr.get_agent(agent_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/employees/{agent_id}/pause")
def pause_employee(agent_id: str) -> Dict[str, Any]:
    """暂停一个 AI Employee。"""
    mgr = AIStateManager()
    try:
        return mgr.pause_agent(agent_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/employees/{agent_id}/resume")
def resume_employee(agent_id: str) -> Dict[str, Any]:
    """恢复一个暂停的 AI Employee。"""
    mgr = AIStateManager()
    try:
        return mgr.resume_agent(agent_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/workflows")
def list_workflows() -> Dict[str, Any]:
    """列出所有 Workflow 执行状态（以 Goal 为维度）。"""
    mgr = AIStateManager()
    goals = mgr.list_goals()
    workflows = []
    for g in goals:
        workflows.append({
            "goal_id": g["goal_id"],
            "state": g["state"],
            "task_count": g["task_count"],
            "completed_tasks": g["completed_tasks"],
            "failed_tasks": g["failed_tasks"],
        })
    return {"workflows": workflows, "count": len(workflows)}


@router.get("/workflows/{goal_id}")
def get_workflow(goal_id: str) -> Dict[str, Any]:
    """获取某个 Goal 的 Workflow 执行状态。"""
    mgr = AIStateManager()
    try:
        goal = mgr.get_goal(goal_id)
        return {
            "goal_id": goal_id,
            "state": goal["state"],
            "tasks": goal.get("tasks", []),
            "trace": goal.get("trace", []),
            "evaluation": goal.get("evaluation"),
        }
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
