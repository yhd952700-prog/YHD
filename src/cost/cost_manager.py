"""Cost management module for Phase 6 SRE."""

import time
from dataclasses import dataclass, field
from typing import Dict, Any
from enum import Enum


class Provider(Enum):
    """LLM providers for cost tracking."""
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GOOGLE = "google"
    SELF_HOST = "self_host"
    MOCK = "mock"


@dataclass
class UsageRecord:
    """A usage record for cost tracking."""
    provider: Provider
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    timestamp: float = field(default_factory=lambda: time.time())


@dataclass
class BudgetPolicy:
    """A budget policy for cost control."""
    name: str
    max_cost: float = 100.0
    max_tokens: int = 100000
    provider: Provider = Provider.OPENAI
    period: str = "monthly"  # daily, weekly, monthly

    def check_budget(self, cost: float) -> bool:
        """Check if cost is within budget."""
        return cost <= self.max_cost


@dataclass
class CostManager:
    """Tracks provider usage, budget policies, and rate-limit throttling."""

    budget_policies: Dict[str, BudgetPolicy] = field(default_factory=dict)
    usage_history: list = field(default_factory=list)
    total_cost: float = 0.0

    def track(self, record: UsageRecord) -> None:
        """Track usage and update total cost."""
        self.usage_history.append(record)

        # Calculate cost (simple pricing model)
        provider_pricing = {
            Provider.OPENAI: 0.002,  # $0.002 per 1K tokens
            Provider.ANTHROPIC: 0.003,
            Provider.GOOGLE: 0.0025,
        }

        pricing = provider_pricing.get(record.provider, 0.002)
        token_cost = (record.input_tokens + record.output_tokens) * pricing / 1000
        self.total_cost += token_cost

    def usage_by_model(self) -> Dict[str, Dict[str, Any]]:
        """Return usage and cost broken down by model.

        B组第二轮补：此前只有总量（``total_cost``）和按 provider 的预算
        统计，没有按模型明细。每个模型返回 input/output/total tokens 与
        按 ``track()`` 同一套 provider 定价算出的 ``cost_usd``；没有用量
        记录的模型不出现（不编零行）。
        """
        pricing = {
            Provider.OPENAI: 0.002,  # $0.002 per 1K tokens
            Provider.ANTHROPIC: 0.003,
            Provider.GOOGLE: 0.0025,
        }
        per_model: Dict[str, Dict[str, Any]] = {}
        for record in self.usage_history:
            entry = per_model.setdefault(
                record.model,
                {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cost_usd": 0.0},
            )
            entry["input_tokens"] += record.input_tokens
            entry["output_tokens"] += record.output_tokens
            entry["total_tokens"] += record.input_tokens + record.output_tokens
            entry["cost_usd"] += (
                (record.input_tokens + record.output_tokens)
                * pricing.get(record.provider, 0.002)
                / 1000
            )
        for entry in per_model.values():
            entry["cost_usd"] = round(entry["cost_usd"], 6)
        return per_model

    def apply_budget_policy(self, policy_name: str) -> bool:
        """Apply a budget policy and return if within budget."""
        policy = self.budget_policies.get(policy_name)
        if policy is None:
            return True

        # Find recent cost for this policy's provider
        recent_cost = sum(
            (u.input_tokens + u.output_tokens) * 0.002 / 1000
            for u in self.usage_history
            if u.provider == policy.provider
        )

        return policy.check_budget(self.total_cost + recent_cost)

    def budget_status(self, policy_name: str) -> Dict[str, Any]:
        """Get budget status for a policy."""
        policy = self.budget_policies.get(policy_name)
        if policy is None:
            return {"status": "no_policy", "max_cost": 0, "current_cost": self.total_cost}

        return {
            "status": "within_budget" if self.total_cost <= policy.max_cost else "over_budget",
            "max_cost": policy.max_cost,
            "current_cost": self.total_cost,
            "provider": policy.provider.value,
            "period": policy.period,
        }
