"""Comprehensive cost tracker tests for LiuHao AI OS.

Covers:
- Cost record creation
- Daily/weekly/monthly aggregation
- Budget management
- Alert thresholds (50%, 80%, 100%)
- Cost optimization suggestions
- Cost forecasting
- CSV/JSON export
- Multiple providers
"""

import time
import json
import pytest
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from src.cost.cost_tracker import (
    CostCategory,
    AlertLevel,
    AggregationPeriod,
    CostRecord,
    Budget,
    CostAlert,
    OptimizationSuggestion,
    CostForecast,
    CostTracker,
)


# ======================================================================
# CostRecord Tests (7 tests)
# ======================================================================

class TestCostRecord:
    """Tests for CostRecord."""

    def test_record_creation(self):
        """Test creating a cost record."""
        record = CostRecord(
            category=CostCategory.API_CALLS,
            amount=29.99,
            provider="openai",
            description="GPT-4 API usage",
        )
        assert record.category == CostCategory.API_CALLS
        assert record.amount == 29.99
        assert record.provider == "openai"
        assert record.description == "GPT-4 API usage"

    def test_record_has_id(self):
        """Test cost record has a generated ID."""
        record = CostRecord(
            category=CostCategory.COMPUTE,
            amount=100.0,
            provider="aws",
        )
        assert record.record_id.startswith("cost_")
        assert len(record.record_id) > 5

    def test_record_timestamp(self):
        """Test cost record timestamp."""
        before = time.time()
        record = CostRecord(
            category=CostCategory.STORAGE,
            amount=10.0,
            provider="gcp",
        )
        after = time.time()
        assert before <= record.timestamp <= after

    def test_negative_amount_raises(self):
        """Test that negative cost amounts raise ValueError."""
        with pytest.raises(ValueError, match="negative"):
            CostRecord(
                category=CostCategory.API_CALLS,
                amount=-5.0,
                provider="test",
            )

    def test_zero_amount_valid(self):
        """Test that zero cost amount is valid."""
        record = CostRecord(
            category=CostCategory.API_CALLS,
            amount=0.0,
            provider="test",
        )
        assert record.amount == 0.0

    def test_record_datetime_property(self):
        """Test datetime property of CostRecord."""
        ts = time.time()
        record = CostRecord(
            category=CostCategory.NETWORK,
            amount=5.0,
            provider="aws",
            timestamp=ts,
        )
        assert isinstance(record.datetime, datetime)

    def test_to_dict(self):
        """Test CostRecord to_dict serialization."""
        record = CostRecord(
            category=CostCategory.THIRD_PARTY,
            amount=49.99,
            provider="stripe",
            resource_id="res_123",
            description="payment processing",
            metadata={"txn_count": 100},
        )
        d = record.to_dict()
        assert d["category"] == "third_party"
        assert d["amount"] == 49.99
        assert d["provider"] == "stripe"
        assert d["resource_id"] == "res_123"
        assert d["description"] == "payment processing"
        assert d["metadata"] == {"txn_count": 100}
        assert "record_id" in d
        assert "timestamp" in d


# ======================================================================
# Budget Tests (7 tests)
# ======================================================================

class TestBudget:
    """Tests for Budget."""

    def test_budget_creation(self):
        """Test creating a budget."""
        budget = Budget(
            name="monthly_api",
            limit=1000.0,
            period=AggregationPeriod.MONTHLY,
        )
        assert budget.name == "monthly_api"
        assert budget.limit == 1000.0
        assert budget.current_spend == 0.0

    def test_remaining_budget(self):
        """Test remaining budget calculation."""
        budget = Budget(name="test", limit=100.0)
        budget.current_spend = 30.0
        assert budget.remaining == 70.0

    def test_utilization_ratio(self):
        """Test utilization ratio calculation."""
        budget = Budget(name="test", limit=200.0)
        budget.current_spend = 100.0
        assert budget.utilization == 0.5

    def test_utilization_zero_limit(self):
        """Test utilization with zero limit returns 0."""
        budget = Budget(name="test", limit=0.0)
        budget.current_spend = 50.0
        assert budget.utilization == 0.0

    def test_is_over_budget(self):
        """Test is_over_budget flag."""
        budget = Budget(name="test", limit=100.0)
        budget.current_spend = 90.0
        assert budget.is_over_budget is False

        budget.current_spend = 100.0
        assert budget.is_over_budget is False  # exactly at limit

        budget.current_spend = 101.0
        assert budget.is_over_budget is True

    def test_reset_budget(self):
        """Test budget reset."""
        budget = Budget(name="test", limit=100.0)
        budget.current_spend = 50.0
        budget.alerts_fired.add(0.5)
        budget.reset()
        assert budget.current_spend == 0.0
        assert len(budget.alerts_fired) == 0

    def test_category_specific_budget(self):
        """Test category-specific budget."""
        budget = Budget(
            name="api_budget",
            limit=500.0,
            category=CostCategory.API_CALLS,
        )
        assert budget.category == CostCategory.API_CALLS


