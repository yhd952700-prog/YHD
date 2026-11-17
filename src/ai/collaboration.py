"""
Multi-Agent Collaboration Protocol — MASTER-SPEC Phase 10.

Adds agent-to-agent message passing, role specialization, hierarchical
delegation, and sequential pipeline execution on top of the existing
`Agent` primitive (``src/ai/employee.py``). Reuses ``Agent.execute()`` which
is provider-backed and real — no fake agents (CODEX-CONTRACT NO FAKE AI).

Layering (existing -> new):

    Agent         (lifecycle + execute)      src/ai/employee.py
      L-- Employee     (fan-out task dist)   src/ai/employee.py
      L-- AgentPool    (fan-out across emp)  src/ai/employee.py
      L-- MultiAgentTeam (NEW: roles + message bus + hierarchy + pipeline)

Where the existing ``Employee``/``AgentPool`` model a single manager pushing
tasks down to homogeneous workers (fan-out/fan-in), this module models
*peer* collaboration: role-specialized agents that route addressed messages to
one another, delegate down a hierarchy, report results back, and hand data
down a sequential pipeline (MASTER-SPEC scenario §228: Manager -> Researchers
-> Analyst -> QA).
"""

from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field
from enum import Enum
import threading
import uuid
import time

from .employee import Agent, AgentStatus
from .providers import (
    DEMO_MODEL_NOT_CONFIGURED_MESSAGE,
    MODEL_NOT_CONFIGURED_MESSAGE,
    BaseProvider,
    get_provider,
)
from .observability import observe, get_logger
from .audit import audited

# 异步派活默认超时（7-3 §4.1：与网关生成超时同一口径，默认 120 秒）。
DEFAULT_DELEGATE_TIMEOUT_SECONDS = 120.0


class Role(Enum):
    """Specialized agent roles (MASTER-SPEC §63, scenario §228)."""
    MANAGER = "manager"
    RESEARCHER = "researcher"
    ANALYST = "analyst"
    QA = "qa"
    # 7-3 §3 的固定队形：提醒员复用 QA 的检查职责，写代码员是新增角色。
    REMINDER = "reminder"
    CODER = "coder"
    GENERAL = "general"


# 7-3 §3：每个角色的系统提示词（队长派活时默认用，hire 可覆盖）。
# 队员只认队长派发的任务书，不认消息正文里的自由文本指令；危险动作
# （写文件 / 删文件 / 改代码 / 对外发消息）一律先等审批，不自己动手。
ROLE_SYSTEM_PROMPTS = {
    Role.MANAGER: (
        "你是贾维斯，队长。Joseph 只跟你一个人说话。你负责拆任务、派活、"
        "汇总结果、回 Joseph 话。队员的回答要标来源；没干完就如实说没干完，"
        "不编一个“已完成”交差。"
    ),
    Role.RESEARCHER: (
        "你是查资料员，只做查资料：联网查东西、知识库检索、汇总来源与时间。"
        "每条关键结论必须标来源与查到的时间；查不到就说查不到，不编答案。"
        "回答前必须走核实检查点（answer_with_verification）：有来源才说，"
        "没来源标“没来源”，查不到就说查不到，不编来源和数字。"
        "你不写文件、不跑命令、不对外发消息。"
    ),
    Role.ANALYST: (
        "你是整理员，只做整理：读文件、汇总表格、写摘要、归档。"
        "写文件必须先走审批，没批之前一个字也不落盘；不删文件、不改代码、"
        "不动系统设置。"
    ),
    Role.REMINDER: (
        "你是提醒员，只做提醒与检查：管提醒、到点催办、检查队员产物的完整性。"
        "你不替 Joseph 做决定、不代发消息；发现问题如实报给队长。"
    ),
    Role.CODER: (
        "你是写代码员，只出提议与补丁说明，不直接写文件、不直接跑命令。"
        "禁区（src/security、src/identity、src/gateway/auth.py、src/selfmod）"
        "永远不动；改代码合入必须等 Joseph 点确认。"
    ),
}

