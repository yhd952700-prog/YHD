"""
Economy layer for LiuHao AI OS (MASTER-SPEC 74-76).

§74 Resource Kernel (quotas)  - hard resource quotas live in src.kernels.resource.
§75 Budget Engine          - reserve / consume / release, with block/unblock freeze.
§76 Billing Engine         - per-model token cost accounting + usage reports.

This module builds the *economy* abstraction on top of those kernels:
- BudgetEngine owns a soft $-budget with explicit two-phase reservation:
      reserve() holds money but does NOT charge it;
      consume() converts a reservation into a real charge;
      release() cancels a reservation.
  This mirrors ResourceQuotaManager.allocate()/commit()/release() semantics but is
  a self-contained, dependency-free budget (no Redis / no global singleton) so the
  economy layer can be unit-tested and composed freely.
- BillingEngine computes cost as total_tokens / 1000 * price_per_1k (a
  self-contained pricing model; no Redis, no global singleton).
- EconomyEngine composes BudgetEngine + BillingEngine: reserve the estimated cost,
  record real usage, then consume. Over-budget or blocked => rejected, never faked.

NO FAKE AI: all arithmetic is real; reservations are real; rejections are real.
"""

from __future__ import annotations

import uuid
from typing import Dict, List, Optional, Union

from .observability import observe

# Pricing entry: a float means "cost per 1K tokens (input+output)"; a dict
# {"input": x, "output": y} gives separate input/output prices per 1K tokens.
PriceEntry = Union[float, Dict[str, float]]
PriceTable = Dict[str, PriceEntry]


class BudgetEngine:
    """§75 Budget Engine - two-phase reservation + block freeze.

    Invariant: remaining() == total - consumed. Reserved money is "promised" but
    not yet charged, so reserve() does NOT change remaining(); only consume() does.
    """

    def __init__(self, total: float):
        if total < 0:
            raise ValueError("budget total must be >= 0")
        self._total = float(total)
        self._consumed = 0.0
        self._reserved: Dict[str, float] = {}
        self._blocked = False

    # --- reservation lifecycle ------------------------------------------------
    @observe("budget.reserve")
    def reserve(self, amount: float) -> Optional[str]:
        """Hold `amount` of budget. Returns a reservation id, or None if refused.

        Refused when: engine is blocked, amount is not positive, or there is not
        enough free budget (total - consumed - reserved_sum).
        """
        if self._blocked:
            return None
        if amount is None or amount <= 0:
            return None
        reserved_sum = sum(self._reserved.values())
        available = self._total - self._consumed - reserved_sum
        if amount > available:
            return None
        rid = f"res-{uuid.uuid4().hex[:12]}"
        self._reserved[rid] = float(amount)
        return rid

    def consume(self, reservation_id: str) -> bool:
        """Convert a reservation into a real charge. Returns True on success."""
        if reservation_id not in self._reserved:
            return False
        if self._blocked:
            # frozen: cannot finalize charges while blocked (reservation kept)
            return False
        amount = self._reserved.pop(reservation_id)
        self._consumed += amount
        return True

    def release(self, reservation_id: str) -> bool:
        """Cancel a reservation, returning the held money. True if it existed."""
        if reservation_id not in self._reserved:
            return False
        del self._reserved[reservation_id]
        return True

    # --- freeze ----------------------------------------------------------------
    def block(self) -> None:
        """Freeze all consumption (reserve/consume refused until unblock)."""
        self._blocked = True

    def unblock(self) -> None:
        self._blocked = False

    @property
    def blocked(self) -> bool:
        return self._blocked

    # --- queries ---------------------------------------------------------------
    def remaining(self) -> float:
        """Money not yet charged (reserved money is still available to charge)."""
        return self._total - self._consumed

    def reserved_total(self) -> float:
        return sum(self._reserved.values())

    def usage_report(self) -> Dict[str, float]:
        return {
            "total": self._total,
            "reserved": self.reserved_total(),
            "consumed": self._consumed,
            "remaining": self.remaining(),
            "blocked": 1.0 if self._blocked else 0.0,
        }