# ======================================================================
# CostTracker Record & Aggregation Tests (8 tests)
# ======================================================================

class TestCostTrackerRecording:
    """Tests for CostTracker cost recording and aggregation."""

    def test_record_single_cost(self):
        """Test recording a single cost entry."""
        tracker = CostTracker()
        record = tracker.record_cost(
            category=CostCategory.API_CALLS,
            amount=29.99,
            provider="openai",
            description="API calls",
        )
        assert isinstance(record, CostRecord)
        assert len(tracker.records) == 1
        assert tracker.total_cost() == 29.99

    def test_record_multiple_costs(self):
        """Test recording multiple cost entries."""
        tracker = CostTracker()
        tracker.record_cost(CostCategory.API_CALLS, 10.0, "provider_a")
        tracker.record_cost(CostCategory.COMPUTE, 20.0, "provider_b")
        tracker.record_cost(CostCategory.STORAGE, 5.0, "provider_a")

        assert len(tracker.records) == 3
        assert tracker.total_cost() == 35.0

    def test_total_cost_by_category(self):
        """Test total cost filtered by category."""
        tracker = CostTracker()
        tracker.record_cost(CostCategory.API_CALLS, 10.0, "p1")
        tracker.record_cost(CostCategory.API_CALLS, 20.0, "p2")
        tracker.record_cost(CostCategory.COMPUTE, 30.0, "p1")

        assert tracker.total_cost(category=CostCategory.API_CALLS) == 30.0
        assert tracker.total_cost(category=CostCategory.COMPUTE) == 30.0

    def test_total_cost_by_provider(self):
        """Test total cost filtered by provider."""
        tracker = CostTracker()
        tracker.record_cost(CostCategory.API_CALLS, 10.0, "aws")
        tracker.record_cost(CostCategory.COMPUTE, 20.0, "aws")
        tracker.record_cost(CostCategory.STORAGE, 5.0, "gcp")

        assert tracker.total_cost(provider="aws") == 30.0
        assert tracker.total_cost(provider="gcp") == 5.0

    def test_by_category_breakdown(self):
        """Test cost breakdown by category."""
        tracker = CostTracker()
        tracker.record_cost(CostCategory.API_CALLS, 10.0, "p1")
        tracker.record_cost(CostCategory.COMPUTE, 20.0, "p1")
        tracker.record_cost(CostCategory.STORAGE, 5.0, "p1")

        breakdown = tracker.by_category()
        assert breakdown[CostCategory.API_CALLS] == 10.0
        assert breakdown[CostCategory.COMPUTE] == 20.0
        assert breakdown[CostCategory.STORAGE] == 5.0

    def test_by_provider_breakdown(self):
        """Test cost breakdown by provider."""
        tracker = CostTracker()
        tracker.record_cost(CostCategory.API_CALLS, 10.0, "openai")
        tracker.record_cost(CostCategory.COMPUTE, 20.0, "aws")

        breakdown = tracker.by_provider()
        assert breakdown["openai"] == 10.0
        assert breakdown["aws"] == 20.0

    def test_daily_aggregation(self):
        """Test daily cost aggregation."""
        tracker = CostTracker()
        today = time.time()
        yesterday = today - 86400  # 1 day ago

        tracker.record_cost(
            CostCategory.API_CALLS, 10.0, "p1", timestamp=today
        )
        tracker.record_cost(
            CostCategory.API_CALLS, 20.0, "p1", timestamp=yesterday
        )

        daily = tracker.aggregate(period=AggregationPeriod.DAILY)
        assert len(daily) == 2  # two different days

    def test_monthly_aggregation(self):
        """Test monthly cost aggregation."""
        tracker = CostTracker()
        # All in same month
        for i in range(5):
            tracker.record_cost(
                CostCategory.API_CALLS,
                10.0,
                "p1",
                timestamp=time.time() - i * 3600,  # hours apart
            )

        monthly = tracker.aggregate(period=AggregationPeriod.MONTHLY)
        # All should be in the same month
        assert len(monthly) == 1
        total = sum(monthly.values())
        assert total == 50.0


