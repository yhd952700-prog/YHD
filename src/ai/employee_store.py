"""
Durable EmployeeStore for LiuHao AI-OS.

Persists ``Employee`` + its agents + tasks to a single JSON file using ONLY the
stdlib ``json`` module (no third-party dependency is introduced). This is the
first real persistence layer for employees/agents/tasks -- previously ALL of
that state lived in in-memory Python dicts and was lost on every process
restart (flagged in the P1 readiness doc as the single biggest gap).

Design mirrors the existing planner persistence in
``src/ai/goal_task_graph.py`` (``save_state`` / ``load_state``):

  * REDIR-able store path via ``LIUHAO_WORKSPACE_ROOT`` so isolation verifiers
    never touch the real workspace.
  * An unserializable ``result`` falls back to
    ``{"__unserializable__": str(result)}`` so the dump never raises mid
    restart-recovery.
  * Enums (``AgentStatus`` / ``TaskStatus``) are stored by their ``.value``
    string and reconstructed from it.

The store holds a dict of employees keyed by name in one file, so
``list_employees`` / ``remove`` operate over the same JSON document.
"""

from __future__ import annotations

from contextlib import contextmanager
import json
import logging
import os
import threading
import time
import uuid
import weakref
from typing import Any, Dict, Iterator, List, Optional, Sequence

from src.common.atomic_write import (
    StorageWriteError,
    atomic_write_bytes,
    exclusive_file_lock,
)
from src.common.data_paths import resolve_store_path

from .employee import Agent, AgentStatus, Employee, Task, TaskStatus
from .providers import BaseProvider

logger = logging.getLogger(__name__)


class EmployeeStoreError(RuntimeError):
    """员工库读/写事务失败。

    调用方必须把这条异常当成"本次修改没有落盘"，不能像以前那样在
    接口层吞掉后返回成功 —— 那正是"返回成功但员工消失"的来源。
    """


#: 同进程内**所有** ``EmployeeStore`` 实例共享的写锁。
#: 实例级 ``RLock`` 挡不住"每个请求 new 一个 store"的真实路径；Windows 的
#: ``msvcrt`` region lock 又是进程级的，同进程内二次加锁会失败，所以进程内
#: 必须先串行。
_PROCESS_WRITE_LOCK = threading.Lock()

#: 读取瞬时冲突（另一个进程正在 replace）时的短重试次数。
_READ_ATTEMPTS = 6
_READ_RETRY_DELAY = 0.02

#: 员工聚合计数器（单调递增；并发读-改-写时按增量合并，而不是整值覆盖）。
_TOTAL_COUNTER_FIELDS = (
    "total_tasks_submitted",
    "total_tasks_completed",
    "total_tasks_failed",
    "total_goals_submitted",
    "total_goals_completed",
    "total_goals_failed",
)

#: agent 级计数器（同样是单调递增）。
_AGENT_COUNTER_FIELDS = ("completed_tasks", "failed_tasks", "total_latency_ms")


