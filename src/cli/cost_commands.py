"""Cost management CLI commands for LiuHao AI OS.

Provides command-line interface for cost tracking and budgeting:
- cost overview, cost breakdown
- cost daily, cost weekly, cost monthly
- cost budget, cost alerts
- cost forecast
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional


class CostCategory(str, Enum):
    """Cost categories."""
    AI_PROVIDER = "ai_provider"
    STORAGE = "storage"
    COMPUTE = "compute"
    NETWORK = "network"
    THIRD_PARTY = "third_party"


@dataclass
class BudgetAlert:
    """A budget alert configuration."""
    name: str
    threshold_pct: float
    triggered: bool = False
    triggered_at: Optional[str] = None


@dataclass
class Budget:
    """A budget configuration."""
    name: str
    amount: float
    period: str  # daily, weekly, monthly
    category: Optional[str] = None
    spent: float = 0.0
    alerts: List[BudgetAlert] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())


class CostManager:
    """In-memory cost manager for CLI operations.

    Provides cost tracking, budget management, and forecasting.
    """

    def __init__(self) -> None:
        self._cost_records: List[Dict[str, Any]] = []
        self._budgets: Dict[str, Budget] = {}
        self._seed_sample_data()

    def _seed_sample_data(self) -> None:
        """Seed with sample cost data for demonstration."""
        now = datetime.utcnow()
        for i in range(30):
            day = now - timedelta(days=i)
            for category in CostCategory:
                self._cost_records.append({
                    "date": day.strftime("%Y-%m-%d"),
                    "category": category.value,
                    "amount": round(10 + i * 0.5 + hash(category.value) % 5, 2),
                    "details": {},
                })

    def get_overview(self) -> Dict[str, Any]:
        """Get overall cost overview.

        Returns:
            Dict with total cost and summary.
        """
        total = sum(r["amount"] for r in self._cost_records)
        today = datetime.utcnow().strftime("%Y-%m-%d")
        today_cost = sum(r["amount"] for r in self._cost_records if r["date"] == today)
        by_category: Dict[str, float] = {}
        for r in self._cost_records:
            by_category[r["category"]] = by_category.get(r["category"], 0) + r["amount"]
        return {
            "total_cost": round(total, 2),
            "today_cost": round(today_cost, 2),
            "currency": "USD",
            "by_category": {k: round(v, 2) for k, v in by_category.items()},
            "record_count": len(self._cost_records),
        }

    def get_breakdown(
        self,
        category: Optional[str] = None,
        period: str = "monthly",
    ) -> Dict[str, Any]:
        """Get cost breakdown by category and period.

        Args:
            category: Optional category filter.
            period: Aggregation period (daily, weekly, monthly).

        Returns:
            Dict with cost breakdown.
        """
        records = self._cost_records
        if category:
            records = [r for r in records if r["category"] == category]

        # Group by period
        grouped: Dict[str, float] = {}
        for r in records:
            date_obj = datetime.strptime(r["date"], "%Y-%m-%d")
            if period == "daily":
                key = r["date"]
            elif period == "weekly":
                # ISO week
                key = f"{date_obj.isocalendar()[0]}-W{date_obj.isocalendar()[1]:02d}"
            elif period == "monthly":
                key = r["date"][:7]
            else:
                key = r["date"]
            grouped[key] = grouped.get(key, 0) + r["amount"]

        return {
            "period": period,
            "category": category or "all",
            "breakdown": {k: round(v, 2) for k, v in sorted(grouped.items())},
            "total": round(sum(grouped.values()), 2),
        }

    def get_daily_cost(self, days: int = 7) -> Dict[str, Any]:
        """Get daily cost for the last N days.

        Args:
            days: Number of days to include.

        Returns:
            Dict with daily cost data.
        """
        now = datetime.utcnow()
        daily: Dict[str, Dict[str, float]] = {}
        for i in range(days):
            day = (now - timedelta(days=i)).strftime("%Y-%m-%d")
            daily[day] = {}
            for cat in CostCategory:
                daily[day][cat.value] = 0.0
        for r in self._cost_records:
            if r["date"] in daily:
                daily[r["date"]][r["category"]] = round(
                    daily[r["date"]].get(r["category"], 0) + r["amount"], 2
                )
        return {
            "days": days,
            "daily": {k: v for k, v in sorted(daily.items(), reverse=True)},
        }

    def get_weekly_cost(self, weeks: int = 4) -> Dict[str, Any]:
        """Get weekly cost for the last N weeks.

        Args:
            weeks: Number of weeks to include.

        Returns:
            Dict with weekly cost data.
        """
        breakdown = self.get_breakdown(period="weekly")
        items = list(breakdown["breakdown"].items())[-weeks:]
        return {
            "weeks": len(items),
            "weekly": dict(items),
            "total": round(sum(v for _, v in items), 2),
        }

    def get_monthly_cost(self, months: int = 6) -> Dict[str, Any]:
        """Get monthly cost for the last N months.

        Args:
            months: Number of months to include.

        Returns:
            Dict with monthly cost data.
        """
        breakdown = self.get_breakdown(period="monthly")
        items = list(breakdown["breakdown"].items())[-months:]
        return {
            "months": len(items),
            "monthly": dict(items),
            "total": round(sum(v for _, v in items), 2),
        }

    def list_budgets(self) -> Dict[str, Any]:
        """List all budget configurations.

        Returns:
            Dict with budget list.
        """
        return {
            "budgets": [
                {
                    "name": b.name,
                    "amount": b.amount,
                    "period": b.period,
                    "category": b.category,
                    "spent": b.spent,
                    "remaining": round(b.amount - b.spent, 2),
                    "pct_used": round((b.spent / b.amount) * 100, 2) if b.amount > 0 else 0,
                    "alerts": [
                        {"name": a.name, "threshold_pct": a.threshold_pct, "triggered": a.triggered}
                        for a in b.alerts
                    ],
                }
                for b in self._budgets.values()
            ]
        }

    def create_budget(
        self,
        name: str,
        amount: float,
        period: str = "monthly",
        category: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a new budget.

        Args:
            name: Budget name.
            amount: Budget amount.
            period: Budget period.
            category: Optional category restriction.

        Returns:
            Dict with created budget details.

        Raises:
            ValueError: If budget name already exists.
        """
        if name in self._budgets:
            raise ValueError(f"Budget already exists: {name}")
        budget = Budget(name=name, amount=amount, period=period, category=category)
        self._budgets[name] = budget
        return {
            "name": budget.name,
            "amount": budget.amount,
            "period": budget.period,
            "category": budget.category,
            "created_at": budget.created_at,
        }

    def add_budget_alert(
        self,
        budget_name: str,
        alert_name: str,
        threshold_pct: float,
    ) -> Dict[str, Any]:
        """Add an alert to a budget.

        Args:
            budget_name: Budget name.
            alert_name: Alert name.
            threshold_pct: Threshold percentage.

        Returns:
            Dict with alert details.

        Raises:
            ValueError: If budget not found.
        """
        budget = self._budgets.get(budget_name)
        if not budget:
            raise ValueError(f"Budget not found: {budget_name}")
        alert = BudgetAlert(name=alert_name, threshold_pct=threshold_pct)
        budget.alerts.append(alert)
        return {
            "budget": budget_name,
            "alert": alert_name,
            "threshold_pct": threshold_pct,
        }

    def list_alerts(self) -> Dict[str, Any]:
        """List all budget alerts.

        Returns:
            Dict with alert list.
        """
        alerts = []
        for budget in self._budgets.values():
            for alert in budget.alerts:
                alerts.append({
                    "budget": budget.name,
                    "alert": alert.name,
                    "threshold_pct": alert.threshold_pct,
                    "triggered": alert.triggered,
                    "triggered_at": alert.triggered_at,
                })
        return {"alerts": alerts, "total": len(alerts)}

    def get_forecast(self, days: int = 30) -> Dict[str, Any]:
        """Forecast future costs based on historical data.

        Args:
            days: Number of days to forecast.

        Returns:
            Dict with forecast data.
        """
        # Simple linear forecast based on average daily cost
        daily_costs: Dict[str, float] = {}
        for r in self._cost_records:
            daily_costs[r["date"]] = daily_costs.get(r["date"], 0) + r["amount"]
        if not daily_costs:
            return {"forecast": {}, "avg_daily": 0, "total_forecast": 0}

        avg_daily = sum(daily_costs.values()) / len(daily_costs)
        now = datetime.utcnow()
        forecast: Dict[str, float] = {}
        for i in range(1, days + 1):
            day = (now + timedelta(days=i)).strftime("%Y-%m-%d")
            # Add some variation
            forecast[day] = round(avg_daily * (0.9 + 0.2 * (i % 5) / 5), 2)

        return {
            "forecast_days": days,
            "avg_daily": round(avg_daily, 2),
            "total_forecast": round(sum(forecast.values()), 2),
            "daily_forecast": forecast,
            "method": "historical_average_with_variation",
        }