# ======================================================================
# Budget & Alert Tests (7 tests)
# ======================================================================

class TestBudgetAlerts:
    """Tests for budget alerts and thresholds."""

    def test_set_budget(self):
        """Test setting a budget."""
        tracker = CostTracker()
        budget = tracker.set_budget("monthly", 1000.0)
        assert budget.name == "monthly"
        assert budget.limit == 1000.0
        assert tracker.get_budget("monthly") is budget

    def test_budget_updates_with_costs(self):
        """Test budget updates as costs are recorded."""
        tracker = CostTracker()
        tracker.set_budget("monthly", 100.0)
        tracker.record_cost(CostCategory.API_CALLS, 30.0, "provider")

        budget = tracker.get_budget("monthly")
        assert budget.current_spend == 30.0
        assert budget.remaining == 70.0

    def test_50_percent_alert(self):
        """Test alert at 50% budget threshold (INFO level)."""
        tracker = CostTracker()
        tracker.set_budget("test", 100.0)
        tracker.record_cost(CostCategory.API_CALLS, 50.0, "p1")  # exactly 50%

        alerts = tracker.recent_alerts()
        info_alerts = [a for a in alerts if a.level == AlertLevel.INFO]
        assert len(info_alerts) >= 1
        assert info_alerts[0].threshold_pct == 50.0

    def test_80_percent_alert(self):
        """Test alert at 80% budget threshold (WARNING level)."""
        tracker = CostTracker()
        tracker.set_budget("test", 100.0)
        tracker.record_cost(CostCategory.API_CALLS, 85.0, "p1")  # over 80%

        alerts = tracker.recent_alerts()
        warning_alerts = [a for a in alerts if a.level == AlertLevel.WARNING]
        assert len(warning_alerts) >= 1

    def test_100_percent_alert(self):
        """Test alert at 100% budget threshold (CRITICAL level)."""
        tracker = CostTracker()
        tracker.set_budget("test", 100.0)
        tracker.record_cost(CostCategory.API_CALLS, 100.0, "p1")

        alerts = tracker.recent_alerts()
        critical_alerts = [a for a in alerts if a.level == AlertLevel.CRITICAL]
        assert len(critical_alerts) >= 1

    def test_category_budget_only_tracks_that_category(self):
        """Test category-specific budget only tracks its category."""
        tracker = CostTracker()
        tracker.set_budget(
            "api_budget", 100.0, category=CostCategory.API_CALLS
        )
        tracker.record_cost(CostCategory.API_CALLS, 50.0, "p1")
        tracker.record_cost(CostCategory.COMPUTE, 200.0, "p1")

        budget = tracker.get_budget("api_budget")
        assert budget.current_spend == 50.0  # only API calls count

    def test_reset_budget(self):
        """Test resetting a budget."""
        tracker = CostTracker()
        tracker.set_budget("test", 100.0)
        tracker.record_cost(CostCategory.API_CALLS, 60.0, "p1")

        assert tracker.get_budget("test").current_spend == 60.0
        tracker.reset_budget("test")
        assert tracker.get_budget("test").current_spend == 0.0


# ======================================================================
# Optimization Suggestions Tests (5 tests)
# ======================================================================

