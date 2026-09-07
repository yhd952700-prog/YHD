"""Runtime Loop — 持续运行控制流（Phase 3 深化）测试。

覆盖 ``RuntimeLoop`` 的闭环语义：poll → authorize → execute → remember → reply。
复用 ``AgentRuntimeService`` / ``AgentPolicy`` / ``AgentMemory`` / ``MessageBus``，
验证（NO-FAKE）：

- 空总线 ``step()`` 返回 ``None``（idle）
- 默认拒绝：无 allow 规则时消息被拒、不执行 provider、不落记忆、回复 denied
- 允许路径：有 allow 规则时真实执行、结果按 agent 归因落库、回复 RESULT
- ``run()`` 排空总线并返回正确 summary
- ``run()`` 尊重 ``max_steps``
- agent 已停止时 ``run()`` 立即返回
"""

import uuid

from src.ai.employee import Agent, AgentStatus
from src.ai.agent_factory import AgentRuntimeService, AgentMemory, AgentPolicy
from src.ai.collaboration import MessageBus, MessageKind
from src.ai.runtime_loop import RuntimeLoop
from src.kernels.policy import (
    define_policy_rule,
    PolicyAction,
    PolicyScope,
    get_policy_engine,
)


class _StubProvider:
    """确定性 provider：回显任务，便于断言真实执行发生。"""

    def generate_with_retry(self, prompt, **kwargs):
        return f"echo:{prompt}"


def _make_loop(principal=None, capabilities=None):
    """手工组装 RuntimeLoop（绕过 AgentFactory，避免身份噪声）。"""
    principal = principal or f"rt-{uuid.uuid4().hex[:8]}"
    agent = Agent(
        id=principal,
        agent_type="worker",
        name=principal,
        provider=_StubProvider(),
    )
    runtime = AgentRuntimeService(agent=agent)
    bus = MessageBus()
    policy = AgentPolicy(principal=principal, capabilities=list(capabilities or []), scope="L1")
    memory = AgentMemory(principal=principal)
    loop = RuntimeLoop(runtime=runtime, bus=bus, policy=policy, memory=memory)
    return loop, agent, bus, memory, principal


def _allow_msg(action_name: str) -> str:
    """注册一条只放行指定消息动作的 ALLOW 规则，返回 rule_id 供清理。"""
    rule_id = f"allow_msg_{uuid.uuid4().hex[:8]}"
    define_policy_rule(
        id=rule_id,
        name=rule_id,
        description="test allow for message action",
        conditions=[{"attribute": "action.name", "operator": "eq", "value": action_name}],
        action=PolicyAction.ALLOW,
        scope=PolicyScope.L0,
        precedence=10,
    )
    return rule_id


class TestRuntimeLoopStep:
    def test_idle_bus_step_returns_none(self):
        loop, _, _, _, _ = _make_loop()
        assert loop.step() is None

    def test_denied_message_no_execute_no_memory(self):
        loop, agent, bus, memory, _ = _make_loop()
        msg_id = loop.dispatch("alice", MessageKind.ASK, {"task": "secret work"})
        handled = loop.step()

        assert handled is not None
        assert handled["message_id"] == msg_id
        assert handled["kind"] == "ask"
        assert handled["status"] == "denied"
        # 被拒不执行：provider 从未被调用
        assert agent.completed_tasks == 0
        # 被拒不落记忆
        assert memory.recall(f"task:{msg_id}") is None
        # 回复一条 denied 给发送者，带 in_reply_to 链
        replies = bus.poll("alice")
        assert len(replies) == 1
        assert replies[0].kind == MessageKind.RESULT
        assert replies[0].payload["status"] == "denied"
        assert replies[0].in_reply_to == msg_id

    def test_allowed_message_executes_stores_replies(self):
        loop, agent, bus, memory, _ = _make_loop()
        rule_id = _allow_msg("msg:ask")
        try:
            msg_id = loop.dispatch("bob", MessageKind.ASK, {"task": "do work"})
            handled = loop.step()
        finally:
            get_policy_engine().unregister_rule(rule_id)

        assert handled["status"] == "completed"
        assert agent.completed_tasks == 1
        # 结果按 agent 归因落库（task:<msg_id> 键）
        entry = memory.recall(f"task:{msg_id}")
        assert entry is not None
        assert entry.value["status"] == "completed"
        assert entry.value["result"] == "echo:do work"
        assert f"agent:{loop.agent_id}" in entry.tags
        # 回复结果给发送者
        replies = bus.poll("bob")
        assert len(replies) == 1
        assert replies[0].kind == MessageKind.RESULT
        assert replies[0].payload["status"] == "completed"

    def test_step_restores_running_status(self):
        loop, agent, _, _, _ = _make_loop()
        rule_id = _allow_msg("msg:ask")
        try:
            loop.dispatch("c", MessageKind.ASK, {"task": "t"})
            loop.step()
        finally:
            get_policy_engine().unregister_rule(rule_id)
        # execute 会把 agent 短暂置为 COMPLETED；循环里应回到 RUNNING 继续
        assert agent.status == AgentStatus.RUNNING


class TestRuntimeLoopRun:
    def test_run_drains_bus_and_reports_summary(self):
        loop, _, bus, _, _ = _make_loop()
        rule_id = _allow_msg("msg:ask")
        try:
            for i in range(3):
                loop.dispatch("d", MessageKind.ASK, {"task": f"task-{i}"})
            summary = loop.run()
        finally:
            get_policy_engine().unregister_rule(rule_id)

        assert summary["steps"] == 3
        assert summary["pending"] == 0
        assert len(summary["handled"]) == 3
        assert summary["status"] == "running"
        assert all(h["status"] == "completed" for h in summary["handled"])

    def test_run_respects_max_steps(self):
        loop, _, _, _, _ = _make_loop()
        rule_id = _allow_msg("msg:ask")
        try:
            for i in range(5):
                loop.dispatch("e", MessageKind.ASK, {"task": f"task-{i}"})
            summary = loop.run(max_steps=2)
        finally:
            get_policy_engine().unregister_rule(rule_id)

        assert summary["steps"] == 2
        assert summary["pending"] == 3  # 剩余 3 条未处理

    def test_run_immediately_stops_when_agent_stopped(self):
        loop, agent, _, _, _ = _make_loop()
        agent.status = AgentStatus.STOPPED
        summary = loop.run()
        assert summary["steps"] == 0
        assert summary["status"] == "stopped"
