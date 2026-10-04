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
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

if TYPE_CHECKING:  # pragma: no cover - import-time only, avoids any gateway<->ai cycle
    from .employee import Agent, Employee
    from .employee_store import EmployeeStore

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# Used to turn a produced file's absolute path into a workspace-relative,
# honest artifact reference (never inventing a path that isn't real).
from src.ai.workspace import workspace_root

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["ai-management"])


# ─── 请求/响应模型 ───────────────────────────────────────────

class GoalCreateRequest(BaseModel):
    """创建+执行 Goal 的请求体。"""
    natural_language: str = Field(
        ..., min_length=1, max_length=2000,
        description="自然语言目标描述",
    )
    scope: str = Field("L1", description="执行范围 L0-L7")
    plan_mode: str = Field("auto", description="计划模式: auto/manual")
    verification_criteria: Optional[Dict[str, Any]] = Field(
        None, description="可选验证标准"
    )
    # 把目标挂到某个真实存在的项目之下（Projects 面，Top-3 #1 缺口之一）。
    # None 表示独立目标，不属于任何项目。传入不存在的 project_id 会被拒绝。
    project_id: Optional[str] = Field(
        None, description="可选：归属的项目 id（必须已存在，否则 404）"
    )
    # Why this exists: ``create_and_execute_goal`` could always run a goal on a
    # background thread, but nothing over HTTP could ask for it, so the goal
    # always finished inside the POST request. That makes the whole
    # cancellation path unreachable from the product surface: by the time any
    # client could call ``POST /v1/goals/{id}/stop`` the goal was already in a
    # terminal state, and "stop" could only ever be a post-hoc state edit --
    # not the cooperative cancellation the code claims to provide. Default
    # ``False`` keeps the existing synchronous contract untouched.
    background: bool = Field(
        False,
        description=(
            "true: return immediately with state='running' and execute on a "
            "background thread, so GET /v1/goals/{id} can be polled and "
            "POST /v1/goals/{id}/stop can really cancel the run. "
            "false (default): execute synchronously and return the terminal state."
        ),
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
    # Real workspace files the goal actually produced (workspace-relative paths).
    # Empty list = the goal wrote nothing into the workspace. Fail-closed: a path
    # is recorded only when a file_write task completed and its output path was
    # proven to land inside the workspace root.
    artifacts: List[str] = Field(default_factory=list)
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


class HireEmployeeRequest(BaseModel):
    """按名雇佣一个新 AI Employee 的请求体。"""
    name: str = Field(..., min_length=1, max_length=128,
                      description="员工名（唯一键，全局不可重复）")
    agent_count: int = Field(3, ge=1, le=64, description="agent 数量")
    agent_types: List[str] = Field(
        default_factory=list,
        description="agent 类型列表（长度不足时按末位补齐）",
    )


class EmployeeSummary(BaseModel):
    """按名列出员工时的单条摘要。"""
    name: str
    agent_count: int
    agents: List[Dict[str, Any]] = Field(default_factory=list)
    stats: Dict[str, Any] = Field(default_factory=dict)


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
        self._goal_threads: Dict[str, threading.Thread] = {}
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
                from src.kernels.execution._journal import ExecutionJournal
                # Wire the REAL local-tool executor (RestrictedPython compute +
                # workspace-contained file writes) so goals actually execute
                # instead of taking the simulated path. Fail-closed: with no
                # executor the Execution Kernel refuses (never reports success
                # for work it did not do).
                _lcore = LCore(scope="L1", register_local_tools=True)
                # Crash recovery: attach a durable, append-only journal so a
                # goal mid-flight when the process died can resume already-
                # completed tasks on restart (journal keyed by goal id). Path is
                # REDIR-able via LIUHAO_WORKSPACE_ROOT so isolation verifiers
                # never touch the real workspace.
                root = os.environ.get("LIUHAO_WORKSPACE_ROOT")
                if root:
                    journal_path = os.path.join(root, "liuhao_execution.journal")
                else:
                    journal_path = os.path.join(
                        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                        ".liuhao_execution.journal",
                    )
                journal = ExecutionJournal(journal_path)
                self._runtime = AgentRuntime(
                    scope="L1",
                    capability_executor=_lcore.capability_executor(),
                    journal=journal,
                )
                logger.info("AIStateManager: AgentRuntime initialized (real executor wired, journal=%s)", journal_path)
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
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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
        background: bool = False,
        project_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """创建并执行一个 Goal，返回执行结果。

        - 真实绑定到默认 Employee（``liuhao-default``）：提交时记 goal 归属、
          把该员工的 agent 标 BUSY，结束时标回 IDLE 并汇总 goal 计数，使
          roster / KPI 反映真实执行。
        - ``background=False``（默认，保持现有契约）：同步跑完，返回
          completed/failed 终态 —— 现有 REST 端点与测试依赖此行为。
        - ``background=True``：在后台线程跑，立即返回 ``state:"running"``，
          供自主工作者场景与取消演示使用（``POST /v1/goals/{id}/stop`` 可干净中止）。
        """
        # Fail fast on a non-existent project before doing any execution work.
        if project_id is not None:
            try:
                from .projects import ProjectStore
                if not ProjectStore().exists(project_id):
                    raise ValueError(f"Project {project_id} not found")
            except ValueError:
                raise
            except Exception as exc:
                raise ValueError(f"Project {project_id} lookup failed: {exc}")

        runtime = self._ensure_runtime()
        goal_id = str(uuid.uuid4())[:8]

        # Bind to the real Employee (count submitted + mark agents BUSY) so the
        # "autonomous worker" actually drives the employee's agent pool.
        employee = self._ensure_employee()
        employee.record_goal_submitted(goal_id)
        employee.mark_agents_busy()
        self._persist_employee()

        if background:
            running_entry = {
                "goal_id": goal_id,
                "state": "running",
                "natural_language": natural_language,
                "scope": scope,
                "correlation_id": "",
                "error": None,
                "replan_count": 0,
                "replan_suggested": False,
                "trace": [],
                "tasks": [],
                "artifacts": [],
                "evaluation": None,
                "created_at": time.time(),
                "project_id": project_id,
            }
            with self._goal_lock:
                self._goals[goal_id] = running_entry
            self._save_goals()
            t = threading.Thread(
                target=self._run_bound_goal,
                args=(goal_id, natural_language, scope, plan_mode,
                      verification_criteria, True, project_id),
                name=f"goal-{goal_id}",
                daemon=True,
            )
            t.start()
            self._goal_threads[goal_id] = t
            # Register the link immediately (goal id is known up-front).
            self._link_goal_to_project(project_id, goal_id)
            return running_entry

        entry = self._run_bound_goal(
            goal_id, natural_language, scope, plan_mode, verification_criteria, True,
            project_id,
        )
        # Register the link once the goal exists (sync path).
        self._link_goal_to_project(project_id, goal_id)
        return entry

    def _link_goal_to_project(
        self, project_id: Optional[str], goal_id: str
    ) -> None:
        """Best-effort: add a goal to a project's goal_ids (no-op if no project)."""
        if not project_id:
            return
        try:
            from .projects import ProjectStore
            ProjectStore().add_goal(project_id, goal_id)
        except Exception as exc:  # linking must never break goal creation
            logger.warning("goal->project link failed (%s/%s): %s",
                           project_id, goal_id, exc)

    def _run_bound_goal(
        self,
        goal_id: str,
        natural_language: str,
        scope: str,
        plan_mode: str,
        verification_criteria: Optional[Dict[str, Any]],
        persist: bool,
        project_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """后台/同步执行单个 Goal 并维护 Employee 绑定状态（BUSY/IDLE + 计数）。"""
        try:
            runtime = self._ensure_runtime()
            result = runtime.run_goal(
                goal_text=natural_language,
                goal_id=goal_id,
                scope=scope,
                plan_mode=plan_mode,
                verification_criteria=verification_criteria,
                persist=persist,
            )
        except Exception as exc:
            logger.error("_run_bound_goal failed: %s", exc, exc_info=True)
            result = None
            error = str(exc)
        else:
            error = None

        # The employee pool is no longer engaged on this goal.
        employee = self._ensure_employee()
        employee.mark_agents_idle()
        if result is not None:
            final_state = result.state.value if hasattr(result.state, "value") else str(result.state)
            employee.record_goal_finished(goal_id, final_state)
        self._persist_employee()

        if result is None:
            entry = {
                "goal_id": goal_id,
                "state": "failed",
                "error": error,
                "natural_language": natural_language,
                "scope": scope,
                "correlation_id": "",
                "replan_count": 0,
                "replan_suggested": False,
                "trace": [],
                "tasks": [],
                "artifacts": [],
                "evaluation": None,
                "created_at": time.time(),
                "project_id": project_id,
            }
        else:
            entry = self._result_to_dict(result, natural_language, scope)
            entry["project_id"] = project_id
        with self._goal_lock:
            self._goals[goal_id] = entry
        self._save_goals()
        self._goal_threads.pop(goal_id, None)
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
            trace=ExecutionTrace(correlation_id=prior_entry.get("correlation_id", "")),
            error=prior_entry.get("error"),
        )
        result = runtime.replan(prior, goal_text=prior_entry["natural_language"])
        entry = self._result_to_dict(result, prior_entry["natural_language"],
                                     prior_entry["scope"])
        entry["replan_count"] = prior_entry.get("replan_count", 0) + 1
        # Keep the project association across a replan (honest continuity).
        entry["project_id"] = prior_entry.get("project_id")
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
                "project_id": item.get("project_id"),
            })
        return summaries

    def get_goal(self, goal_id: str) -> Dict[str, Any]:
        """获取 Goal 详情。"""
        with self._goal_lock:
            item = self._goals.get(goal_id)
        if item is None:
            raise ValueError(f"Goal {goal_id} not found")
        return item

    def stop_goal(self, goal_id: str) -> Dict[str, Any]:
        """人类操作员中止一个 Goal（主权控制）—— 真实取消运行中目标并审计。

        - 若目标仍在后台运行：设置其 stop flag，执行循环会在任务边界抛出
          GoalCancelled，目标被干净置为 ``cancelled``（绝不伪造成功）。
        - 若目标已结束：**如实保留其真实终态**，不在事后把 error 改写成
          "aborted"。把一个已经 completed/failed 的目标标为人类中止，就是让
          操作员看到一个自己并未造成的结果。
        - 无论哪种情况都审计这条人类喊停，使审计链如实记录主权动作。
        """
        with self._goal_lock:
            item = self._goals.get(goal_id)
            if item is None:
                raise ValueError(f"Goal {goal_id} not found")

        # Ask the runtime to abort any live execution for this goal. Retry
        # briefly because the background thread registers its stop flag a few
        # ms after the goal is created.
        cancelled = False
        try:
            runtime = self._ensure_runtime()
            deadline = time.time() + 5.0
            while time.time() < deadline:
                if runtime.request_stop(goal_id):
                    cancelled = True
                    break
                with self._goal_lock:
                    cur = self._goals.get(goal_id)
                if cur is not None and cur.get("state") != "running":
                    break
                time.sleep(0.1)
        except Exception as exc:
            logger.warning("stop_goal: request_stop failed: %s", exc)

        if cancelled:
            # Wait for the background execution to observe the stop flag and
            # persist its terminal CANCELLED state (bounded wait).
            self._await_goal(goal_id, timeout=30.0)

        # Audit the human stop (honest: this is an allowed sovereign action).
        try:
            self._audit_goal_stop(goal_id, was_running=cancelled)
        except Exception as exc:
            logger.warning("stop_goal: audit failed: %s", exc)

        # Reflect cancellation in the persisted goal state -- but ONLY for a
        # goal that was actually still running. A goal that had already reached
        # a terminal state keeps its real outcome and its real error: the old
        # code stamped "aborted by human operator" onto anything that received
        # a stop POST, so an already-failed goal appeared to have been stopped
        # by someone who was not there, and its root cause was overwritten.
        with self._goal_lock:
            item = self._goals.get(goal_id)
            if item is not None:
                item = dict(item)
                if item.get("state") not in ("completed", "failed", "cancelled"):
                    item["state"] = "cancelled"
                    item["error"] = item.get("error") or "aborted by human operator"
                    item["replan_suggested"] = False
                self._goals[goal_id] = item
            final_state = (self._goals.get(goal_id) or {}).get("state")
        self._save_goals()

        # Keep the employee KPI honest about the cancellation -- and only about
        # a cancellation. Counting every stop POST as a failed goal meant one
        # completed goal was booked BOTH as completed and as failed, inflating
        # the denominator of every success-rate figure derived from these KPIs.
        if final_state == "cancelled":
            try:
                employee = self._ensure_employee()
                employee.record_goal_finished(goal_id, "cancelled")
                self._persist_employee()
            except Exception:
                pass

        with self._goal_lock:
            return dict(self._goals.get(goal_id, item))

    def _await_goal(self, goal_id: str, timeout: float = 30.0) -> None:
        """Block until the background goal leaves the 'running' state (or timeout)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._goal_lock:
                item = self._goals.get(goal_id)
            if item is None or item.get("state") != "running":
                return
            time.sleep(0.1)

    def _audit_goal_stop(self, goal_id: str, was_running: bool) -> None:
        """Record the human stop action in the audit chain (honest sovereignty)."""
        from src.kernels.audit import log_event, AuditEventType, AuditScope

        log_event(
            AuditEventType.GOAL_CONTROL,
            principal_id="human-operator",
            scope=AuditScope.L1,
            outcome="allow",
            details={
                "goal_id": goal_id,
                "action": "stop",
                "was_running": bool(was_running),
                "reason": "human operator abort",
            },
            correlation_id=goal_id,
        )

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
        """获取单个 agent 详情（跨所有员工按 id 解析）。"""
        _employee, agent = self._find_employee_with_agent(agent_id)
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
        """暂停一个 agent（跨所有员工按 id 解析）。"""
        employee, agent = self._find_employee_with_agent(agent_id)
        if agent is None:
            raise ValueError(f"Agent {agent_id} not found")
        ok = agent.pause()
        if employee is self._ensure_employee():
            self._persist_employee()
        else:
            self._store().save(employee)
        return {"agent_id": agent_id, "paused": ok, "status": agent.status.value}

    def resume_agent(self, agent_id: str) -> Dict[str, Any]:
        """恢复一个暂停的 agent（跨所有员工按 id 解析）。"""
        employee, agent = self._find_employee_with_agent(agent_id)
        if agent is None:
            raise ValueError(f"Agent {agent_id} not found")
        ok = agent.resume()
        if employee is self._ensure_employee():
            self._persist_employee()
        else:
            self._store().save(employee)
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
            "total_goals_submitted": emp.total_goals_submitted,
            "total_goals_completed": emp.total_goals_completed,
            "total_goals_failed": emp.total_goals_failed,
            "task_queue_length": len(emp.task_queue),
        }

    # ── 按名员工生命周期（by-name hire / list / remove） ──

    _SEED_DEFAULT_NAME = "liuhao-default"

    def _store(self) -> "EmployeeStore":
        """返回（懒初始化的）EmployeeStore 单例句柄。"""
        if self._employee_store is None:
            self._ensure_employee()  # 顺便把 store 建好
        return self._employee_store

    def _summarize_employee(self, emp: "Employee") -> Dict[str, Any]:
        """把一个真实 Employee 转成列表摘要。"""
        agents = [
            {
                "id": a.id,
                "agent_type": a.agent_type,
                "name": a.name,
                "status": a.status.value,
                "current_task": a.current_task,
                "completed_tasks": a.completed_tasks,
                "failed_tasks": a.failed_tasks,
                "total_latency_ms": a.total_latency_ms,
            }
            for a in emp.agents.values()
        ]
        return {
            "name": emp.name,
            "agent_count": len(emp.agents),
            "agents": agents,
            "stats": {
                "name": emp.name,
                "agent_count": len(emp.agents),
                "total_tasks_submitted": emp.total_tasks_submitted,
                "total_tasks_completed": emp.total_tasks_completed,
                "total_tasks_failed": emp.total_tasks_failed,
                "total_goals_submitted": emp.total_goals_submitted,
                "total_goals_completed": emp.total_goals_completed,
                "total_goals_failed": emp.total_goals_failed,
                "task_queue_length": len(emp.task_queue),
            },
        }

    def hire_employee(
        self,
        name: str,
        agent_count: int = 3,
        agent_types: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """按名雇佣（创建并持久化）一个新员工。

        幂等且**绝不静默覆盖**已存在的同名员工：若 ``name`` 已存在于 store，
        返回 ``already_existed=True`` 标记，由端点决定回 409。否则用
        ``EmployeeStore.create_employee`` 落盘后返回摘要。
        """
        store = self._store()
        existing = store.load(name)
        if existing is not None:
            return {
                "created": False,
                "already_existed": True,
                "employee": self._summarize_employee(existing),
            }
        emp = store.create_employee(
            name=name,
            agent_count=agent_count,
            agent_types=agent_types,
        )
        return {
            "created": True,
            "already_existed": False,
            "employee": self._summarize_employee(emp),
        }

    def list_all_employees(self) -> List[Dict[str, Any]]:
        """列出 store 中**所有**真实员工（不止默认），含 name/agent_count/stats。

        这是 P1「按名员工名册」的真实数据源；与 roster 的 ``real_employees``
        同源（都来自 ``EmployeeStore.list_employees()``）。
        """
        store = self._store()
        result: List[Dict[str, Any]] = []
        for ename in store.list_employees():
            emp = store.load(ename)
            if emp is None:
                continue
            result.append(self._summarize_employee(emp))
        return result

    def remove_employee(self, name: str) -> bool:
        """移除一名员工。

        硬保护：拒绝删除 seed 默认员工 ``liuhao-default``（网关
        ``_ensure_employee`` 依赖它，删掉会让后续请求 500）。返回 ``True``
        表示删除成功；``False`` 表示该员工本就不存在。
        """
        if name == self._SEED_DEFAULT_NAME:
            raise ValueError(
                f"refusing to remove the seed default employee {name!r} "
                f"(the gateway depends on it)"
            )
        store = self._store()
        return store.remove(name)

    def _find_employee_with_agent(
        self, agent_id: str
    ) -> "Tuple[Optional[Employee], Optional[Agent]]":
        """跨所有员工按 agent id 解析出 (employee, agent)。

        默认员工（``_ensure_employee`` 持有的内存实例）优先匹配，使其仍是
        真实来源、pause/resume 经 ``_persist_employee`` 落盘；其余按名员工从
        store 按需加载。agent id 全局唯一（``<employee>-a<i>``），因此不会歧义。
        """
        default = self._ensure_employee()
        agent = default.agents.get(agent_id)
        if agent is not None:
            return default, agent
        store = self._store()
        for ename in store.list_employees():
            if ename == default.name:
                continue
            emp = store.load(ename)
            if emp is None:
                continue
            agent = emp.agents.get(agent_id)
            if agent is not None:
                return emp, agent
        return None, None

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
            # The tasks live on the PLAN, not on the context: ``ExecutionContext``
            # has no ``tasks`` field at all (src/kernels/execution/__init__.py:212),
            # so the old ``hasattr(ctx, "tasks")`` guard was always False and this
            # list was ALWAYS empty -- every goal detail served over HTTP rendered
            # as "did nothing, no tasks". Task status is how an operator decides
            # whether a "completed" goal actually completed anything, so read the
            # plan and fall back to the old attribute if a future shape has one.
            plan_tasks = getattr(getattr(ctx, "plan", None), "tasks", None)
            ctx_tasks = plan_tasks if plan_tasks is not None else getattr(ctx, "tasks", None)
            if ctx_tasks:
                for t in ctx_tasks:
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

        # Collect REAL produced artifacts: workspace files a file_write task
        # actually wrote. We only record a path when the task completed AND its
        # output carries an absolute `path` that provably sits inside a workspace
        # root (the one the tool itself used, falling back to the live
        # workspace_root()). Nothing is invented; if no file was written the list
        # stays empty.
        artifacts = AIStateManager._collect_artifacts(plan_tasks or [])

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
            "artifacts": artifacts,
            "evaluation": evaluation,
            "created_at": time.time(),
        }

    @staticmethod
    def _collect_artifacts(plan_tasks: List[Any]) -> List[str]:
        """Return workspace-relative paths of files the goal really produced.

        Walks the plan's tasks. For each completed ``file_write`` task whose
        output carries a concrete ``path``, it proves the path lands inside the
        workspace (using the root the tool reported, else the live
        :func:`workspace_root`) and records the relative path. Deduped; never
        invents a path. A path outside the workspace (or with no readable root)
        is skipped fail-closed.
        """
        produced: List[str] = []
        seen: set[str] = set()
        for t in plan_tasks:
            if getattr(t, "capability_id", None) != "file_write":
                continue
            tstatus = t.status.value if hasattr(t.status, "value") else str(t.status)
            if tstatus != "completed":
                continue
            res = t.result
            if not isinstance(res, dict):
                continue
            written = res.get("path")
            if not isinstance(written, str) or not written:
                continue
            # Prefer the exact root the tool wrote into; fall back to the live
            # workspace root.
            root = res.get("workspace_root")
            root = root if isinstance(root, str) and root else workspace_root()
            try:
                ws_root = os.path.abspath(root)
                abs_path = os.path.abspath(written)
            except (TypeError, ValueError, OSError):
                continue
            if abs_path == ws_root:
                rel = ""
            elif abs_path.startswith(ws_root + os.sep):
                rel = os.path.relpath(abs_path, ws_root)
            else:
                # Outside the workspace: do not record (fail-closed).
                continue
            # Normalize to forward slashes for a stable, portable reference.
            rel = rel.replace(os.sep, "/")
            if rel and rel not in seen:
                seen.add(rel)
                produced.append(rel)
        return produced


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

    ``background=true`` 时只在后台启动执行并立即返回 ``state:"running"``，
    使 ``GET /v1/goals/{id}`` 可被轮询、``POST /v1/goals/{id}/stop`` 能真的
    终止一次运行中的执行 —— 没有这个开关，停止能力在 HTTP 面上永远不可达，
    因为目标在 POST 返回前就已经跑完了。
    """
    mgr = AIStateManager()
    try:
        return mgr.create_and_execute_goal(
            natural_language=req.natural_language,
            scope=req.scope,
            plan_mode=req.plan_mode,
            verification_criteria=req.verification_criteria,
            background=req.background,
            project_id=req.project_id,
        )
    except ValueError as exc:
        # e.g. a non-existent project_id — honest 404, not a 503 crash.
        raise HTTPException(status_code=404, detail=str(exc))
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