class TestOptimizationSuggestions:
    """Tests for cost optimization suggestions."""

    def test_suggestions_for_api_costs(self):
        """Test optimization suggestions for API costs."""
        tracker = CostTracker()
        tracker.record_cost(CostCategory.API_CALLS, 100.0, "openai")

        suggestions = tracker.get_optimization_suggestions()
        api_suggestions = [
            s for s in suggestions if s.category == CostCategory.API_CALLS
        ]
        assert len(api_suggestions) >= 1
        assert api_suggestions[0].estimated_savings > 0

    def test_suggestions_for_compute_costs(self):
        """Test optimization suggestions for compute costs."""
        tracker = CostTracker()
        tracker.record_cost(CostCategory.COMPUTE, 200.0, "aws")

        suggestions = tracker.get_optimization_suggestions()
        compute_suggestions = [
            s for s in suggestions if s.category == CostCategory.COMPUTE
        ]
        assert len(compute_suggestions) >= 1
        assert compute_suggestions[0].priority == "high"

    def test_suggestions_for_storage_costs(self):
        """Test optimization suggestions for storage costs."""
        tracker = CostTracker()
        tracker.record_cost(CostCategory.STORAGE, 50.0, "gcp")

        suggestions = tracker.get_optimization_suggestions()
        storage_suggestions = [
            s for s in suggestions if s.category == CostCategory.STORAGE
        ]
        assert len(storage_suggestions) >= 1

    def test_over_budget_suggestion(self):
        """Test suggestion for over-budget spending."""
        tracker = CostTracker()
        tracker.set_budget("monthly", 100.0)
        tracker.record_cost(CostCategory.API_CALLS, 150.0, "p1")  # over budget

        suggestions = tracker.get_optimization_suggestions()
        budget_suggestions = [
            s for s in suggestions if "Budget exceeded" in s.title
        ]
        assert len(budget_suggestions) >= 1

    def test_suggestion_to_dict(self):
        """Test OptimizationSuggestion to_dict."""
        suggestion = OptimizationSuggestion(
            title="Test suggestion",
            description="A test",
            estimated_savings=25.0,
            category=CostCategory.API_CALLS,
            priority="high",
            action_items=["do A", "do B"],
        )
        d = suggestion.to_dict()
        assert d["title"] == "Test suggestion"
        assert d["estimated_savings"] == 25.0
        assert d["category"] == "api_calls"
        assert d["priority"] == "high"
        assert len(d["action_items"]) == 2


# ======================================================================
# Cost Forecasting Tests (5 tests)
# ======================================================================

class TestCostForecasting:
    """Tests for cost forecasting."""

    def test_forecast_no_data(self):
        """Test forecast with no historical data."""
        tracker = CostTracker()
        forecast = tracker.forecast_cost()
        assert isinstance(forecast, CostForecast)
        assert forecast.projected_cost == 0.0
        assert forecast.trend == "stable"
        assert forecast.confidence == 0.0

    def test_forecast_with_stable_data(self):
        """Test forecast with stable cost data."""
        tracker = CostTracker()
        now = time.time()
        # Add 6 days of stable costs
        for i in range(6):
            tracker.record_cost(
                CostCategory.API_CALLS,
                100.0,
                "provider",
                timestamp=now - i * 86400,
            )

        forecast = tracker.forecast_cost(period=AggregationPeriod.DAILY)
        assert forecast.projected_cost > 0
        assert forecast.trend == "stable"
        assert forecast.basis_points >= 1

    def test_forecast_increasing_trend(self):
        """Test forecast detects increasing trend."""
        tracker = CostTracker()
        now = time.time()
        # Increasing costs: 10, 20, 30, 40, 50, 60
        for i in range(6):
            tracker.record_cost(
                CostCategory.API_CALLS,
                10.0 + i * 10.0,
                "provider",
                timestamp=now - (5 - i) * 86400,
            )

        forecast = tracker.forecast_cost(period=AggregationPeriod.DAILY)
        assert forecast.trend == "increasing"
        assert forecast.projected_cost > forecast.historical_avg

    def test_forecast_confidence_increases_with_data(self):
        """Test forecast confidence increases with more data points."""
        tracker = CostTracker()
        now = time.time()

        # With 2 data points
        for i in range(2):
            tracker.record_cost(
                CostCategory.API_CALLS, 100.0, "p1",
                timestamp=now - i * 86400,
            )
        forecast_2 = tracker.forecast_cost(period=AggregationPeriod.DAILY)
        conf_2 = forecast_2.confidence

        # Add more data points
        for i in range(2, 8):
            tracker.record_cost(
                CostCategory.API_CALLS, 100.0, "p1",
                timestamp=now - i * 86400,
            )
        forecast_8 = tracker.forecast_cost(period=AggregationPeriod.DAILY)
        conf_8 = forecast_8.confidence

        assert conf_8 >= conf_2

    def test_forecast_to_dict(self):
        """Test CostForecast to_dict serialization."""
        forecast = CostForecast(
            period=AggregationPeriod.MONTHLY,
            projected_cost=1500.0,
            historical_avg=1200.0,
            trend="increasing",
            confidence=0.8,
            basis_points=6,
        )
        d = forecast.to_dict()
        assert d["period"] == "monthly"
        assert d["projected_cost"] == 1500.0
        assert d["historical_avg"] == 1200.0
        assert d["trend"] == "increasing"
        assert d["confidence"] == 0.8
        assert d["basis_points"] == 6


