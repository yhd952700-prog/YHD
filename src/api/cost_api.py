"""Cost API endpoints for LiuHao AI OS.

RESTful API endpoints for cost management:
- Cost overview
- Cost breakdown by category
- Budget management
- Cost forecast
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field


router = APIRouter(prefix="/cost", tags=["cost"])


# =====================================================================
# Models
# =====================================================================


class BudgetCreate(BaseModel):
    """Budget creation request."""
    name: str = Field(..., min_length=1, max_length=100)
    amount: float = Field(..., gt=0, description="Budget amount")
    period: str = Field(default="monthly", description="Budget period")
    category: Optional[str] = Field(default=None, description="Category restriction")


class BudgetResponse(BaseModel):
    """Budget response model."""
    name: str
    amount: float
    period: str
    category: Optional[str]
    spent: float
    remaining: float
    pct_used: float
    alerts: List[Dict[str, Any]]


class AlertCreate(BaseModel):
    """Budget alert creation request."""
    budget_name: str
    alert_name: str
    threshold_pct: float = Field(..., gt=0, le=100)


# =====================================================================
# In-memory data (scaffold)
# =====================================================================


_cost_records: List[Dict[str, Any]] = []
_budgets: Dict[str, Dict[str, Any]] = {}
_budget_alerts: List[Dict[str, Any]] = []


def _seed_sample_data() -> None:
    """Seed with sample cost data (once)."""
    if _cost_records:
        return
    now = datetime.utcnow()
    categories = ["ai_provider", "storage", "compute", "network", "third_party"]
    for i in range(30):
        day = now - timedelta(days=i)
        for cat in categories:
            _cost_records.append({
                "date": day.strftime("%Y-%m-%d"),
                "category": cat,
                "amount": round(10 + i * 0.5 + hash(cat) % 5, 2),
            })


_seed_sample_data()


# =====================================================================
# Cost Overview Endpoints
# =====================================================================


@router.get("/overview")
async def cost_overview() -> Dict[str, Any]:
    """Get overall cost overview.

    Returns total cost, today's cost, and breakdown by category.

    Returns:
        Dict with cost overview data.
    """
    total = sum(r["amount"] for r in _cost_records)
    today = datetime.utcnow().strftime("%Y-%m-%d")
    today_cost = sum(r["amount"] for r in _cost_records if r["date"] == today)
    by_category: Dict[str, float] = {}
    for r in _cost_records:
        by_category[r["category"]] = by_category.get(r["category"], 0) + r["amount"]

    return {
        "total_cost": round(total, 2),
        "today_cost": round(today_cost, 2),
        "currency": "USD",
        "by_category": {k: round(v, 2) for k, v in by_category.items()},
        "record_count": len(_cost_records),
        "period": "last_30_days",
    }


@router.get("/breakdown")
async def cost_breakdown(
    category: Optional[str] = Query(None, description="Filter by category"),
    period: str = Query("monthly", regex="^(daily|weekly|monthly)$", description="Aggregation period"),
) -> Dict[str, Any]:
    """Get cost breakdown by category and period.

    Args:
        category: Optional category filter.
        period: Aggregation period.

    Returns:
        Dict with cost breakdown data.
    """
    records = _cost_records
    if category:
        records = [r for r in records if r["category"] == category]

    grouped: Dict[str, float] = {}
    for r in records:
        date_obj = datetime.strptime(r["date"], "%Y-%m-%d")
        if period == "daily":
            key = r["date"]
        elif period == "weekly":
            iso = date_obj.isocalendar()
            key = f"{iso[0]}-W{iso[1]:02d}"
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


@router.get("/daily")
async def cost_daily(
    days: int = Query(7, ge=1, le=90, description="Number of days"),
) -> Dict[str, Any]:
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

    for r in _cost_records:
        if r["date"] in daily:
            daily[r["date"]][r["category"]] = round(
                daily[r["date"]].get(r["category"], 0) + r["amount"], 2
            )

    return {
        "days": days,
        "daily": {k: v for k, v in sorted(daily.items(), reverse=True)},
    }


# =====================================================================
# Budget Management Endpoints
# =====================================================================


@router.get("/budgets")
async def list_budgets() -> Dict[str, Any]:
    """List all budget configurations.

    Returns:
        Dict with budget list.
    """
    return {
        "budgets": [
            {
                "name": b["name"],
                "amount": b["amount"],
                "period": b["period"],
                "category": b["category"],
                "spent": b["spent"],
                "remaining": round(b["amount"] - b["spent"], 2),
                "pct_used": round((b["spent"] / b["amount"]) * 100, 2) if b["amount"] > 0 else 0,
                "alerts": [
                    {"name": a["alert_name"], "threshold_pct": a["threshold_pct"], "triggered": a.get("triggered", False)}
                    for a in _budget_alerts if a["budget_name"] == b["name"]
                ],
            }
            for b in _budgets.values()
        ],
        "total": len(_budgets),
    }


@router.post("/budgets", status_code=201)
async def create_budget(body: BudgetCreate) -> Dict[str, Any]:
    """Create a new budget.

    Args:
        body: Budget creation request.

    Returns:
        Created budget details.

    Raises:
        HTTPException: 409 if budget already exists.
    """
    if body.name in _budgets:
        raise HTTPException(status_code=409, detail=f"Budget already exists: {body.name}")
    _budgets[body.name] = {
        "name": body.name,
        "amount": body.amount,
        "period": body.period,
        "category": body.category,
        "spent": 0.0,
        "created_at": datetime.utcnow().isoformat(),
    }
    return {
        "name": body.name,
        "amount": body.amount,
        "period": body.period,
        "category": body.category,
        "spent": 0.0,
        "remaining": body.amount,
        "pct_used": 0.0,
    }


@router.get("/budgets/{name}")
async def get_budget(name: str) -> Dict[str, Any]:
    """Get details of a specific budget.

    Args:
        name: Budget name.

    Returns:
        Budget details.

    Raises:
        HTTPException: 404 if not found.
    """
    if name not in _budgets:
        raise HTTPException(status_code=404, detail=f"Budget not found: {name}")
    b = _budgets[name]
    return {
        "name": b["name"],
        "amount": b["amount"],
        "period": b["period"],
        "category": b["category"],
        "spent": b["spent"],
        "remaining": round(b["amount"] - b["spent"], 2),
        "pct_used": round((b["spent"] / b["amount"]) * 100, 2) if b["amount"] > 0 else 0,
        "alerts": [
            {"name": a["alert_name"], "threshold_pct": a["threshold_pct"], "triggered": a.get("triggered", False)}
            for a in _budget_alerts if a["budget_name"] == name
        ],
    }


@router.delete("/budgets/{name}", status_code=204)
async def delete_budget(name: str) -> None:
    """Delete a budget.

    Args:
        name: Budget name.

    Raises:
        HTTPException: 404 if not found.
    """
    if name not in _budgets:
        raise HTTPException(status_code=404, detail=f"Budget not found: {name}")
    del _budgets[name]
    # Remove associated alerts
    global _budget_alerts
    _budget_alerts = [a for a in _budget_alerts if a["budget_name"] != name]


# =====================================================================
# Budget Alerts Endpoints
# =====================================================================


@router.get("/alerts")
async def list_alerts() -> Dict[str, Any]:
    """List all budget alerts.

    Returns:
        Dict with alert list.
    """
    return {
        "alerts": [
            {
                "budget": a["budget_name"],
                "name": a["alert_name"],
                "threshold_pct": a["threshold_pct"],
                "triggered": a.get("triggered", False),
                "triggered_at": a.get("triggered_at"),
            }
            for a in _budget_alerts
        ],
        "total": len(_budget_alerts),
    }


@router.post("/alerts", status_code=201)
async def create_alert(body: AlertCreate) -> Dict[str, Any]:
    """Add an alert to a budget.

    Args:
        body: Alert creation request.

    Returns:
        Created alert details.

    Raises:
        HTTPException: 404 if budget not found.
    """
    if body.budget_name not in _budgets:
        raise HTTPException(status_code=404, detail=f"Budget not found: {body.budget_name}")
    alert = {
        "budget_name": body.budget_name,
        "alert_name": body.alert_name,
        "threshold_pct": body.threshold_pct,
        "triggered": False,
        "created_at": datetime.utcnow().isoformat(),
    }
    _budget_alerts.append(alert)
    return {
        "budget": body.budget_name,
        "alert": body.alert_name,
        "threshold_pct": body.threshold_pct,
    }


# =====================================================================
# Cost Forecast Endpoint
# =====================================================================


@router.get("/forecast")
async def cost_forecast(
    days: int = Query(30, ge=1, le=365, description="Days to forecast"),
) -> Dict[str, Any]:
    """Forecast future costs based on historical data.

    Uses historical average to predict future costs with variation.

    Args:
        days: Number of days to forecast.

    Returns:
        Dict with forecast data.
    """
    daily_costs: Dict[str, float] = {}
    for r in _cost_records:
        daily_costs[r["date"]] = daily_costs.get(r["date"], 0) + r["amount"]

    if not daily_costs:
        return {"forecast_days": days, "avg_daily": 0, "total_forecast": 0, "daily_forecast": {}}

    avg_daily = sum(daily_costs.values()) / len(daily_costs)
    now = datetime.utcnow()
    forecast: Dict[str, float] = {}
    for i in range(1, days + 1):
        day = (now + timedelta(days=i)).strftime("%Y-%m-%d")
        forecast[day] = round(avg_daily * (0.9 + 0.2 * (i % 5) / 5), 2)

    return {
        "forecast_days": days,
        "avg_daily": round(avg_daily, 2),
        "total_forecast": round(sum(forecast.values()), 2),
        "daily_forecast": forecast,
        "method": "historical_average_with_variation",
    }
