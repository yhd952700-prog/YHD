"""驾驶舱遥测端点测试（真实数据，不伪造）。

验证：
- summary 返回真实字段（状态/运行时长/provider/会话/审计）
- activity 过滤掉 kernel state_change 噪声
- 数据源异常时降级返回空值 + error 字段，不用假数据填充
"""

from src.gateway.dashboard import dashboard_activity, dashboard_summary, recent_activity


def test_summary_returns_real_fields():
    s = dashboard_summary()
    assert s["status"] == "ready"
    assert isinstance(s["uptime_seconds"], float) and s["uptime_seconds"] >= 0
    assert "provider" in s and "model" in s["provider"]
    assert "sessions" in s and isinstance(s["sessions"]["count"], int)
    assert "audit" in s and isinstance(s["audit"].get("total_events", 0), int)


def test_summary_sessions_count_matches_ids():
    s = dashboard_summary()
    assert s["sessions"]["count"] == len(s["sessions"]["ids"])


def test_activity_filters_kernel_noise():
    items = recent_activity(limit=20)
    for it in items:
        assert it["type"] != "state_change"
        assert "outcome" in it


def test_activity_endpoint_shape():
    r = dashboard_activity(limit=5)
    assert r["limit"] == 5
    assert r["count"] == len(r["activity"])
    assert r["count"] <= 5


def test_activity_survives_noise_burst(monkeypatch):
    """噪声占绝对多数时，仍要捞到埋在其中的有意义事件。

    回归背景：审计里 state_change 噪声占 99%+，早期实现只取最新 200 条，
    过滤后常为空 → 驾驶舱「最近动态」长期显示「暂无审计事件」。
    """
    import src.kernels.audit as audit_kernel

    noise = [
        {
            "event_type": "state_change",
            "outcome": "success",
            "principal_id": "kernel",
            "timestamp": 1.0 + i,
            "correlation_id": None,
        }
        for i in range(3000)
    ]
    meaningful = [
        {
            "event_type": "access_denied",
            "outcome": "deny",
            "principal_id": "bob",
            "timestamp": 2.0,
            "correlation_id": "c1",
        },
        {
            "event_type": "policy_eval",
            "outcome": "error",
            "principal_id": "alice",
            "timestamp": 3.0,
            "correlation_id": "c2",
        },
    ]
    # 最新在前，两条有意义事件被埋在噪声深处（第 2000 / 2500 位）
    events = list(noise)
    events.insert(2000, meaningful[1])
    events.insert(2500, meaningful[0])

    monkeypatch.setattr(
        audit_kernel, "audit_query", lambda **kwargs: events[: kwargs.get("limit", 10)]
    )

    items = recent_activity(limit=5)
    assert [i["type"] for i in items] == ["policy_eval", "access_denied"]
    assert all(i["type"] != "state_change" for i in items)


def test_activity_empty_when_audit_unavailable(monkeypatch):
    """审计源异常时如实降级为空列表（不伪造动态条目）。"""
    import src.kernels.audit as audit_kernel

    def _boom(**kwargs):
        raise RuntimeError("audit db down")

    monkeypatch.setattr(audit_kernel, "audit_query", _boom)
    assert recent_activity(limit=5) == []