# ======================================================================
# Export Tests (6 tests)
# ======================================================================

class TestCostExport:
    """Tests for CSV/JSON export."""

    def test_csv_export_contains_records(self):
        """Test CSV export includes cost records."""
        tracker = CostTracker()
        tracker.record_cost(CostCategory.API_CALLS, 29.99, "openai", description="test")

        csv_content = tracker.to_csv()
        assert "record_id" in csv_content
        assert "category" in csv_content
        assert "amount" in csv_content
        assert "provider" in csv_content
        assert "api_calls" in csv_content
        assert "29.99" in csv_content

    def test_csv_export_empty(self):
        """Test CSV export with no records."""
        tracker = CostTracker()
        csv_content = tracker.to_csv()
        # Should have header row
        assert "record_id" in csv_content
        lines = csv_content.strip().split("\n")
        assert len(lines) == 1  # header only

    def test_json_export_structure(self):
        """Test JSON export structure."""
        tracker = CostTracker()
        tracker.record_cost(CostCategory.API_CALLS, 10.0, "openai")
        tracker.set_budget("monthly", 100.0)

        json_content = tracker.to_json()
        data = json.loads(json_content)

        assert "currency" in data
        assert "total_records" in data
        assert "total_cost" in data
        assert "records" in data
        assert "budgets" in data
        assert "alerts" in data
        assert "by_category" in data
        assert "by_provider" in data

    def test_json_export_values(self):
        """Test JSON export values are correct."""
        tracker = CostTracker()
        tracker.record_cost(CostCategory.API_CALLS, 50.0, "openai")
        tracker.record_cost(CostCategory.COMPUTE, 30.0, "aws")

        json_content = tracker.to_json()
        data = json.loads(json_content)

        assert data["total_records"] == 2
        assert data["total_cost"] == 80.0
        assert data["by_category"]["api_calls"] == 50.0
        assert data["by_provider"]["openai"] == 50.0

    def test_export_to_file_json(self, tmp_path):
        """Test exporting to a JSON file."""
        tracker = CostTracker()
        tracker.record_cost(CostCategory.STORAGE, 15.0, "gcp")

        filepath = tmp_path / "costs.json"
        result_path = tracker.export_to_file(filepath, fmt="json")

        assert result_path.exists()
        content = result_path.read_text()
        data = json.loads(content)
        assert data["total_records"] == 1

    def test_export_to_file_csv(self, tmp_path):
        """Test exporting to a CSV file."""
        tracker = CostTracker()
        tracker.record_cost(CostCategory.NETWORK, 25.0, "aws")

        filepath = tmp_path / "costs.csv"
        result_path = tracker.export_to_file(filepath, fmt="csv")

        assert result_path.exists()
        content = result_path.read_text()
        assert "record_id" in content
        assert "network" in content


# ======================================================================
# Multiple Providers Tests (3 tests)
# ======================================================================

class TestMultipleProviders:
    """Tests for multi-provider cost tracking."""

    def test_multiple_providers(self):
        """Test tracking costs across multiple providers."""
        tracker = CostTracker()
        tracker.record_cost(CostCategory.API_CALLS, 100.0, "openai")
        tracker.record_cost(CostCategory.API_CALLS, 50.0, "anthropic")
        tracker.record_cost(CostCategory.COMPUTE, 200.0, "aws")
        tracker.record_cost(CostCategory.STORAGE, 30.0, "gcp")

        providers = tracker.by_provider()
        assert len(providers) == 4
        assert providers["openai"] == 100.0
        assert providers["anthropic"] == 50.0
        assert providers["aws"] == 200.0
        assert providers["gcp"] == 30.0

    def test_provider_filtering(self):
        """Test filtering costs by provider."""
        tracker = CostTracker()
        tracker.record_cost(CostCategory.API_CALLS, 100.0, "provider_a")
        tracker.record_cost(CostCategory.API_CALLS, 200.0, "provider_b")

        assert tracker.total_cost(provider="provider_a") == 100.0
        assert tracker.total_cost(provider="provider_b") == 200.0

    def test_all_cost_categories(self):
        """Test all cost categories can be used."""
        tracker = CostTracker()
        categories = list(CostCategory)
        for i, cat in enumerate(categories):
            tracker.record_cost(cat, float(i + 1) * 10.0, f"provider_{i}")

        assert len(tracker.records) == len(categories)
        breakdown = tracker.by_category()
        assert len(breakdown) == len(categories)