# =====================================================================
# CLI Parser
# =====================================================================


def register_cost_commands(
    subparsers: argparse._SubParsersAction,
) -> None:
    """Register cost subcommands under the given argparse subparser.

    Args:
        subparsers: The parent subparsers action to attach to.
    """
    p_cost = subparsers.add_parser("cost", help="Cost tracking and budgeting")
    cost_sub = p_cost.add_subparsers(dest="cost_command", required=True)

    # cost overview
    cost_sub.add_parser("overview", help="Show overall cost overview")

    # cost breakdown
    p_breakdown = cost_sub.add_parser("breakdown", help="Cost breakdown by category")
    p_breakdown.add_argument("--category", help="Filter by category")
    p_breakdown.add_argument(
        "--period",
        default="monthly",
        choices=["daily", "weekly", "monthly"],
        help="Aggregation period",
    )

    # cost daily
    p_daily = cost_sub.add_parser("daily", help="Daily cost report")
    p_daily.add_argument("--days", type=int, default=7, help="Number of days")

    # cost weekly
    p_weekly = cost_sub.add_parser("weekly", help="Weekly cost report")
    p_weekly.add_argument("--weeks", type=int, default=4, help="Number of weeks")

    # cost monthly
    p_monthly = cost_sub.add_parser("monthly", help="Monthly cost report")
    p_monthly.add_argument("--months", type=int, default=6, help="Number of months")

    # cost budget list
    p_budget = cost_sub.add_parser("budget", help="Budget management")
    budget_sub = p_budget.add_subparsers(dest="budget_command", required=True)

    p_budget_list = budget_sub.add_parser("list", help="List budgets")

    p_budget_create = budget_sub.add_parser("create", help="Create a budget")
    p_budget_create.add_argument("--name", required=True, help="Budget name")
    p_budget_create.add_argument("--amount", type=float, required=True, help="Budget amount")
    p_budget_create.add_argument(
        "--period", default="monthly", choices=["daily", "weekly", "monthly"], help="Budget period"
    )
    p_budget_create.add_argument("--category", help="Category restriction")

    # cost alerts
    p_alerts = cost_sub.add_parser("alerts", help="Budget alerts")
    alerts_sub = p_alerts.add_subparsers(dest="alerts_command", required=True)

    p_alert_list = alerts_sub.add_parser("list", help="List all alerts")

    p_alert_add = alerts_sub.add_parser("add", help="Add a budget alert")
    p_alert_add.add_argument("--budget", required=True, help="Budget name")
    p_alert_add.add_argument("--name", required=True, help="Alert name")
    p_alert_add.add_argument("--threshold", type=float, required=True, help="Threshold percentage")

    # cost forecast
    p_forecast = cost_sub.add_parser("forecast", help="Cost forecast")
    p_forecast.add_argument("--days", type=int, default=30, help="Days to forecast")


