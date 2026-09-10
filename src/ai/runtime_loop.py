"""Agent Runtime Loop — 持续运行的事件循环（Phase 3 深化）。

把已有的四个构件串成一个真实运行的控制流，让 agent 从"一次性 execute"
变成"持续运行的 runtime"：

    AgentRuntimeService  (生命周期：start/pause/resume/stop/cancel/execute)
      L-- MessageBus      (消息泵：register/send/poll/pending)
      L-- AgentPolicy     (授权：每个动作先过 Policy 引擎)
      L-- AgentMemory     (记忆：执行结果按 agent 归因落库)

循环语义（每轮 ``step()``）：
    1. poll 一条消息（无消息则本轮为空）
    2. 授权 —— ``AgentPolicy.authorize``，被拒则**不执行**、回复 denied
    3. 执行 —— ``Agent.execute``（真实 provider 调用）
    4. 记记忆 —— 结果写入 agent-scoped Memory Kernel
    5. 回复 —— 一条 RESULT 消息回给发送者（``in_reply_to`` 链）

``run()`` 持续 step 直到：无消息（idle）、达到 ``max_steps``、或 agent 被
stop/cancel。诚实（NO-FAKE）：本实现是**同步消息排空循环**；"挂起等待新消息"
的异步持续泵需要线程/事件循环，是后续扩展，此处不伪装成已实现。

复用而非重写：``AgentRuntimeService``/``AgentPolicy``/``AgentMemory`` 来自
``agent_factory.py``，``MessageBus``/``AgentMessage``/``MessageKind`` 来自
``collaboration.py``，``Agent``/``AgentStatus`` 来自 ``employee.py``。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .agent_factory import AgentMemory, AgentPolicy, AgentRuntimeService
from .collaboration import AgentMessage, MessageBus, MessageKind
from .employee import AgentStatus
from .observability import observe, get_logger


@dataclass
class RuntimeLoop:
    """持续运行的 agent 控制流：poll → authorize → execute → remember → reply.

    Args:
        runtime: 生命周期控制器（包装 ``Agent``）。
        bus: 共享消息总线。
        policy: agent 作用域的授权绑定。
        memory: agent 作用域的记忆绑定。
    """

    runtime: AgentRuntimeService
    bus: MessageBus
    policy: AgentPolicy
    memory: AgentMemory
    _handled: List[Dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        # 预建邮箱，确保定向/broadcast 消息都能到达。
        self.bus.register(self.runtime.agent.id)
        self._log = get_logger("runtime_loop")

    @property
    def agent_id(self) -> str:
        return self.runtime.agent.id

    # --- 输入 -----------------------------------------------------------------
    def dispatch(
        self,
        from_agent: str,
        kind: MessageKind,
        payload: Dict[str, Any],
        in_reply_to: Optional[str] = None,
    ) -> str:
        """从外部给本 agent 投递一条消息，返回消息 id。"""
        msg = AgentMessage(
            id=f"msg_{uuid.uuid4().hex[:12]}",
            sender=from_agent,
            receiver=self.agent_id,
            kind=kind,
            payload=dict(payload),
            in_reply_to=in_reply_to,
        )
        self.bus.send(msg)
        return msg.id

    # --- 单步 -----------------------------------------------------------------
    @observe("RuntimeLoop.step")
    def step(self) -> Optional[Dict[str, Any]]:
        """处理一条消息（一轮循环）。无消息返回 ``None``。

        Returns:
            ``{"message_id", "kind", "status"}`` —— status 为 ``denied`` /
            ``completed`` / ``failed``。
        """
        agent_id = self.agent_id
        msg = self.bus.poll_one(agent_id)
        if msg is None:
            return None

        # 1. 授权 —— 每个消息动作先过 Policy 引擎。
        decision = self.policy.authorize(action=f"msg:{msg.kind.value}", risk_level="LOW")
        if not decision.is_allowed:
            self._reply(
                msg,
                payload={
                    "status": "denied",
                    "denied_rule_count": len(decision.denied_rules),
                },
            )
            handled: Dict[str, Any] = {
                "message_id": msg.id,
                "kind": msg.kind.value,
                "status": "denied",
            }
        else:
            # 2. 执行 —— 真实 provider 调用（task 从 payload 提取，其余作 kwargs）。
            payload = dict(msg.payload)
            task = str(payload.pop("task", msg.kind.value))
            result = self.runtime.execute(task=task, **payload)

            # 3. 记记忆 —— 执行结果按 agent 归因落库。
            self.memory.store(key=f"task:{msg.id}", value=result)

            # 4. 回复 —— 结果回给发送者。
            self._reply(msg, payload=result)

            handled = {
                "message_id": msg.id,
                "kind": msg.kind.value,
                "status": result.get("status"),
            }

        # 执行会把 agent 短暂置为 COMPLETED/FAILED；循环中回到 RUNNING 继续。
        self.runtime.agent.status = AgentStatus.RUNNING
        self._handled.append(handled)
        return handled

    # --- 循环 -----------------------------------------------------------------
    @observe("RuntimeLoop.run")
    def run(
        self,
        max_steps: Optional[int] = None,
        stop_when_idle: bool = True,
    ) -> Dict[str, Any]:
        """持续 step 直到停止条件满足。

        Returns:
            ``{"steps", "handled", "pending", "status"}``。
        """
        self.runtime.start()
        steps = 0
        while True:
            if self.runtime.agent.status == AgentStatus.STOPPED:
                break
            if max_steps is not None and steps >= max_steps:
                break
            handled = self.step()
            if handled is None:
                # 同步循环里无消息即停（挂起等待需异步泵，非本层职责）。
                if stop_when_idle:
                    break
                break
            steps += 1

        return {
            "steps": steps,
            "handled": list(self._handled),
            "pending": self.bus.pending(self.agent_id),
            "status": self.runtime.agent.status.value,
        }

    # --- 内部 -----------------------------------------------------------------
    def _reply(self, to: AgentMessage, payload: Dict[str, Any]) -> str:
        reply = AgentMessage(
            id=f"msg_{uuid.uuid4().hex[:12]}",
            sender=self.agent_id,
            receiver=to.sender,
            kind=MessageKind.RESULT,
            payload=payload,
            in_reply_to=to.id,
        )
        self.bus.send(reply)
        return reply.id
