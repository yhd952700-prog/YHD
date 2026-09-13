"""``/v1/dashboard/analytics`` —— 驾驶舱图表数据的真实性与诚实降级。

两条不变量：

1. **连续轴**：窗口内每一天都必须出现，没有事件的那天是真实的 0，而不是从
   轴上消失（消失会让趋势看起来比实际更"平"或更"陡"）。
2. **噪声可追溯**：``state_change`` 被排除时必须报出排除了多少，否则"分布
   总和"与"总事件数"对不上，而看图的人无从知道原因。
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from src.gateway import dashboard
from src.gateway.main import get_app


@pytest.fixture
def client():
    with TestClient(get_app()) as test_client:
        yield test_client


class TestTimeseries:
    def test_returns_one_point_per_day_on_a_continuous_axis(self, client):
        days = 7
        body = client.get(f"/v1/dashboard/analytics?days={days}").json()["timeseries"]
        assert body["available"] is True
        assert len(body["days"]) == days

        dates = [row["date"] for row in body["days"]]
        assert dates == sorted(dates), "dates must be ascending"
        assert len(set(dates)) == days, "each day appears exactly once"

        # Consecutive calendar days, not just sorted labels.
        stamps = [time.mktime(time.strptime(d, "%Y-%m-%d")) for d in dates]
        for earlier, later in zip(stamps, stamps[1:]):
            assert later - earlier == pytest.approx(86400, abs=1)

    def test_last_point_is_today(self, client):
        rows = client.get("/v1/dashboard/analytics?days=3").json()["timeseries"]["days"]
        assert rows[-1]["date"] == time.strftime("%Y-%m-%d", time.localtime())

    def test_every_row_carries_the_full_shape(self, client):
        rows = client.get("/v1/dashboard/analytics?days=5").json()["timeseries"]["days"]
        for row in rows:
            assert set(row) == {"date", "total", "allowed", "denied", "noise"}
            assert all(isinstance(row[key], int) for key in row if key != "date")

    def test_counts_are_non_negative_and_subset_of_total(self, client):
        rows = client.get("/v1/dashboard/analytics?days=14").json()["timeseries"]["days"]
        for row in rows:
            assert row["total"] >= 0
            assert row["allowed"] + row["denied"] <= row["total"]

    @pytest.mark.parametrize("days", [1, 90])
    def test_boundary_windows_are_accepted(self, client, days):
        body = client.get(f"/v1/dashboard/analytics?days={days}").json()["timeseries"]
        assert len(body["days"]) == days

    @pytest.mark.parametrize("days", [0, -3, 200])
    def test_out_of_range_windows_are_rejected(self, client, days):
        assert client.get(f"/v1/dashboard/analytics?days={days}").status_code == 422

    def test_an_unavailable_source_says_so_instead_of_faking_a_curve(
        self, client, monkeypatch
    ):
        def boom(*args, **kwargs):
            raise RuntimeError("audit store is down")

        monkeypatch.setattr("src.kernels.audit.audit_query", boom)
        body = client.get("/v1/dashboard/analytics").json()["timeseries"]
        assert body["available"] is False
        assert "audit store is down" in body["error"]
        assert body["days"] == []


class TestBreakdown:
    def test_excludes_noise_and_reports_how_much(self, client, monkeypatch):
        monkeypatch.setattr(
            "src.kernels.audit.audit_stats",
            lambda: {
                "total_events": 1000,
                "breakdown": {
                    "state_change:state_change": 990,
                    "access_allowed:allow": 8,
                    "access_denied:deny": 2,
                },
            },
        )
        body = client.get("/v1/dashboard/analytics").json()["breakdown"]
        assert body["available"] is True
        assert body["excluded_noise_events"] == 990
        assert body["total_events"] == 1000
        assert [item["type"] for item in body["items"]] == ["access_allowed", "access_denied"]

    def test_items_are_sorted_by_count_descending(self, client, monkeypatch):
        monkeypatch.setattr(
            "src.kernels.audit.audit_stats",
            lambda: {
                "total_events": 60,
                "breakdown": {
                    "kernel_status:ok": 5,
                    "access_allowed:allow": 50,
                    "policy_eval:allow": 5,
                },
            },
        )
        items = client.get("/v1/dashboard/analytics").json()["breakdown"]["items"]
        counts = [item["count"] for item in items]
        assert counts == sorted(counts, reverse=True)

    def test_an_unavailable_source_says_so(self, client, monkeypatch):
        def boom():
            raise RuntimeError("stats unavailable")

        monkeypatch.setattr("src.kernels.audit.audit_stats", boom)
        body = client.get("/v1/dashboard/analytics").json()["breakdown"]
        assert body["available"] is False
        assert body["items"] == []
        assert "stats unavailable" in body["error"]

    def test_an_empty_store_yields_an_empty_but_available_breakdown(self, client, monkeypatch):
        monkeypatch.setattr(
            "src.kernels.audit.audit_stats",
            lambda: {"total_events": 0, "breakdown": {}},
        )
        body = client.get("/v1/dashboard/analytics").json()["breakdown"]
        # Empty is a fact, not a failure -- the UI should say "no events yet".
        assert body["available"] is True
        assert body["items"] == []
        assert body["total_events"] == 0


class TestDayKey:
    def test_uses_local_calendar_days(self):
        stamp = time.mktime(time.strptime("2026-03-04 13:45:00", "%Y-%m-%d %H:%M:%S"))
        assert dashboard._day_key(stamp) == "2026-03-04"