#: 7-3 §3：每个角色能调的聊天工具（capability 名）。队员的工具是贾维斯
#: 工具集的子集（取交集，不放大）；角色表里没有的工具，队员调不到。
#: make_tools() 里没有的 capability 不会出现（不编能力）。
ROLE_TOOL_ALLOWLIST = {
    # 注意：受限 Python 计算在 make_tools() 里的 capability 真名是
    # "python_compute"（下划线），不是设计稿写的 "python.compute"。
    "researcher": frozenset(
        {"memory.search", "knowledge.search", "python_compute", "system.status"}
    ),
    "analyst": frozenset(
        {"memory.search", "file.read", "file.list", "knowledge.search"}
    ),
    "reminder": frozenset({"memory.search", "reminder.manage"}),
    "coder": frozenset({"memory.search", "file.read", "file.list"}),
    # 队长与通用角色不设子集（队长就是贾维斯本体）。
    "manager": None,
    "general": None,
}

#: 危险动作种类（7-3 Step 4 / 七家合并 P1）：派活时标上，审批条目与
#: 汇总里原样带上，方便 Joseph 一眼看出要拍板的是哪类事。
DANGER_KINDS = ("delete_file", "code_merge", "send_message")

#: 超时 / 连接类失败才重试（7-3 §6，与聊天 chat_with_retry 同一规则）；
#: 产出过内容后绝不重试。
_RETRYABLE_ERROR_MARKERS = (
    "超时", "timeout", "Timeout", "timed out",
    "connection", "Connection", "connect", "Connect",
    "暂时", "temporarily",
)


class MessageKind(Enum):
    """Inter-agent message kinds (collaboration primitives)."""
    ASK = "ask"            # request info/action; expect a reply
    TELL = "tell"          # inform without expecting action
    DELEGATE = "delegate"  # assign a subtask down the hierarchy
    REPORT = "report"      # deliver a result back to a delegator
    REVIEW = "review"      # emit/receive a review verdict
    RESULT = "result"      # raw pipeline hand-off payload


@dataclass
class AgentMessage:
    """A message addressed between two agents on a shared bus."""
    id: str
    sender: str
    receiver: str  # agent id, or "*" for broadcast
    kind: MessageKind
    payload: Dict[str, Any]
    timestamp: float = field(default_factory=time.time)
    in_reply_to: Optional[str] = None


class MessageBus:
    """Addressable in-memory message bus.

    Real routing: a message is delivered to its named receiver's mailbox (or to
    every mailbox on broadcast) and nowhere else. ``poll`` drains a mailbox.
    """

    def __init__(self) -> None:
        self._mailboxes: Dict[str, List[AgentMessage]] = {}

    def register(self, agent_id: str) -> None:
        """Create a (possibly empty) mailbox so broadcast reaches this agent."""
        self._mailboxes.setdefault(agent_id, [])

    @audited("p10.bus.send", module="src.ai.collaboration")
    def send(self, message: AgentMessage) -> None:
        if message.receiver == "*":
            for box in self._mailboxes.values():
                box.append(message)
        else:
            self._mailboxes.setdefault(message.receiver, []).append(message)

    def poll(self, agent_id: str) -> List[AgentMessage]:
        """Return and drain the mailbox for ``agent_id``."""
        box = self._mailboxes.get(agent_id, [])
        if box:
            self._mailboxes[agent_id] = []
        return box

    def poll_one(self, agent_id: str) -> Optional[AgentMessage]:
        """Pop and return a single message (FIFO), leaving the rest queued.

        Unlike ``poll`` (which drains the whole mailbox), this lets a
        consuming loop process one message per step without dropping any
        backlog — the behaviour ``RuntimeLoop`` relies on.
        """
        box = self._mailboxes.get(agent_id)
        if not box:
            return None
        return box.pop(0)

    def pending(self, agent_id: str) -> int:
        return len(self._mailboxes.get(agent_id, []))