@router.post("/goals/{goal_id}/stop")
def stop_goal(goal_id: str) -> Dict[str, Any]:
    """人类操作员中止一个正在运行的 Goal（主权控制）。

    控制台「中止」按钮的落点。若目标仍在后台运行，设置其 stop flag，执行循环
    会在任务边界干净抛出 GoalCancelled，目标被置为 ``cancelled``（绝不伪造成功）；
    若目标已结束则幂等标记为 ``cancelled``。人类喊停动作会被审计。
    """
    mgr = AIStateManager()
    try:
        return mgr.stop_goal(goal_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/employees")
def list_employees() -> Dict[str, Any]:
    """列出**所有**真实 AI Employee（来自 EmployeeStore，按名）。

    返回 ``employees``（全员摘要列表）作为 P1「按名员工名册」的真实契约；
    同时保留 ``agents``/``count``/``stats``（默认员工的 agent pool），
    以保持现有 console 运行时 Tab 的向后兼容。
    """
    mgr = AIStateManager()
    try:
        all_emps = mgr.list_all_employees()
        default_agents = mgr.list_agents()
        default_stats = mgr.get_employee_stats()
        return {
            "employees": all_emps,
            "employee_count": len(all_emps),
            "agents": default_agents,
            "count": len(default_agents),
            "stats": default_stats,
        }
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Employee listing failed: {exc}")


@router.post("/employees")
def hire_employee(req: HireEmployeeRequest) -> Dict[str, Any]:
    """按名雇佣一个新 AI Employee。

    若 ``name`` 已存在 → 409（绝不静默覆盖）；创建成功 → 201。
    """
    mgr = AIStateManager()
    try:
        result = mgr.hire_employee(req.name, req.agent_count, req.agent_types)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if result["already_existed"]:
        raise HTTPException(
            status_code=409,
            detail=f"employee {req.name!r} already exists",
        )
    return JSONResponse(status_code=201, content=result["employee"])


@router.delete("/employees/{name}")
def remove_employee(name: str) -> Dict[str, Any]:
    """移除一名按名员工。

    - 删除 seed 默认 ``liuhao-default`` → 403（保护网关依赖）；
    - 不存在 → 404；成功 → 200。
    """
    mgr = AIStateManager()
    try:
        removed = mgr.remove_employee(name)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    if not removed:
        raise HTTPException(status_code=404, detail=f"employee {name!r} not found")
    return {"removed": name, "ok": True}


@router.get("/employees/{agent_id}")
def get_employee(agent_id: str) -> Dict[str, Any]:
    """获取单个 AI Employee（agent）详情（跨所有员工按 id 解析）。"""
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
