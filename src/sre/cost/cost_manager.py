"""Cost management for LiuHao AI OS — SRE Phase 6 (COST).

Tracks model-usage cost and enforces per-provider budget policies.

Design (NO FAKE): every cost figure is a real accumulation from a per-model
pricing table; budget checks return honest status, never fabricated numbers.
A model without price data is reported as **unknown cost** (``None``), never
as 0.0 — a "0" would read as "this was free" and silently understate the bill.
This module is deliberately free of Redis/Vault dependencies so it can run as
a load-test baseline and in offline unit tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Tuple


#: 未知成本的对外文案。金额未知 ≠ 0 元，不能静默按 0 计入统计。
UNKNOWN_COST_MESSAGE = "该模型暂无价格数据，未计入成本"


class Provider(str, Enum):
    """Supported model providers for cost attribution."""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    AZURE = "azure"
    GOOGLE = "google"
    OPENSOURCE = "opensource"


# USD per 1k tokens (input, output). A small, honest default table.
# Real deployments would load this from the Model Registry.
_DEFAULT_PRICING: Dict[Provider, Dict[str, Tuple[float, float]]] = {
    Provider.OPENAI: {"gpt-4": (0.03, 0.06), "gpt-3.5-turbo": (0.0015, 0.002)},
    Provider.ANTHROPIC: {"claude-3": (0.008, 0.024)},
    Provider.AZURE: {"gpt-4": (0.03, 0.06)},
    Provider.GOOGLE: {"gemini-pro": (0.00125, 0.005)},
    Provider.OPENSOURCE: {},  # 自托管：按 token 计费为 0，见 _FREE_PROVIDERS
}

#: 按 token 计费确实为 0 的 provider（自托管 / 免费）。这是已知状态，不是"未知价格"。
_FREE_PROVIDERS = frozenset({Provider.OPENSOURCE})


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
        self._unknown_records: List[UsageRecord] = []

    # --- pricing helpers ----------------------------------------------------
    @staticmethod
    def _unit_prices(provider: Provider, model: str) -> Optional[Tuple[float, float]]:
        """Return (input, output) USD per 1k tokens, or ``None`` if unpriced."""
        if provider in _FREE_PROVIDERS:
            return (0.0, 0.0)
        return _DEFAULT_PRICING.get(provider, {}).get(model)

    @staticmethod
    def estimate_cost(record: UsageRecord) -> Optional[float]:
        """Compute the USD cost of a usage record from the pricing table.

        Returns ``None`` when the model has no price data. ``None`` is a
        distinct state, not 0.0: callers must not add it to a total, and must
        not display it as "$0.00".
        """
        prices = CostManager._unit_prices(record.provider, record.model)
        if prices is None:
            return None
        input_price, output_price = prices
        return (
            record.input_tokens / 1000.0 * input_price
            + record.output_tokens / 1000.0 * output_price
        )

    # --- tracking -----------------------------------------------------------
    def track(self, record: UsageRecord) -> Optional[float]:
        """Record a usage event and return its cost.

        Returns ``None`` for an unpriced model. Such records are counted in
        ``unknown_cost_records`` and excluded from ``total_cost`` instead of
        being accumulated as 0.0.
        """
        cost = self.estimate_cost(record)
        if cost is None:
            self._unknown_records.append(record)
        else:
            self.total_cost += cost
        self.total_input_tokens += record.input_tokens
        self.total_output_tokens += record.output_tokens
        self._records.append(record)
        return cost

    @property
    def unknown_cost_records(self) -> List[UsageRecord]:
        """Usage records whose model has no price data (cost not summed)."""
        return list(self._unknown_records)

    def usage_by_model(self) -> Dict[str, dict]:
        """Return usage and cost broken down by model.

        B组第二轮补：此前只有总量（``cost_summary``）和按 provider 的预算
        状态，没有按模型明细。键为 ``"<provider>/<model>"``（同一模型名
        可能分属不同 provider，定价也不同，必须区分）。每个模型返回
        provider、input/output/total tokens 与 ``cost_usd``；无价格数据
        的模型 ``cost_usd`` 为 ``None``（与 ``estimate_cost`` 的约定一致，
        绝不按 0 计入），并在 ``cost_complete`` 为 False 时标明。
        """
        per_model: Dict[str, dict] = {}
        for record in self._records:
            key = f"{record.provider.value}/{record.model}"
            entry = per_model.setdefault(
                key,
                {
                    "provider": record.provider.value,
                    "model": record.model,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                    "cost_usd": 0.0,
                    "cost_complete": True,
                },
            )
            entry["input_tokens"] += record.input_tokens
            entry["output_tokens"] += record.output_tokens
            entry["total_tokens"] += record.input_tokens + record.output_tokens
            cost = self.estimate_cost(record)
            if cost is None:
                entry["cost_complete"] = False
                entry["cost_usd"] = None
            elif entry["cost_usd"] is not None:
                entry["cost_usd"] = round(entry["cost_usd"] + cost, 6)
        for entry in per_model.values():
            if entry["cost_usd"] is not None:
                entry["cost_usd"] = round(entry["cost_usd"], 6)
        return per_model

    def cost_summary(self) -> dict:
        """Report the accumulated cost and whether it is complete.

        ``total_cost`` only covers priced models; ``total_cost_complete`` is
        False when any usage could not be priced, and ``message`` tells the
        reader why the number must not be read as the full bill.
        """
        unknown = len(self._unknown_records)
        return {
            "total_cost": round(self.total_cost, 6),
            "total_cost_complete": unknown == 0,
            "unknown_cost_records": unknown,
            "message": "" if unknown == 0 else UNKNOWN_COST_MESSAGE,
        }

    # --- budget policies ----------------------------------------------------
    def budget_status(self, name: str) -> dict:
        """Return the current status of a named budget policy as a dict.

        Honest: unknown policy returns a dict with "status": "unknown" rather
        than raising; a policy whose provider has unpriced usage returns
        "status": "unknown_price" with ``current_cost``/``remaining`` set to
        ``None`` (never a partial sum that reads as a real budget figure).
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
        if current is None:
            return {
                "name": name,
                "status": "unknown_price",
                "max_cost": policy.max_cost,
                "current_cost": None,
                "remaining": None,
                "unknown_models": sorted(
                    {r.model for r in self._unknown_records if r.provider == policy.provider}
                ),
                "message": UNKNOWN_COST_MESSAGE,
            }
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
        budget (True = within budget, False = exceeded, unknown, or unpriced)."""
        return self.budget_status(name)["status"] == "ok"

    def _provider_cost(self, provider: Provider) -> Optional[float]:
        """Total accumulated cost attributed to a provider.

        ``None`` when any of that provider's records has no price data: a
        partial sum would understate the bill while still looking exact.
        """
        costs = [
            self.estimate_cost(r) for r in self._records if r.provider == provider
        ]
        if any(cost is None for cost in costs):
            return None
        return sum(costs)

    # --- reporting ----------------------------------------------------------
    def records(self) -> List[UsageRecord]:
        return list(self._records)