class BillingEngine:
    """§76 Billing Engine - per-model token cost accounting.

    Pricing arithmetic: cost = (tokens_in + tokens_out) / 1000 * price_per_1k for
    the simple float form, and input/output split for the dict form.

    未知价格模型**不按 0 元入账**：成本返回 ``None``，报表里明确标记
    ``pricing_status="unknown"`` / ``暂无价格``，并且 ``total_cost`` 只汇总
    已知模型；``total_cost_complete=False`` 告诉调用方"这个合计不是全部"。
    token 用量仍然照记（用量是真实的，只是价格未知）。
    """

    UNKNOWN_COST_MESSAGE = "该模型暂无价格数据，未计入成本"

    def __init__(self, price_table: PriceTable):
        self._price_table: PriceTable = dict(price_table)
        self._usage: Dict[str, Dict[str, float]] = {}

    # --- pricing (shared by record + estimate) --------------------------------
    def _price(self, model: str, tokens_in: int, tokens_out: int):
        entry = self._price_table.get(model)
        if entry is None:
            return None, True
        if isinstance(entry, dict):
            in_p = float(entry.get("input", 0.0))
            out_p = float(entry.get("output", 0.0))
            cost = (tokens_in / 1000.0) * in_p + (tokens_out / 1000.0) * out_p
            return cost, False
        # float: cost per 1K tokens over total tokens
        price = float(entry)
        cost = ((tokens_in + tokens_out) / 1000.0) * price
        return cost, False

    def estimate_cost(
        self, model: str, tokens_in: int, tokens_out: int
    ) -> Optional[float]:
        """Pure cost estimate - no accounting side effects.

        未知价格返回 ``None``（**不是 0.0**）：0 元会被误读成"免费"。
        """
        cost, _ = self._price(model, tokens_in, tokens_out)
        return cost

    def has_price(self, model: str) -> bool:
        """Whether this model has a price entry."""
        return model in self._price_table

    @observe("billing.record_usage")
    def record_usage(
        self, model: str, tokens_in: int, tokens_out: int
    ) -> Dict[str, Union[float, str, bool, None]]:
        """Record one usage event, aggregate it, and return its cost breakdown."""
        cost, unknown = self._price(model, tokens_in, tokens_out)
        agg = self._usage.setdefault(
            model,
            {
                "tokens_in": 0.0,
                "tokens_out": 0.0,
                "total_tokens": 0.0,
                "cost": 0.0,
                "unknown": 0.0,
            },
        )
        agg["tokens_in"] += tokens_in
        agg["tokens_out"] += tokens_out
        agg["total_tokens"] += tokens_in + tokens_out
        if unknown:
            agg["unknown"] = 1.0
        else:
            agg["cost"] += float(cost or 0.0)

        return {
            "model": model,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            # 未知价格明确返回 None，而不是 0.0
            "cost": None if unknown else cost,
            "unknown_model": unknown,
            "pricing_status": "unknown" if unknown else "priced",
            "message": self.UNKNOWN_COST_MESSAGE if unknown else "",
        }

    def usage_report(self) -> Dict[str, object]:
        models: Dict[str, Dict[str, object]] = {}
        unknown_models: List[str] = []
        for model, agg in self._usage.items():
            unknown = bool(agg.get("unknown"))
            if unknown:
                unknown_models.append(model)
            models[model] = {
                "tokens_in": agg["tokens_in"],
                "tokens_out": agg["tokens_out"],
                "total_tokens": agg["total_tokens"],
                "cost": None if unknown else round(agg["cost"], 6),
                "pricing_status": "unknown" if unknown else "priced",
                "message": self.UNKNOWN_COST_MESSAGE if unknown else "",
            }
        total_tokens = sum(a["total_tokens"] for a in self._usage.values())
        # 合计只含已知价格模型；未知模型以 None 展示，绝不当 0 元参与合计。
        total_cost = sum(
            agg["cost"] for agg in self._usage.values() if not agg.get("unknown")
        )
        return {
            "models": models,
            "total_tokens": total_tokens,
            "total_cost": round(total_cost, 6),
            "total_cost_complete": not unknown_models,
            "unknown_models": unknown_models,
            "message": self.UNKNOWN_COST_MESSAGE if unknown_models else "",
            "model_count": len(self._usage),
        }


class EconomyEngine:
    """§74-76 composition: budget control + billing in one execution path."""

    def __init__(self, budget: BudgetEngine, billing: BillingEngine):
        self._budget = budget
        self._billing = billing

    @observe("economy.execute")
    def execute(self, model: str, tokens_in: int, tokens_out: int) -> Dict[str, object]:
        """Run one model call through budget + billing.

        Flow: estimate cost -> reserve -> record usage -> consume the reservation.
        Returns ok=True with the realized cost, or ok=False when blocked /
        over-budget (reservation refused). Nothing is faked.
        """
        if self._budget.blocked:
            return {"ok": False, "status": "blocked", "model": model}

        estimated = self._billing.estimate_cost(model, tokens_in, tokens_out)
        if estimated is None:
            # 未知价格：不预留、不扣费（0 元会骗过预算闸门），但用量照记，
            # 并把"暂无价格"明确返回给调用方。
            record = self._billing.record_usage(model, tokens_in, tokens_out)
            return {
                "ok": True,
                "status": "ok",
                "model": model,
                "cost": None,
                "estimated_cost": None,
                "unknown_model": True,
                "pricing_status": "unknown",
                "message": self._billing.UNKNOWN_COST_MESSAGE,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
            }

        rid = self._budget.reserve(estimated)
        if rid is None:
            return {
                "ok": False,
                "status": "over_budget",
                "model": model,
                "estimated_cost": estimated,
            }

        record = self._billing.record_usage(model, tokens_in, tokens_out)
        self._budget.consume(rid)
        return {
            "ok": True,
            "status": "ok",
            "model": model,
            "cost": record["cost"],
            "estimated_cost": estimated,
            "unknown_model": record["unknown_model"],
            "pricing_status": record["pricing_status"],
        }