class EmployeeStore:
    """JSON-file backed persistence for ``Employee`` instances.

    Usage::

        store = EmployeeStore()                       # path auto-resolved
        emp = store.create_employee("liuhao-default") # seeds OR loads
        emp.add_task("...")
        store.save(emp)                               # persist
        emp2 = store.load("liuhao-default")            # recover after restart
        store.remove("liuhao-default")
    """

    VERSION = 1

    def __init__(self, path: Optional[str] = None):
        self.path = path or self._default_store_path()
        # 实例级锁：保护同一实例内的状态（基线表等）。真正跨实例/跨进程的
        # 互斥由 ``_PROCESS_WRITE_LOCK`` + OS 文件锁完成。
        self._lock = threading.RLock()
        #: 每个已加载/已保存 Employee 对象上次落盘时的计数器快照。
        #: 并发「读-改-写」时用它算出本次增量并叠加到磁盘当前值，避免后写者
        #: 用旧快照把先写者的 +1 覆盖掉。
        self._baselines: "weakref.WeakKeyDictionary[Employee, Dict[str, Any]]" = (
            weakref.WeakKeyDictionary()
        )

    # ------------------------------------------------------------------ #
    # Path resolution (REDIR-able, mirrors the planner's helper)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _default_store_path() -> str:
        """Default location of the employee store.

        ``LIUHAO_WORKSPACE_ROOT/liuhao_employees.json`` when set (isolation
        verifiers, and the installed launcher's ``<data>/workspace``), then
        ``LIUHAO_DATA_DIR/liuhao_employees.json``, else the repo root.
        """
        repo_root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        return resolve_store_path(
            "liuhao_employees.json",
            os.path.join(repo_root, ".liuhao_employees.json"),
            workspace=True,
        )

    # ------------------------------------------------------------------ #
    # Low-level file IO
    # ------------------------------------------------------------------ #
    def _load_store(self, *, strict: bool = False) -> Dict[str, Any]:
        """Read the whole store document.

        Read errors are retried briefly (another process's ``os.replace`` can
        transiently deny access on Windows). ``strict=True`` is used inside
        write transactions: there a failed/corrupt read must **raise** rather
        than masquerade as an empty store -- treating it as empty and writing
        the merged result back is exactly how every employee gets wiped.
        """
        if not os.path.exists(self.path):
            return {"version": self.VERSION, "employees": {}}
        last_error: Optional[BaseException] = None
        for attempt in range(_READ_ATTEMPTS):
            try:
                with open(self.path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
            except OSError as exc:
                last_error = exc
                if attempt + 1 < _READ_ATTEMPTS:
                    time.sleep(_READ_RETRY_DELAY)
                    continue
                if strict:
                    raise EmployeeStoreError(
                        f"读取员工库失败（{self.path}）：{type(exc).__name__}: {exc}；"
                        "为避免用空快照覆盖已有员工，本次写入已拒绝"
                    ) from exc
                logger.error("EmployeeStore: read failed (%s): %s", self.path, exc)
                return {"version": self.VERSION, "employees": {}}
            except (json.JSONDecodeError, ValueError) as exc:
                if strict:
                    raise EmployeeStoreError(
                        f"员工库文件已损坏（{self.path}）：{exc}；"
                        "为避免覆盖唯一副本，本次写入已拒绝"
                    ) from exc
                logger.error("EmployeeStore: corrupt store (%s): %s", self.path, exc)
                return {"version": self.VERSION, "employees": {}}
            if not isinstance(data, dict):
                if strict:
                    raise EmployeeStoreError(
                        f"员工库结构不合法（{self.path}）：期望 JSON 对象，"
                        f"实际 {type(data).__name__}；本次写入已拒绝"
                    )
                return {"version": self.VERSION, "employees": {}}
            data.setdefault("version", self.VERSION)
            data.setdefault("employees", {})
            return data
        # 逻辑上不可达：最后一次循环要么 return 要么 raise。
        raise EmployeeStoreError(
            f"读取员工库失败（{self.path}）：{last_error}"
        )

    def _write_store(self, data: Dict[str, Any]) -> None:
        """Atomically replace the store (unique temp + fsync + retrying rename).

        ``atomic_write_bytes`` uses a unique temp file in the same directory,
        fsyncs the bytes, then ``os.replace`` with a bounded retry. On final
        failure it raises, and we surface that as :class:`EmployeeStoreError`
        so the caller cannot report success for data that never reached disk.
        """
        payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        try:
            atomic_write_bytes(self.path, payload)
        except Exception as exc:
            logger.error("EmployeeStore: persist failed (%s): %s", self.path, exc)
            # 存储层的 StorageWriteError 本身已是中文可行动提示，直接透传，
            # 不再前缀英文类名。
            detail = str(exc) if isinstance(exc, StorageWriteError) else f"{type(exc).__name__}: {exc}"
            raise EmployeeStoreError(
                f"员工库写入失败（{self.path}）：{detail}；"
                "已重试仍无法原子替换，本次修改未落盘"
            ) from exc

    @contextmanager
    def _write_transaction(self) -> Iterator[None]:
        """进程锁 → 实例锁 → OS 文件锁；整个读-改-写都在锁内完成。

        顺序与 goals 持久化一致。进程锁必须最外层：Windows ``msvcrt`` 的
        region lock 是进程级的，同进程内两个句柄互相加锁会直接失败，因此同
        进程的线程必须先由 ``_PROCESS_WRITE_LOCK`` 串行。
        """
        with _PROCESS_WRITE_LOCK:
            with self._lock:
                with exclusive_file_lock(
                    f"{self.path}.lock", description="员工库"
                ):
                    yield

    # ------------------------------------------------------------------ #
    # Counter baselines (concurrent read-modify-write merge)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _counter_snapshot(data: Dict[str, Any]) -> Dict[str, Any]:
        agents: Dict[str, Dict[str, Any]] = {}
        for ad in data.get("agents", []):
            if not isinstance(ad, dict) or not ad.get("id"):
                continue
            agents[ad["id"]] = {
                field: ad.get(field, 0) for field in _AGENT_COUNTER_FIELDS
            }
        totals = {field: data.get(field, 0) for field in _TOTAL_COUNTER_FIELDS}
        return {
            "agents": agents,
            "totals": totals,
            # 归属不是计数器，但同样要记基线：只有本实例主动改过归属才允许
            # 覆盖磁盘值（见 ``_merge_serialized_employee``）。
            "owner_account_id": str(data.get("owner_account_id") or ""),
        }

    def _remember_baseline(self, employee: Employee, data: Dict[str, Any]) -> None:
        with self._lock:
            self._baselines[employee] = self._counter_snapshot(data)

    @staticmethod
    def _apply_counter_delta(
        memory_value: Any, baseline_value: Any, disk_value: Any, *, as_float: bool = False
    ) -> Any:
        """``disk + (memory - baseline)``，即只把本次调用产生的增量叠加进去。"""
        try:
            value = (disk_value or 0) + ((memory_value or 0) - (baseline_value or 0))
        except (TypeError, ValueError):
            return memory_value
        return float(value) if as_float else int(value)

    @staticmethod
    def _union_preserving_order(first: Any, second: Any) -> List[Any]:
        out = list(first or [])
        seen = set(out)
        for item in second or []:
            if item not in seen:
                out.append(item)
                seen.add(item)
        return out

    def _merge_serialized_employee(
        self,
        disk: Optional[Dict[str, Any]],
        memory: Dict[str, Any],
        employee: Employee,
    ) -> Dict[str, Any]:
        """Merge the in-memory employee into the on-disk document.

        * 其它员工：由外层"磁盘 ∪ 本员工"保留，绝不整份覆盖；
        * 本员工的单调计数器：按 **增量** 叠加到磁盘当前值，并发 +1 不丢；
        * 本员工的 agent/task/列表：磁盘上多出来的条目（另一个写入者新增的）
          保留，同一 id 以内存为准。
        """
        if not isinstance(disk, dict):
            return memory
        # 归属是管理面的事实，不是执行期的派生物：长驻进程在启动时把员工读进
        # 内存，之后任何一次 save 都会把内存里的旧 owner 写回去，把刚完成的
        # 归属迁移静默回滚（实测：迁到 liuhao 后派一次活，owner 又变回原账号）。
        # 规则：只有**本实例加载后主动改过**（内存 != 基线）才采信内存，
        # 其余情况（含没有基线的跨实例 save）一律以磁盘为准。
        disk_owner = str(disk.get("owner_account_id") or "")
        memory_owner = str(memory.get("owner_account_id") or "")
        baseline = self._baselines.get(employee)
        if baseline is None:
            # 不是本实例加载的对象，没有增量依据：内存版本就是这个员工的事实；
            # 唯一例外是归属——无法判定是否"主动修改"，一律保留磁盘值。
            if disk_owner and disk_owner != memory_owner:
                merged = dict(memory)
                merged["owner_account_id"] = disk_owner
                return merged
            return memory

        merged = dict(memory)
        if memory_owner == str(baseline.get("owner_account_id") or "") and disk_owner:
            merged["owner_account_id"] = disk_owner
        for field in _TOTAL_COUNTER_FIELDS:
            merged[field] = self._apply_counter_delta(
                memory.get(field, 0),
                baseline["totals"].get(field, 0),
                disk.get(field, 0),
            )

        disk_agents = {
            ad.get("id"): ad
            for ad in disk.get("agents", [])
            if isinstance(ad, dict) and ad.get("id")
        }
        memory_agents: List[Dict[str, Any]] = []
        memory_ids = set()
        for ad in memory.get("agents", []):
            current = dict(ad)
            agent_id = current.get("id")
            memory_ids.add(agent_id)
            disk_agent = disk_agents.get(agent_id)
            base_agent = baseline["agents"].get(agent_id)
            if disk_agent is not None and base_agent is not None:
                current["completed_tasks"] = self._apply_counter_delta(
                    current.get("completed_tasks", 0),
                    base_agent.get("completed_tasks", 0),
                    disk_agent.get("completed_tasks", 0),
                )
                current["failed_tasks"] = self._apply_counter_delta(
                    current.get("failed_tasks", 0),
                    base_agent.get("failed_tasks", 0),
                    disk_agent.get("failed_tasks", 0),
                )
                current["total_latency_ms"] = self._apply_counter_delta(
                    current.get("total_latency_ms", 0.0),
                    base_agent.get("total_latency_ms", 0.0),
                    disk_agent.get("total_latency_ms", 0.0),
                    as_float=True,
                )
            memory_agents.append(current)
        # 磁盘上有、内存里没有的 agent（另一个写入者新增）：不能丢。
        for agent_id, disk_agent in disk_agents.items():
            if agent_id not in memory_ids:
                memory_agents.append(disk_agent)
        merged["agents"] = memory_agents

        disk_tasks = {
            td.get("id"): td
            for td in disk.get("tasks", [])
            if isinstance(td, dict) and td.get("id")
        }
        memory_tasks: List[Dict[str, Any]] = [dict(td) for td in memory.get("tasks", [])]
        memory_task_ids = {td.get("id") for td in memory_tasks}
        for task_id, disk_task in disk_tasks.items():
            if task_id not in memory_task_ids:
                memory_tasks.append(disk_task)
        merged["tasks"] = memory_tasks

        for field in ("task_queue", "completed_tasks", "failed_tasks", "goals"):
            merged[field] = self._union_preserving_order(
                memory.get(field, []), disk.get(field, [])
            )
        return merged

    # ------------------------------------------------------------------ #
    # Serialization helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _safe_json(value: Any) -> Any:
        """Return ``value`` if it is JSON-safe, else a stringified fallback."""
        try:
            json.dumps(value)
            return value
        except (TypeError, ValueError):
            return {"__unserializable__": str(value)}

    def _serialize_agent(self, agent: Agent) -> Dict[str, Any]:
        return {
            "id": agent.id,
            "agent_type": agent.agent_type,
            "name": agent.name,
            "system_prompt": agent.system_prompt,
            "status": agent.status.value,
            "completed_tasks": agent.completed_tasks,
            "failed_tasks": agent.failed_tasks,
            "total_latency_ms": agent.total_latency_ms,
            "metadata": agent.metadata,
            "current_task": agent.current_task,
        }

    def _serialize_task(self, task: Task) -> Dict[str, Any]:
        return {
            "id": task.id,
            "description": task.description,
            "task_type": task.task_type,
            "depends_on": list(task.depends_on),
            "status": task.status.value,
            "assigned_agent": task.assigned_agent,
            "result": self._safe_json(task.result),
            "error": getattr(task, "error", None),
            "priority": task.priority,
            "metadata": getattr(task, "metadata", {}) or {},
            "created_at": task.created_at,
            "started_at": task.started_at,
            "completed_at": task.completed_at,
        }

    def _serialize_employee(self, employee: Employee) -> Dict[str, Any]:
        return {
            "name": employee.name,
            "agent_count": employee.agent_count,
            "agent_types": list(employee.agent_types),
            "system_prompts": employee.system_prompts,
            "enable_observability": employee.enable_observability,
            # 员工实体字段：重启后必须还原，不能只恢复 agent 池。
            "role_description": employee.role_description,
            "default_capabilities": list(employee.default_capabilities),
            "owner_account_id": employee.owner_account_id,
            "employment_status": employee.employment_status,
            # 稳定 id（改名不变）与"最后一次做了什么/什么时候"。
            "employee_id": employee.employee_id,
            "last_activity": dict(employee.last_activity or {}),
            "agents": [self._serialize_agent(a) for a in employee.agents.values()],
            "tasks": [self._serialize_task(t) for t in employee.tasks.values()],
            "task_queue": list(employee.task_queue),
            "completed_tasks": list(employee.completed_tasks),
            "failed_tasks": list(employee.failed_tasks),
            "total_tasks_submitted": employee.total_tasks_submitted,
            "total_tasks_completed": employee.total_tasks_completed,
            "total_tasks_failed": employee.total_tasks_failed,
            "total_goals_submitted": employee.total_goals_submitted,
            "total_goals_completed": employee.total_goals_completed,
            "total_goals_failed": employee.total_goals_failed,
            "goals": list(employee.goals),
        }

    def _deserialize_employee(
        self, data: Dict[str, Any], provider: Optional[BaseProvider] = None
    ) -> Employee:
        # 未显式传入 provider 时保持 None：Employee 会在执行期按当前模型配置
        # 重新解析，避免名册长期持有构建时的旧 Key（改 Key 免重启）。
        emp = Employee(
            name=data["name"],
            provider=provider,
            agent_count=data.get("agent_count", len(data.get("agents", []))),
            agent_types=list(data.get("agent_types", [])) or None,
            system_prompts=data.get("system_prompts") or None,
            enable_observability=bool(data.get("enable_observability", True)),
            # 老数据缺字段时按"在职、无职责、无归属"还原，不报错。
            role_description=data.get("role_description", "") or "",
            default_capabilities=list(data.get("default_capabilities") or []),
            owner_account_id=data.get("owner_account_id", "") or "",
            employment_status=data.get("employment_status", Employee.ACTIVE)
            or Employee.ACTIVE,
            employee_id=data.get("employee_id", "") or "",
            last_activity=(
                data.get("last_activity")
                if isinstance(data.get("last_activity"), dict)
                else None
            ),
        )

        # Reconstruct agents (overwrite the freshly-built pool).
        emp.agents = {}
        for ad in data.get("agents", []):
            agent = Agent(
                id=ad["id"],
                agent_type=ad["agent_type"],
                name=ad["name"],
                provider=emp.provider,
                provider_resolver=getattr(emp, "_provider_resolver", None),
                system_prompt=ad.get("system_prompt", ""),
                status=AgentStatus(ad["status"]),
                current_task=ad.get("current_task"),
                completed_tasks=int(ad.get("completed_tasks", 0)),
                failed_tasks=int(ad.get("failed_tasks", 0)),
                total_latency_ms=float(ad.get("total_latency_ms", 0.0)),
                metadata=ad.get("metadata", {}) or {},
            )
            emp.agents[agent.id] = agent

        # Reconstruct tasks.
        emp.tasks = {}
        for td in data.get("tasks", []):
            task = Task(
                id=td["id"],
                description=td.get("description", ""),
                task_type=td.get("task_type", "general"),
                priority=int(td.get("priority", 0)),
                depends_on=list(td.get("depends_on", []) or []),
                status=TaskStatus(td["status"]),
                assigned_agent=td.get("assigned_agent"),
                result=td.get("result"),
                created_at=td.get("created_at", time.time()),
                started_at=td.get("started_at"),
                completed_at=td.get("completed_at"),
            )
            # ``error`` / ``metadata`` exist on the planner's TaskNode but not on
            # the Employee Task dataclass; restore them as ad-hoc attributes so
            # the serialized shape round-trips without altering the dataclass.
            task.error = td.get("error")
            task.metadata = td.get("metadata", {}) or {}
            emp.tasks[task.id] = task

        # Reconstruct queue + completion lists + aggregate counters.
        emp.task_queue = list(data.get("task_queue", []))
        emp.completed_tasks = list(data.get("completed_tasks", []))
        emp.failed_tasks = list(data.get("failed_tasks", []))
        emp.total_tasks_submitted = int(data.get("total_tasks_submitted", 0))
        emp.total_tasks_completed = int(data.get("total_tasks_completed", 0))
        emp.total_tasks_failed = int(data.get("total_tasks_failed", 0))
        emp.total_goals_submitted = int(data.get("total_goals_submitted", 0))
        emp.total_goals_completed = int(data.get("total_goals_completed", 0))
        emp.total_goals_failed = int(data.get("total_goals_failed", 0))
        emp.goals = list(data.get("goals", []))
        # 记录"刚读到的磁盘状态"，后续 save 只把本次调用产生的增量叠加回去。
        self._remember_baseline(emp, data)
        return emp

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def _seed_builtin_employees(
        self, store: Dict[str, Any], owner_account_id: str = ""
    ) -> List[str]:
        """把仓库内置角色补进**当前事务里的** store 文档（不落盘）。

        必须在 ``_write_transaction`` 内调用：这样“检查是否已存在 + 写入”
        和调用方的员工创建是同一个原子操作。返回本次新建的角色名。
        """
        from .builtin_employees import builtin_employee_specs

        employees = store.setdefault("employees", {})
        added: List[str] = []
        for spec in builtin_employee_specs():
            name = str(spec["name"])
            if name in employees:
                continue
            employee = Employee(
                name=name,
                agent_count=int(spec.get("agent_count", 1)),
                agent_types=list(spec.get("agent_types") or []) or None,
                enable_observability=bool(
                    spec.get("enable_observability", False)
                ),
                role_description=str(spec.get("role_description", "")),
                default_capabilities=list(spec.get("default_capabilities") or []),
                owner_account_id=str(owner_account_id or ""),
                employee_id=uuid.uuid4().hex,
            )
            data = self._serialize_employee(employee)
            employees[name] = data
            # 与 create_employee 保持同一口径：内存对象也有基线，后续 save 才能
            # 正确计算增量而不是覆盖。
            self._remember_baseline(employee, data)
            added.append(name)
        return added

    def ensure_builtin_employees(self, owner_account_id: str = "") -> List[str]:
        """幂等写入仓库内置角色；返回本次新建的角色名（已存在则为空列表）。"""
        with self._write_transaction():
            store = self._load_store(strict=True)
            added = self._seed_builtin_employees(store, owner_account_id)
            if added:
                self._write_store(store)
                logger.info(
                    "evt=builtin_employees_seeded names=%s owner=%s",
                    ",".join(added), owner_account_id or "-",
                )
        return added

    def create_employee(
        self,
        name: str,
        agent_count: int = 3,
        agent_types: Optional[List[str]] = None,
        system_prompts: Optional[Dict[str, str]] = None,
        enable_observability: bool = True,
        role_description: str = "",
        default_capabilities: Optional[List[str]] = None,
        owner_account_id: str = "",
        employment_status: str = "active",
    ) -> Employee:
        """Get (or seed) an employee by ``name``.

        If an employee with ``name`` already exists in the store, it is LOADED
        (reconstructing agents + tasks + aggregate counters). Otherwise a fresh
        ``Employee`` is constructed with the supplied config and SAVED.

        The existence check and the write happen inside one cross-process
        transaction, so two concurrent hires of *different* names both persist
        instead of the later writer replacing the earlier one's snapshot.
        """
        with self._write_transaction():
            store = self._load_store(strict=True)
            existing = store.get("employees", {}).get(name)
            if existing is not None:
                # 验收项三：即使请求的角色已存在，也保证产品内置角色在位。
                # 只在有归属账号时自动补齐：内置角色属于账号，不属于匿名临时库。
                if owner_account_id and self._seed_builtin_employees(
                    store, owner_account_id
                ):
                    self._write_store(store)
                return self._deserialize_employee(existing)
            emp = Employee(
                name=name,
                agent_count=agent_count,
                agent_types=agent_types,
                system_prompts=system_prompts,
                enable_observability=enable_observability,
                role_description=role_description,
                default_capabilities=default_capabilities,
                owner_account_id=owner_account_id,
                employment_status=employment_status,
                # 稳定 id：改名后仍能把历史记忆/关联认回这位员工。
                employee_id=uuid.uuid4().hex,
            )
            data = self._serialize_employee(emp)
            store.setdefault("employees", {})[name] = data
            if owner_account_id:
                self._seed_builtin_employees(store, owner_account_id)
            self._write_store(store)
            self._remember_baseline(emp, data)
            return emp

    def save(self, employee: Employee) -> str:
        """Serialize ``employee`` (agents, tasks, queue, counters) to JSON.

        Write path: process lock → instance lock → OS file lock → read the
        on-disk document → merge (other employees are preserved; this employee's
        monotonic counters are applied as deltas) → temp + fsync + replace.
        Returns the path written; raises :class:`EmployeeStoreError` if the write
        could not be completed, so callers never report success for lost data.
        """
        with self._write_transaction():
            store = self._load_store(strict=True)
            employees = store.setdefault("employees", {})
            memory = self._serialize_employee(employee)
            merged = self._merge_serialized_employee(
                employees.get(employee.name), memory, employee
            )
            employees[employee.name] = merged
            self._write_store(store)
            self._remember_baseline(employee, merged)
        return self.path

    def load(self, name: str) -> Optional[Employee]:
        """Reconstruct an employee from the store, or ``None`` if absent."""
        store = self._load_store()
        data = store.get("employees", {}).get(name)
        if data is None:
            return None
        return self._deserialize_employee(data)

    def load_many(self, names: Optional[Sequence[str]] = None) -> List[Employee]:
        """一次读库、批量重建员工（名册/列表专用）。

        ``load()`` 每调一次都会重读整份 JSON 并解析，遍历 N 名员工就是
        O(N²) 的读放大量。这里只读一次库，再按需重建，供管理面带全量
        员工字段的列表接口使用。
        """
        store = self._load_store()
        employees = store.get("employees") or {}
        keys = list(employees.keys()) if names is None else [str(n) for n in names]
        result: List[Employee] = []
        for name in keys:
            data = employees.get(name)
            if not isinstance(data, dict):
                continue
            try:
                result.append(self._deserialize_employee(data))
            except Exception as exc:  # noqa: BLE001 - 单条损坏不拖垮整份名册
                logger.warning("could not load employee %s: %s", name, exc)
        return result

    def list_employees(self) -> List[str]:
        """Names of all employees present in the store."""
        return list(self._load_store().get("employees", {}).keys())

    def capability_assignments(self, *, strict: bool = False) -> List[Dict[str, Any]]:
        """员工 → 默认能力（只读，不构造 Employee/Agent）。

        与 ``list_summaries`` 同样的理由：``load()`` 会重建 Employee 并拉起
        可观测性依赖，而"某项能力正被哪些员工使用"只用到两个字段。字段名与
        ``_serialize_employee`` 落盘时写的键保持一致（``default_capabilities``）。

        ``strict=True`` 时读取失败会抛出 ``EmployeeStoreError``，让调用方能区分
        "确实没人用"与"读不出来"——需要据此拦截删除的调用方必须用 strict。
        """
        store = self._load_store(strict=strict)
        rows: List[Dict[str, Any]] = []
        for key, data in (store.get("employees") or {}).items():
            if not isinstance(data, dict):
                continue
            rows.append(
                {
                    "name": str(data.get("name") or key),
                    "employment_status": str(
                        data.get("employment_status") or Employee.ACTIVE
                    ),
                    "default_capabilities": [
                        str(cap) for cap in (data.get("default_capabilities") or [])
                    ],
                }
            )
        return rows

    def list_summaries(self) -> List[Dict[str, Any]]:
        """只读摘要（名册专用）：直接读 JSON，不构造 Employee/Agent。

        ``load()`` 会重建完整员工对象，而 ``Employee(enable_observability=True)``
        会按需导入 ``knowledge.memory``（mem0 → openai → qdrant_client，实测
        首次导入 6–12s）。名册只需要名称 / agent 列表 / 计数器，因此这里走
        纯 JSON 读取，字段口径与 ``_serialize_employee`` 保持一致，但绝不把
        重型导入带进首屏请求路径。

        读文件失败或结构损坏时沿用 ``_load_store`` 的既有降级：返回空列表。
        """
        store = self._load_store()
        rows: List[Dict[str, Any]] = []
        for key, data in (store.get("employees") or {}).items():
            if not isinstance(data, dict):
                continue
            name = str(data.get("name") or key)
            agents = [
                {
                    "id": agent.get("id"),
                    "agent_type": agent.get("agent_type"),
                    "status": agent.get("status"),
                }
                for agent in (data.get("agents") or [])
                if isinstance(agent, dict)
            ]
            rows.append(
                {
                    "id": name,
                    "name": name,
                    "agent_count": int(data.get("agent_count", len(agents)) or 0),
                    "agents": agents,
                    "total_tasks_submitted": int(data.get("total_tasks_submitted") or 0),
                    "total_tasks_completed": int(data.get("total_tasks_completed") or 0),
                    "total_tasks_failed": int(data.get("total_tasks_failed") or 0),
                }
            )
        return rows

    # ------------------------------------------------------------------ #
    # 备份导出 / 导入（整实体：agent 池、任务、计数器、归属）
    # ------------------------------------------------------------------ #
    def export_employees(
        self, owners: Optional[Sequence[str]] = None
    ) -> List[Dict[str, Any]]:
        """按归属导出员工序列化数据；``owners=None`` 表示整库。"""
        store = self._load_store()
        allowed = set(owners) if owners is not None else None
        exported: List[Dict[str, Any]] = []
        for _, data in sorted((store.get("employees") or {}).items()):
            if not isinstance(data, dict):
                continue
            owner = str(data.get("owner_account_id") or "")
            if allowed is not None and owner and owner not in allowed:
                continue
            exported.append(dict(data))
        return exported

    def import_employees(self, payloads: Sequence[Dict[str, Any]]) -> Dict[str, int]:
        """合并导入：同名员工已存在则跳过，绝不覆盖现场数据。

        整个导入在**一个写事务**里完成：只读一次库、只写一次盘（一次
        fsync），而不是每位员工都 ``load()`` + ``save()`` 一遍。1000 名员工
        的包从 O(n²) 的整库读写降到 O(n)。
        """
        restored = 0
        skipped = 0
        with self._write_transaction():
            store = self._load_store(strict=True)
            employees = store.setdefault("employees", {})
            for data in payloads or []:
                if not isinstance(data, dict) or not data.get("name"):
                    skipped += 1
                    continue
                name = str(data["name"])
                if name in employees:
                    skipped += 1
                    continue
                try:
                    employee = self._deserialize_employee(dict(data))
                    employees[name] = self._serialize_employee(employee)
                    restored += 1
                except Exception as exc:  # noqa: BLE001 - 单个员工失败不阻断整体导入
                    logger.warning(
                        "could not restore employee %s: %s", data.get("name"), exc
                    )
                    skipped += 1
            if restored:
                self._write_store(store)
        return {"restored": restored, "skipped": skipped}

    def rename(self, old_name: str, new_name: str) -> Optional[Employee]:
        """改名，但**保留 agent id 不变**（历史任务的归属仍能解析）。

        返回改名后的员工；原名不存在返回 ``None``；新名已存在抛 ``ValueError``
        （绝不静默覆盖另一个员工）。
        """
        with self._write_transaction():
            store = self._load_store(strict=True)
            employees = store.get("employees", {})
            data = employees.get(old_name)
            if data is None:
                return None
            if new_name in employees:
                raise ValueError(f"employee {new_name!r} already exists")
            data = dict(data)
            data["name"] = new_name
            del employees[old_name]
            employees[new_name] = data
            self._write_store(store)
            return self._deserialize_employee(data)

    def remove(self, name: str) -> bool:
        """Remove an employee from the store. Returns True if it existed."""
        with self._write_transaction():
            store = self._load_store(strict=True)
            if name in store.get("employees", {}):
                del store["employees"][name]
                self._write_store(store)
                return True
            return False