# =====================================================================
# Command Handler
# =====================================================================


def handle_cost_command(
    args: argparse.Namespace, manager: CostManager
) -> int:
    """Dispatch a cost CLI command to the appropriate handler.

    Args:
        args: Parsed argparse namespace.
        manager: CostManager instance.

    Returns:
        Exit code (0 for success, 1 for error).
    """
    try:
        if args.cost_command == "overview":
            result = manager.get_overview()
        elif args.cost_command == "breakdown":
            result = manager.get_breakdown(
                category=args.category, period=args.period
            )
        elif args.cost_command == "daily":
            result = manager.get_daily_cost(days=args.days)
        elif args.cost_command == "weekly":
            result = manager.get_weekly_cost(weeks=args.weeks)
        elif args.cost_command == "monthly":
            result = manager.get_monthly_cost(months=args.months)
        elif args.cost_command == "budget":
            if args.budget_command == "list":
                result = manager.list_budgets()
            elif args.budget_command == "create":
                result = manager.create_budget(
                    name=args.name,
                    amount=args.amount,
                    period=args.period,
                    category=args.category,
                )
            else:
                print(f"Unknown budget command: {args.budget_command}")
                return 1
        elif args.cost_command == "alerts":
            if args.alerts_command == "list":
                result = manager.list_alerts()
            elif args.alerts_command == "add":
                result = manager.add_budget_alert(
                    budget_name=args.budget,
                    alert_name=args.name,
                    threshold_pct=args.threshold,
                )
            else:
                print(f"Unknown alerts command: {args.alerts_command}")
                return 1
        elif args.cost_command == "forecast":
            result = manager.get_forecast(days=args.days)
        else:
            print(f"Unknown cost command: {args.cost_command}")
            return 1

        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}, indent=2, ensure_ascii=False))
        return 1
