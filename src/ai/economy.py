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
from typing import Dict, Optional, Union

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
    the simple float form, and input/output split for the dict form. Unknown
    models are reported honestly (cost 0, unknown_model=True).
    """

    def __init__(self, price_table: PriceTable):
        self._price_table: PriceTable = dict(price_table)
        self._usage: Dict[str, Dict[str, float]] = {}

    # --- pricing (shared by record + estimate) --------------------------------
    def _price(self, model: str, tokens_in: int, tokens_out: int):
        entry = self._price_table.get(model)
        if entry is None:
            return 0.0, True
        if isinstance(entry, dict):
            in_p = float(entry.get("input", 0.0))
            out_p = float(entry.get("output", 0.0))
            cost = (tokens_in / 1000.0) * in_p + (tokens_out / 1000.0) * out_p
            return cost, False
        # float: cost per 1K tokens over total tokens
        price = float(entry)
        cost = ((tokens_in + tokens_out) / 1000.0) * price
        return cost, False

    def estimate_cost(self, model: str, tokens_in: int, tokens_out: int) -> float:
        """Pure cost estimate - no accounting side effects."""
        cost, _ = self._price(model, tokens_in, tokens_out)
        return cost

    @observe("billing.record_usage")
    def record_usage(self, model: str, tokens_in: int, tokens_out: int) -> Dict[str, Union[float, str, bool]]:
        """Record one usage event, aggregate it, and return its cost breakdown."""
        cost, unknown = self._price(model, tokens_in, tokens_out)
        agg = self._usage.setdefault(
            model, {"tokens_in": 0.0, "tokens_out": 0.0, "total_tokens": 0.0, "cost": 0.0}
        )
        agg["tokens_in"] += tokens_in
        agg["tokens_out"] += tokens_out
        agg["total_tokens"] += tokens_in + tokens_out
        agg["cost"] += cost

        return {
            "model": model,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost": cost,
            "unknown_model": unknown,
        }

    def usage_report(self) -> Dict[str, object]:
        models = {
            m: {
                "tokens_in": agg["tokens_in"],
                "tokens_out": agg["tokens_out"],
                "total_tokens": agg["total_tokens"],
                "cost": round(agg["cost"], 6),
            }
            for m, agg in self._usage.items()
        }
        total_tokens = sum(a["total_tokens"] for a in self._usage.values())
        total_cost = sum(a["cost"] for a in self._usage.values())
        return {
            "models": models,
            "total_tokens": total_tokens,
            "total_cost": round(total_cost, 6),
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
        }
