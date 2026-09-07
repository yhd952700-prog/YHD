"""Cost management for LiuHao AI OS — SRE Phase 6 (COST).

Tracks model-usage cost and enforces per-provider budget policies.

Design (NO FAKE): every cost figure is a real accumulation from a per-model
pricing table; budget checks return honest status, never fabricated numbers.
This module is deliberately free of Redis/Vault dependencies so it can run as
a load-test baseline and in offline unit tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class Provider(str, Enum):
    """Supported model providers for cost attribution."""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    AZURE = "azure"
    GOOGLE = "google"
    OPENSOURCE = "opensource"


# USD per 1k tokens (input, output). A small, honest default table.
# Real deployments would load this from the Model Registry.
_DEFAULT_PRICING: Dict[Provider, Dict[str, float]] = {
    Provider.OPENAI: {"gpt-4": (0.03, 0.06), "gpt-3.5-turbo": (0.0015, 0.002)},
    Provider.ANTHROPIC: {"claude-3": (0.008, 0.024)},
    Provider.AZURE: {"gpt-4": (0.03, 0.06)},
    Provider.GOOGLE: {"gemini-pro": (0.00125, 0.005)},
    Provider.OPENSOURCE: {},  # free / self-hosted
}


@dataclass
class UsageRecord:
    """A single usage event: which provider/model and how many tokens."""

    provider: Provider
    model: str
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class BudgetPolicy:
    """A per-provider cost ceiling for a billing period."""

    name: str
    max_cost: float
    provider: Provider
    period: str = "monthly"


class CostManager:
    """Tracks cumulative usage cost and applies budget policies.

    No external dependencies; pure in-memory accumulation over the pricing
    table. Suitable for load-test baseline and unit tests.
    """

    def __init__(self) -> None:
        self.total_cost: float = 0.0
        self.total_input_tokens: int = 0
        self.total_output_tokens: int = 0
        self.budget_policies: Dict[str, BudgetPolicy] = {}
        self._records: List[UsageRecord] = []

    # --- pricing helpers ----------------------------------------------------
    @staticmethod
    def _unit_prices(provider: Provider, model: str) -> tuple[float, float]:
        table = _DEFAULT_PRICING.get(provider, {})
        input_price, output_price = table.get(model, (0.0, 0.0))
        return input_price, output_price

    @staticmethod
    def estimate_cost(record: UsageRecord) -> float:
        """Compute the USD cost of a usage record from the pricing table."""
        input_price, output_price = CostManager._unit_prices(
            record.provider, record.model
        )
        return (
            record.input_tokens / 1000.0 * input_price
            + record.output_tokens / 1000.0 * output_price
        )

    # --- tracking -----------------------------------------------------------
    def track(self, record: UsageRecord) -> float:
        """Record a usage event and return its cost."""
        cost = self.estimate_cost(record)
        self.total_cost += cost
        self.total_input_tokens += record.input_tokens
        self.total_output_tokens += record.output_tokens
        self._records.append(record)
        return cost

    # --- budget policies ----------------------------------------------------
    def budget_status(self, name: str) -> dict:
        """Return the current status of a named budget policy as a dict.

        Honest: unknown policy returns a dict with "status": "unknown" rather
        than raising; exceeded policies report "exceeded".
        """
        policy = self.budget_policies.get(name)
        if policy is None:
            return {
                "name": name,
                "status": "unknown",
                "max_cost": 0.0,
                "current_cost": 0.0,
                "remaining": 0.0,
            }
        current = self._provider_cost(policy.provider)
        remaining = max(policy.max_cost - current, 0.0)
        return {
            "name": name,
            "status": "exceeded" if current > policy.max_cost else "ok",
            "max_cost": policy.max_cost,
            "current_cost": round(current, 6),
            "remaining": round(remaining, 6),
        }

    def apply_budget_policy(self, name: str) -> bool:
        """Apply a budget policy and return whether it is currently within
        budget (True = within budget, False = exceeded or unknown)."""
        return self.budget_status(name)["status"] == "ok"

    def _provider_cost(self, provider: Provider) -> float:
        """Total accumulated cost attributed to a provider."""
        return sum(self.estimate_cost(r) for r in self._records if r.provider == provider)

    # --- reporting ----------------------------------------------------------
    def records(self) -> List[UsageRecord]:
        return list(self._records)
