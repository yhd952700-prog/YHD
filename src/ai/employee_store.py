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
from typing import Any, Dict, Iterator, List, Optional

from src.common.atomic_write import atomic_write_bytes, exclusive_file_lock

from .employee import Agent, AgentStatus, Employee, Task, TaskStatus
from .providers import BaseProvider, get_provider

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

        ``LIUHAO_WORKSPACE_ROOT/liuhao_employees.json`` when that env var is
        set (so isolation verifiers never write to the real workspace), else
        ``<repo_root>/.liuhao_employees.json``.
        """
        root = os.environ.get("LIUHAO_WORKSPACE_ROOT")
        if root:
            return os.path.join(root, "liuhao_employees.json")
        repo_root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        return os.path.join(repo_root, ".liuhao_employees.json")

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
            raise EmployeeStoreError(
                f"员工库写入失败（{self.path}）：{type(exc).__name__}: {exc}；"
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
        return {"agents": agents, "totals": totals}

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
        baseline = self._baselines.get(employee)
        if baseline is None:
            # 不是本实例加载的对象，没有增量依据：内存版本就是这个员工的事实。
            return memory

        merged = dict(memory)
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
        provider = provider or get_provider()
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
                provider=provider,
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

    def list_employees(self) -> List[str]:
        """Names of all employees present in the store."""
        return list(self._load_store().get("employees", {}).keys())

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