@dataclass
class Collaborator:
    """An ``Agent`` wrapped with a role and a mailbox on the shared bus."""

    agent: Agent
    role: Role
    bus: MessageBus

    def __post_init__(self) -> None:
        # Register the mailbox up front so broadcast reaches this member.
        self.bus.register(self.agent.id)

    @property
    def id(self) -> str:
        return self.agent.id

    def send(self, receiver: str, kind: MessageKind, payload: Dict[str, Any],
             in_reply_to: Optional[str] = None) -> str:
        message = AgentMessage(
            id=f"msg_{uuid.uuid4().hex[:12]}",
            sender=self.id,
            receiver=receiver,
            kind=kind,
            payload=payload,
            in_reply_to=in_reply_to,
        )
        self.bus.send(message)
        return message.id

    def receive(self) -> List[AgentMessage]:
        return self.bus.poll(self.id)

    def pending(self) -> int:
        return self.bus.pending(self.id)


class MultiAgentTeam:
    """A role-specialized team with message passing, hierarchy, and pipelines.

    Members are real ``Agent`` instances (provider-backed). Roles are used for
    routing; messages flow through a shared ``MessageBus``.
    """

    # A member is "available" when it has not run yet (IDLE) or has finished
    # its previous task (COMPLETED). FAILED/STOPPED/PAUSED members are not
    # auto-picked for new work.
    _AVAILABLE = (AgentStatus.IDLE, AgentStatus.COMPLETED)

    def __init__(self, name: str, provider: Optional[BaseProvider] = None,
                 team_store: Any = None) -> None:
        self.name = name
        self.provider = provider or get_provider()
        self.bus = MessageBus()
        self.members: Dict[str, Collaborator] = {}
        self._by_role: Dict[Role, List[str]] = {}
        self._log = get_logger("collaboration")
        # 持久化（7-3 Step 2）：不传即不落盘，纯内存行为与原来一样。
        self.team_store = team_store
        # 已终态（超时 / 已叫停）的任务 id：后台线程晚归时不再改写记录。
        self._final_tasks: set = set()
        self._final_lock = threading.Lock()
        # P5 成品落盘钩子（team_service 注入）：complete 前把队员产物写成
        # 真实文件，返回 {"kind": "file", "name": ..., "path": ...}；
        # 不设钩子时不写文件，行为与原来一样。
        self.artifact_writer = None

    def _mark_final(self, task_id: str) -> bool:
        """把任务标为终态；已终态返回 False（晚归的线程不再写）。"""
        with self._final_lock:
            if task_id in self._final_tasks:
                return False
            self._final_tasks.add(task_id)
            return True

    def hire(self, role: Role, name: Optional[str] = None,
             agent_id: Optional[str] = None,
             system_prompt: Optional[str] = None,
             provider: Optional[BaseProvider] = None) -> str:
        """Create a role-specialized agent and add it to the team."""
        agent_id = agent_id or f"{role.value}_{uuid.uuid4().hex[:8]}"
        name = name or f"{self.name}-{role.value}"
        agent = Agent(
            id=agent_id,
            agent_type=role.value,
            name=name,
            provider=provider or self.provider,
            system_prompt=system_prompt or "",
        )
        self.members[agent_id] = Collaborator(agent=agent, role=role, bus=self.bus)
        self._by_role.setdefault(role, []).append(agent_id)
        return agent_id

    def member_ids(self, role: Role) -> List[str]:
        return list(self._by_role.get(role, []))

    def _first_available(self, role: Role) -> Optional[str]:
        for agent_id in self._by_role.get(role, []):
            if self.members[agent_id].agent.status in self._AVAILABLE:
                return agent_id
        return None

    @observe("MultiAgentTeam.delegate")
    def delegate(self, manager_id: str, role: Role, task: str, **kwargs) -> Dict[str, Any]:
        """Manager delegates a task to the first available ``role`` agent.

        Routes a DELEGATE message through the bus; the worker receives it from
        its mailbox, executes (a real provider call), and sends a REPORT back
        to the manager. The report remains in the manager's mailbox.
        """
        manager = self.members.get(manager_id)
        if manager is None:
            return {"status": "failed", "error": f"unknown manager {manager_id}"}

        target = self._first_available(role)
        if target is None:
            return {"status": "failed", "error": f"no available {role.value} agent"}

        msg_id = manager.send(target, MessageKind.DELEGATE, {"task": task})

        worker = self.members[target]
        received = worker.receive()
        if not received:
            return {"status": "failed", "error": f"delegate message lost for {target}"}

        # The task actually travels in the DELEGATE message payload.
        result = worker.agent.execute(received[0].payload.get("task", task), **kwargs)
        result["delegated_by"] = manager_id
        result["role"] = role.value

        worker.send(manager_id, MessageKind.REPORT,
                    {"task": task, "result": result}, in_reply_to=msg_id)
        return result

    def delegate_async(self, manager_id: str, role: Role, task: str,
                       plan_id: str = "",
                       step_total: int = 1,
                       timeout: float = DEFAULT_DELEGATE_TIMEOUT_SECONDS,
                       approval_required: bool = False,
                       danger_kind: str = "",
                       **kwargs) -> Dict[str, Any]:
        """异步派活（7-3 Step 2）：派发即返回任务 id，不当场等结果。

        队员在守护线程里执行，默认 120 秒超时；超时 / 失败 / 完成都写进
        ``team_store``（没传 store 时只返回任务 id，不落盘）。同一队员已有
        在跑任务时直接拒绝（409 语义，见 7-3 §4.1），不并行抢占。

        ``approval_required=True``（7-3 Step 4）：含危险动作的派活只登记、
        不执行——任务挂为 ``awaiting_approval``，等
        :meth:`approve_and_run` 被 Joseph 批了才跑；没接持久化时直接拒绝
        （挂不起就不能放行，fail-closed）。
        """
        if danger_kind and danger_kind not in DANGER_KINDS:
            raise ValueError(
                f"未知的危险动作种类：{danger_kind}（只认 {list(DANGER_KINDS)}）"
            )
        manager = self.members.get(manager_id)
        if manager is None:
            return {"status": "failed", "error": f"unknown manager {manager_id}"}

        target = self._first_available(role)
        if target is None:
            return {"status": "failed", "error": f"no available {role.value} agent"}

        store = self.team_store
        correlation_id = f"team_{uuid.uuid4().hex[:12]}"

        if approval_required:
            # 挂起：队员不标忙、不执行；批了才跑。没持久化就拒绝。
            if store is None:
                return {
                    "status": "failed",
                    "error": "没有接团队持久化，含危险动作的派活不能挂起等批",
                }
            record = store.create_held_task(
                role=role.value, title=task, plan_id=plan_id,
                step_total=max(1, step_total), correlation_id=correlation_id,
                meta={
                    "manager_id": manager_id,
                    "agent_id": target,
                    "task": task,
                    "timeout": timeout,
                    "step_total": max(1, step_total),
                    "danger_kind": danger_kind,
                },
            )
            task_id = record["task_id"]
            store.ensure_member(role.value, agent_id=target)
            msg_id = manager.send(target, MessageKind.DELEGATE, {"task": task})
            store.record_message(
                message_id=msg_id, sender=manager_id, receiver=target,
                kind=MessageKind.DELEGATE.value, summary=task[:200],
                correlation_id=correlation_id,
            )
            return {
                "status": "awaiting_approval",
                "task_id": task_id,
                "correlation_id": correlation_id,
                "role": role.value,
                "agent_id": target,
                "danger_kind": danger_kind,
            }

        # 派发瞬间即标忙：异步线程还没跑到 execute() 之前，第二通派活
        # 不能再挑到同一个人（409 语义，不并行抢占）。
        self.members[target].agent.status = AgentStatus.RUNNING

        task_id = ""
        if store is not None:
            record = store.create_task(
                role=role.value, title=task, plan_id=plan_id,
                step_total=max(1, step_total), correlation_id=correlation_id,
            )
            task_id = record["task_id"]
            store.ensure_member(role.value, agent_id=target)

        msg_id = manager.send(target, MessageKind.DELEGATE, {"task": task})
        if store is not None:
            store.record_message(
                message_id=msg_id, sender=manager_id, receiver=target,
                kind=MessageKind.DELEGATE.value, summary=task[:200],
                correlation_id=correlation_id,
            )

        thread = threading.Thread(
            target=self._run_delegated,
            name=f"team-delegate-{role.value}",
            daemon=True,
            kwargs={
                "task_id": task_id,
                "manager_id": manager_id,
                "target": target,
                "role": role,
                "task": task,
                "msg_id": msg_id,
                "correlation_id": correlation_id,
                "timeout": timeout,
                "kwargs": kwargs,
            },
        )
        thread.start()
        return {
            "status": "accepted",
            "task_id": task_id,
            "correlation_id": correlation_id,
            "role": role.value,
            "agent_id": target,
        }

    def approve_and_run(self, task_id: str) -> Dict[str, Any]:
        """Joseph 批了待批任务：开始执行（7-3 Step 4）。

        队员按角色现找（重启后队员 id 会变，不认挂起时记的旧 id）；
        任务不在待批状态时拒绝，不重复执行。
        """
        store = self.team_store
        if store is None:
            return {"status": "failed", "error": "no team store attached"}
        try:
            record = store.get_task(task_id)
        except KeyError:
            return {"status": "failed", "error": f"unknown team task {task_id}"}
        if record["status"] != "awaiting_approval":
            return {
                "status": "failed",
                "error": f"任务不在待批状态（现在是 {record['status']}），不能开始执行",
            }
        meta = record.get("meta") or {}
        try:
            role = Role(str(record["role"]))
        except ValueError:
            return {"status": "failed", "error": f"unknown role {record['role']}"}
        manager_id = self._first_available(Role.MANAGER) or str(meta.get("manager_id") or "")
        manager = self.members.get(manager_id)
        if manager is None:
            return {"status": "failed", "error": f"unknown manager {manager_id}"}
        target = self._first_available(role)
        if target is None:
            return {"status": "failed", "error": f"no available {role.value} agent"}
        self.members[target].agent.status = AgentStatus.RUNNING
        store.mark_running(task_id)
        store.ensure_member(role.value, agent_id=target)
        timeout = float(meta.get("timeout") or DEFAULT_DELEGATE_TIMEOUT_SECONDS)
        task_text = str(record["title"])
        correlation_id = str(record.get("correlation_id") or task_id)
        msg_id = manager.send(target, MessageKind.DELEGATE, {"task": task_text})
        store.record_message(
            message_id=msg_id, sender=manager_id, receiver=target,
            kind=MessageKind.DELEGATE.value, summary=task_text[:200],
            correlation_id=correlation_id,
        )
        thread = threading.Thread(
            target=self._run_delegated,
            name=f"team-delegate-{role.value}",
            daemon=True,
            kwargs={
                "task_id": task_id,
                "manager_id": manager_id,
                "target": target,
                "role": role,
                "task": task_text,
                "msg_id": msg_id,
                "correlation_id": correlation_id,
                "timeout": timeout,
                "kwargs": {},
            },
        )
        thread.start()
        return {
            "status": "accepted",
            "task_id": task_id,
            "correlation_id": correlation_id,
            "role": role.value,
            "agent_id": target,
        }

    def _run_delegated(self, *, task_id: str, manager_id: str, target: str,
                       role: Role, task: str, msg_id: str, correlation_id: str,
                       timeout: float, kwargs: Dict[str, Any]) -> None:
        """队员执行线程：取件 → 执行（超时/连接失败重试 1 次）→ 回报告。

        超时仍以后台线程晚归不改写终态记录（_mark_final 守卫）。完成时
        经 artifact_writer 把产物落盘为真实文件（P5），再记完成。
        后台线程里的 store 写操作一律走 _safe：网关 teardown 时连接
        可能已关，写失败只记日志，不让守护线程抛错。
        """
        store = self.team_store

        def _safe(fn, *args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception:  # noqa: BLE001 - 后台写失败只记日志
                self._log.warning("team background store write failed", exc_info=True)
                return None

        worker = self.members[target]
        received = worker.receive()
        if not received:
            # 派发时标的忙要还回去，否则队员永远卡在 RUNNING。
            worker.agent.status = AgentStatus.IDLE
            if store is not None and self._mark_final(task_id):
                _safe(store.fail_task, task_id, f"派发消息丢失（队员 {target} 没收到）")
            return

        last_error = ""
        for attempt in (1, 2):
            outcome: Dict[str, Any] = {}
            error_text = ""

            def _execute() -> None:
                try:
                    outcome.update(
                        worker.agent.execute(received[0].payload.get("task", task), **kwargs)
                    )
                except Exception as exc:  # noqa: BLE001 - 执行异常进任务记录
                    outcome["status"] = "failed"
                    outcome["error"] = str(exc)

            worker_thread = threading.Thread(target=_execute, daemon=True)
            worker_thread.start()
            worker_thread.join(timeout=timeout)
            if worker_thread.is_alive():
                # 超时：第一次超时重试 1 次，第二次超时才记失败；后台调用
                # 晚归时终态守卫会拦住改写。
                last_error = (
                    f"执行超过 {int(timeout)} 秒，已停止等待"
                    "（后台如仍在跑，结果不再采用）"
                )
                if attempt == 1:
                    worker.agent.status = AgentStatus.RUNNING
                    if store is not None:
                        _safe(
                            store.record_message,
                            message_id=f"msg_{uuid.uuid4().hex[:12]}",
                            sender=manager_id, receiver=target,
                            kind=MessageKind.TELL.value,
                            summary=f"第 1 次执行超时，重试 1 次（{last_error}）",
                            correlation_id=correlation_id,
                        )
                    continue
                worker.agent.status = AgentStatus.FAILED
                if store is not None and self._mark_final(task_id):
                    _safe(store.fail_task, task_id, last_error)
                return
            if task_id and task_id in self._final_tasks:
                return  # 已叫停：结果不再采用。
            if outcome.get("status") == "completed":
                break
            last_error = str(
                outcome.get("error") or outcome.get("result") or "队员没有完成"
            )
            retryable = any(
                marker in last_error for marker in _RETRYABLE_ERROR_MARKERS
            )
            if attempt == 1 and retryable:
                if store is not None:
                    _safe(
                        store.record_message,
                        message_id=f"msg_{uuid.uuid4().hex[:12]}",
                        sender=manager_id, receiver=target,
                        kind=MessageKind.TELL.value,
                        summary=f"第 1 次执行失败（{last_error[:100]}），重试 1 次",
                        correlation_id=correlation_id,
                    )
                continue
            if store is not None and self._mark_final(task_id):
                _safe(store.fail_task, task_id, last_error)
            return
        else:  # pragma: no cover - 循环正常 break 时不走这里
            return

        report = worker.send(
            manager_id, MessageKind.REPORT,
            {"task": task, "result": outcome}, in_reply_to=msg_id,
        )
        if store is not None:
            if not self._mark_final(task_id):
                return
            _safe(
                store.record_message,
                message_id=report, sender=target, receiver=manager_id,
                kind=MessageKind.REPORT.value,
                summary=str(outcome.get("result", ""))[:200],
                correlation_id=correlation_id,
            )
            # P5：产物先落盘为真实文件，再记完成；写失败不谎称已交付。
            artifacts = []
            if self.artifact_writer is not None and task_id:
                try:
                    artifact = self.artifact_writer(
                        target, task_id, str(outcome.get("result", ""))
                    )
                except Exception:  # noqa: BLE001 - 产物写失败只记日志
                    artifact = None
                    self._log.warning("team artifact write failed: %s", task_id)
                if isinstance(artifact, dict) and artifact.get("path"):
                    artifacts.append(artifact)
            # Agent.execute 不返回用量：标 estimated，不伪装成真实用量。
            tokens = outcome.get("tokens_used")
            result_text = str(outcome.get("result", ""))
            # 没配模型时队员回来的是未配置提示（不是干完的活）：
            # 状态记 needs_model（卡住了），绝不能记 done 让人以为活干完了。
            # 结果原文照样留着、产物照样落盘，卡住的原因写进 error。
            if result_text.strip() in (
                MODEL_NOT_CONFIGURED_MESSAGE.strip(),
                DEMO_MODEL_NOT_CONFIGURED_MESSAGE.strip(),
            ):
                _safe(
                    store.block_task,
                    task_id,
                    "没有配置模型，队员没法干活：请到「系统设置 → 模型与 Key」"
                    "配置模型后再派",
                    result_text=result_text,
                    artifacts=artifacts,
                )
            else:
                _safe(
                    store.complete_task,
                    task_id,
                    result_text=result_text,
                    artifacts=artifacts,
                    tokens_used=int(tokens) if isinstance(tokens, int) else None,
                    tokens_estimated=not isinstance(tokens, int),
                )

    def cancel_team_task(self, task_id: str, reason: str = "") -> Dict[str, Any]:
        """叫停一个在跑的队员任务：记录标已取消；后台晚归的结果不再采用。"""
        self._mark_final(task_id)
        store = self.team_store
        if store is None:
            return {"status": "failed", "error": "no team store attached"}
        try:
            record = store.cancel_task(task_id, reason or "已叫停")
        except KeyError:
            return {"status": "failed", "error": f"unknown team task {task_id}"}
        return {"status": "cancelled", "task_id": task_id, "record": record}

    def get_team_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """查一条队员任务的记录（没接 store 时返回 None）。"""
        store = self.team_store
        if store is None:
            return None
        try:
            return store.get_task(task_id)
        except KeyError:
            return None

    @observe("MultiAgentTeam.pipeline")
    def pipeline(self, stages: List[Role], seed: str, **kwargs) -> Dict[str, Any]:
        """Run a sequential pipeline: each stage's output feeds the next.

        Each stage picks the first available agent of that role and hands its
        output to the following stage. Returns per-stage results and the final
        output.
        """
        current = seed
        stage_results: List[Dict[str, Any]] = []

        for role in stages:
            target = self._first_available(role)
            if target is None:
                return {
                    "status": "failed",
                    "error": f"no available {role.value} agent",
                    "stages": stage_results,
                }
            worker = self.members[target]
            result = worker.agent.execute(current, **kwargs)
            stage_results.append({
                "role": role.value,
                "agent_id": target,
                "status": result.get("status"),
                "result": result.get("result"),
            })
            if result.get("status") != "completed":
                return {
                    "status": "failed",
                    "error": f"stage {role.value} failed",
                    "stages": stage_results,
                }
            current = result.get("result", "")

        return {"status": "completed", "stages": stage_results, "final": current}

    def research(self, query: str, **kwargs) -> Dict[str, Any]:
        """查资料员执行链路（A3r §3）：检索 → 核实检查点 → 结构化回答。

        直接走 :func:`src.ai.web_research.answer_with_verification`：
        有来源才说，没来源标“没来源”，查不到老实说查不到。返回里带
        ``researcher``（队里有 researcher 成员时记它的 id，否则为空），
        供调用方归因；回答本身不经过模型补写。
        """
        from .web_research import answer_with_verification

        answer = answer_with_verification(query, **kwargs)
        researchers = self.member_ids(Role.RESEARCHER)
        answer["researcher"] = researchers[0] if researchers else ""
        return answer

    def discuss(self, statements: List[Dict[str, Any]]) -> Dict[str, Any]:
        """群聊讨论汇总（A3r §6）：Bot 们互相反驳，最后贾维斯汇总。

        ``statements`` 是各 Bot 的说法（``{bot, claim, subject?, value?,
        sources?}``）；汇总只认真实冲突（见
        :func:`src.ai.reasoned_reply.summarize_group_discussion`），
        没有真实冲突就不反驳、不演假辩论。
        """
        from .reasoned_reply import summarize_group_discussion

        summary = summarize_group_discussion(statements)
        summary["team"] = self.name
        return summary

    @observe("MultiAgentTeam.broadcast")
    def broadcast(self, sender_id: str, kind: MessageKind, payload: Dict[str, Any]) -> str:
        """Broadcast a message to every member from ``sender_id``."""
        sender = self.members.get(sender_id)
        if sender is None:
            return ""
        return sender.send("*", kind, payload)
