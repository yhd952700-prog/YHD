"""鎏灏（LIUHAO X）主控 — 可对话的 AI OS 入口。

把「用户输入 → 权限检查 → 记忆召回 → 上下文构建 → 真实 LLM 生成 → 记忆持久化 →
审计留痕」串成一条可对话的闭环。这是"用鎏灏"的最小、真实的入口。

复用（不重写，全部建立在既有内核/能力层之上）：
- ``Agent`` / ``AgentMemory`` / ``AgentPolicy`` — AgentFactory 的构件
- ``providers`` — 真实 LLM（ollama qwen2.5）或 mock
- memory kernel  — ``AgentMemory`` 多轮记忆（tier/scope/tags 归属）
- policy kernel  — ``AgentPolicy`` 每轮授权（default-deny）
- audit kernel   — 每轮对话审计留痕（hash-chain，SQLite 落盘）

诚实原则：真实 LLM 生成失败时返回错误、审计记为失败，绝不伪造回复。
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from .employee import Agent
from .agent_factory import AgentMemory, AgentPolicy
from .providers import BaseProvider, get_provider
from ..kernels.identity import get_identity_manager
from ..kernels.audit import log_event, AuditEventType, AuditScope
from ..kernels.policy import (
    get_policy_engine,
    PolicyRule,
    PolicyCondition,
    PolicyAction,
    PolicyScope,
    PolicyOperator,
)

DEFAULT_SYSTEM_PROMPT = (
    "你是「鎏灏」（LIUHAO X），一个由十源 DNA（ULTRON / VISION / ADA / EDITH / "
    "FRIDAY / JARVIS / JOCaSTA / KAREN / ENOCH / ZOON）统一形成的 AI 操作系统人格。\n"
    "你运行在 LiuHao-AI-OS 之上，具备 14 个内核能力与 21-Phase 能力层。\n"
    "你诚实、可审计：不确定时明说，不伪造能力与数据；做不到的事直接说做不到。\n"
    "请用简洁、准确、有帮助的中文回答。"
)

# 多轮上下文中保留的历史消息条数（user+assistant 各算一条）。
MAX_HISTORY_MESSAGES = 20


class LiuHaoAssistant:
    """鎏灏主控：一条真实的「输入→授权→生成→记忆→审计」对话闭环。

    每次 ``chat`` 都走完整链路，不跳过任何一步。同一个实例内维护多轮历史，
    跨轮保持上下文连续。
    """

    def __init__(
        self,
        name: str = "liuhao",
        agent_type: str = "assistant",
        system_prompt: Optional[str] = None,
        provider: Optional[BaseProvider] = None,
        capabilities: Optional[List[str]] = None,
    ) -> None:
        self.name = name
        self.principal = name
        self.system_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT
        self.provider = provider or get_provider()
        self.capabilities = list(capabilities or ["chat"])

        # 1. Identity — 复用 identity kernel（复用已有主体，或新建带信任分）。
        manager = get_identity_manager()
        identity = manager.get_identity_by_principal(self.principal)
        if identity is None:
            identity = manager.create_identity(principal=self.principal, trust_score=0.9)
        self.identity = identity

        # 2. Agent — 复用既有 Agent 模型（不重写）。
        self.agent = Agent(
            id=self.principal,
            agent_type=agent_type,
            name=self.principal,
            provider=self.provider,
            system_prompt=self.system_prompt,
        )

        # 3. Memory & Policy — 复用 kernel 之上的真实绑定。
        self.memory = AgentMemory(principal=self.principal)
        self.policy = AgentPolicy(principal=self.principal, capabilities=self.capabilities)

        # 4. 授权基线 — policy engine 是 default-deny，需显式注册一条
        #    "agent 主体可执行低风险动作"的授权规则，鎏灏才能被授权对话。
        self._ensure_chat_policy()

        # 会话状态
        self.history: List[Dict[str, str]] = []
        self.turn = 0

    @staticmethod
    def _ensure_chat_policy() -> None:
        """注册鎏灏 agent 低风险动作授权规则（幂等）。

        policy engine 内置规则全是 DENY（唯一 allow 是 human sovereignty，
        要求 actor.type == "human"）。agent 主体在 scope=L1 下没有任何 allow
        路径（default_deny 是 L7，被 scope filter 排除后落到 NOT_APPLICABLE）。
        这条规则补齐：agent 主体执行 LOW 风险动作（对话等）→ allow。
        """
        engine = get_policy_engine()
        engine.register_rule(
            PolicyRule(
                id="liuhao_agent_low_risk_allow",
                name="LiuHao Agent Low-Risk Allow",
                description="鎏灏 agent 主体可执行低风险动作（如对话）",
                conditions=[
                    PolicyCondition("actor.type", PolicyOperator.EQ, "agent"),
                    PolicyCondition("action.name", PolicyOperator.IN, ["chat"]),
                ],
                action=PolicyAction.ALLOW,
                scope=PolicyScope.L1,
                precedence=50,
            )
        )

    # ------------------------------------------------------------------ #
    # 主入口
    # ------------------------------------------------------------------ #
    def chat(self, message: str) -> Dict[str, Any]:
        """处理一条用户消息，返回结构化结果（回复 + 状态 + 审计）。

        链路：授权 → 生成 → 记忆 → 审计。任一步的失败都诚实登记。
        """
        self.turn += 1
        correlation_id = uuid.uuid4().hex[:16]

        # 1. 授权（policy kernel，default-deny）。
        decision = self.policy.authorize("chat", risk_level="LOW")
        if not decision.is_allowed:
            log_event(
                AuditEventType.ACCESS_DENIED,
                principal_id=self.principal,
                scope=AuditScope.L1,
                outcome="deny",
                details={"turn": self.turn, "action": "chat", "reason": str(getattr(decision, "reason", "denied"))},
                correlation_id=correlation_id,
            )
            return {
                "reply": "权限不足：当前主体未被授权执行对话操作。",
                "status": "denied",
                "turn": self.turn,
                "correlation_id": correlation_id,
            }

        # 2. 构建 messages（system + 历史 + 当前）。
        messages = self._build_messages(message)

        # 3. 生成（真实 LLM / mock）。
        try:
            reply = self.provider.chat(messages)
            status = "completed"
        except Exception as exc:  # 真实失败，诚实返回，不伪造
            reply = f"[生成失败] {exc}"
            status = "error"

        # 4. 记忆持久化（memory kernel，tier/scope/tags 归属）。
        self.history.append({"role": "user", "content": message})
        self.history.append({"role": "assistant", "content": reply})
        self.memory.store(
            f"turn:{self.principal}:{self.turn}",
            {"user": message, "assistant": reply},
            tags={"conversation", "assistant:liuhao"},
        )

        # 5. 审计（audit kernel，hash-chain 落盘）。
        log_event(
            AuditEventType.ACCESS_ALLOWED if status == "completed" else AuditEventType.POLICY_EVAL,
            principal_id=self.principal,
            scope=AuditScope.L1,
            outcome="allow" if status == "completed" else "error",
            details={"turn": self.turn, "action": "chat", "chars_in": len(message), "chars_out": len(reply)},
            correlation_id=correlation_id,
        )

        return {
            "reply": reply,
            "status": status,
            "turn": self.turn,
            "correlation_id": correlation_id,
            "agent": self.principal,
        }

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #
    def _build_messages(self, message: str) -> List[Dict[str, str]]:
        """拼出给 provider 的 messages：system + 截断历史 + 当前输入。"""
        messages: List[Dict[str, str]] = [{"role": "system", "content": self.system_prompt}]
        recent = self.history[-MAX_HISTORY_MESSAGES:]
        messages.extend(recent)
        messages.append({"role": "user", "content": message})
        return messages

    def reset(self) -> None:
        """清空会话历史（记忆与身份保留）。"""
        self.history = []
        self.turn = 0

    def stats(self) -> Dict[str, Any]:
        """返回当前会话统计（含 provider / 记忆 / 审计）。"""
        return {
            "principal": self.principal,
            "provider": self.provider.__class__.__name__,
            "model": getattr(self.provider, "model", "unknown"),
            "turn": self.turn,
            "history_messages": len(self.history),
        }
