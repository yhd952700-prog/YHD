"""P1 诚实告警读取面 —— /v1/alerts 与 /v1/alerts/rules 的最小真实闭合。

为什么这个测试存在
------------------
告警存储此前是"只写不读"：执行失败告警（真实路径，经 EventBus -> AlertStore）
只落 JSON / 日志，没有任何 API 或 UI 能读取。本测试证明新加的两个只读端点真的
返回 store 里**真实存在**的内容，而不是 mock 或装饰性数据：

- ``/v1/alerts/rules`` 经 ``install_production_rules()`` 注册进 store 的 7 条规则，
  全部 ``enabled=True``（诚实：真实指标发射器 MetricCollector 已接线，闭环真实，
  仅在指标真的越界时才产生 firing 告警，而非装饰性启用）；
- ``/v1/alerts`` 在没有任何执行失败时如实返回 ``count == 0``、``firing_count == 0``、
  ``alerts == []``，而不是报错或假装"一切正常"；
- 未带令牌 → 401，证明端点真的走了 ``require_human_principal`` 闸门。

隔离约定
--------
``LIUHAO_ALERTS_STORE_PATH`` 经 monkeypatch 指向 ``tmp_path``，绝不会碰真实告警库。
鉴权走真实 bearer 令牌（与网关 validator 同一签发/校验路径），不是 stub。
"""

from __future__ import annotations

import os

os.environ.setdefault("LIUHAO_WORKSPACE_ROOT", os.environ.get("LIUHAO_WORKSPACE_ROOT", ""))

import pytest
from fastapi.testclient import TestClient

from src.gateway.main import get_app


def _auth_headers() -> dict:
    """真实 bearer 令牌（网关 validator 同一签发路径）。"""
    from src.security import get_jwt_handler

    token, _payload = get_jwt_handler().create_token(
        subject="test-human", metadata={"kind": "human", "scope": "L0"}
    )
    return {"Authorization": f"Bearer {token}"}


#: 良性环境快照：全部 7 个真实指标键、全零值（对所有生产规则阈值都是安全的）。
#: 用于把「环境输入」从用例中确定性化——规则引擎、store、读取面保持真实闭环。
_BENIGN_SNAPSHOT = {
    "memory_usage_percent": 0.0,
    "cpu_usage_percent": 0.0,
    "error_rate_percent": 0.0,
    "latency_p99_ms": 0.0,
    "service_heartbeat_interval": 0.0,
    "audit_log_lag_seconds": 0.0,
    "audit_write_failing": 0.0,
}


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """每用例独立临时告警库；绝不会写真实 store。"""
    monkeypatch.setenv("LIUHAO_ALERTS_STORE_PATH", str(tmp_path / "alerts.json"))
    yield


@pytest.fixture
def client(monkeypatch):
    # 在构造 app 之前先注册规则，使 store 落到当前用例的临时路径。
    from src.observability.alerts import install_production_rules
    from src.observability.metrics import MetricCollector

    # 环境隔离：/v1/alerts 的指标规则评估的是**真实环境指标**（CPU/内存/延迟），
    # 在负载高的 CI runner 上会合法触发（run 43119840379 实测：2 核 runner
    # 满载时 cpu_usage_high firing，把「诚实空态」用例顶红）——那是产品在如实
    # 上报环境，不是读取面造假。本文件验证的是读取面契约，不是环境本身，因此
    # 把采集器钉在全零良性快照；firing 能力的反证由
    # test_enabled_rule_fires_on_breach_disabled_does_not 直接驱动规则对象完成。
    monkeypatch.setattr(
        MetricCollector,
        "collect_snapshot",
        lambda self: dict(_BENIGN_SNAPSHOT),
    )
    install_production_rules()
    app = get_app()
    with TestClient(app) as c:
        yield c, _auth_headers()


# install_production_rules() 注册的 7 条系统健康规则（现已 enabled=True，
# 因为真实指标发射器 MetricCollector 已接线，闭环真实；装饰性由下方
# test_enabled_rule_fires_on_breach 反证）。
PRODUCTION_RULE_IDS = {
    "svc_heartbeat_missing",
    "err_rate_high",
    "latency_p99_high",
    "mem_usage_high",
    "cpu_usage_high",
    "audit_lag_high",
    "audit_write_failing",
}


def test_alert_rules_visible_and_emitter_backed(client, tmp_path):
    """7 条生产规则被注册且全部 enabled=True（诚实：真实闭环，非装饰性启用）。

    注意：读取面如实返回 store 里的全部规则。除这 7 条生产规则外，启动流程还会
    注册 3 条 execution-binding 规则（真实可用路径，enabled=True），所以总数 >= 7，
    而非恰好 7。本测试只校验"诚实不变量"：7 条生产规则都在，且每条都
    enabled=True（因为 MetricCollector 真实发射器已接线，越界才 firing，闭环真实）。
    enabled 是否为装饰性的反证见 test_enabled_rule_fires_on_breach。
    """
    c, headers = client
    r = c.get("/v1/alerts/rules", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    # 至少包含 7 条生产规则（可能还有 execution-binding 注册的真实规则）。
    assert body["count"] >= 7, body
    by_id = {rule["id"]: rule for rule in body["rules"]}
    for rid in PRODUCTION_RULE_IDS:
        assert rid in by_id, f"缺失生产规则 {rid}"
        assert by_id[rid]["enabled"] is True, (
            f"{rid} 应为 enabled=True（真实指标发射器已接线，闭环真实，非装饰性）"
        )
        # 关键字段真实存在。
        rule = by_id[rid]
        assert isinstance(rule["name"], str) and rule["name"]
        assert isinstance(rule["severity"], str) and rule["severity"]
        assert isinstance(rule["alert_type"], str) and rule["alert_type"]
        assert isinstance(rule["metric_name"], str) and rule["metric_name"]


def test_enabled_rule_fires_on_breach_disabled_does_not():
    """反证 enabled=True 不是装饰性：越界时规则真的产生 firing，disabled 时不产生。

    直接驱动规则对象（不污染单例 store / 不依赖 15s 守护线程），证明：
    - enabled 规则在越界值上 evaluate() 返回 FIRING 告警；
    - 同一规则在阈值内 evaluate() 返回 None；
    - 把该规则临时 disabled 后，即便越界也 evaluate() 返回 None（enabled 标志有真实语义）。
    这是把"规则 enabled=True"从装饰性声明变成可证伪事实的关键测试。
    """
    from src.observability.alerts import (
        get_alert_manager,
        install_production_rules,
    )

    install_production_rules()
    mgr = get_alert_manager()
    rule = mgr.get_rule("latency_p99_high")
    assert rule is not None, "latency_p99_high 规则应已注册"
    assert rule.enabled is True, "诚实不变量：真实发射器接线后规则应为 enabled"

    # 越界（2000ms 阈值之上）→ 真 firing。
    firing = rule.evaluate(99999.0)
    assert firing is not None, "enabled 规则越界必须 firing（否则 enabled 是装饰性）"
    assert firing.state.name == "FIRING", firing.state

    # 阈值内（远低于 2000ms）→ 不 firing。
    assert rule.evaluate(10.0) is None, "阈值内不应 firing"

    # disabled → 即便越界也不 firing，证明 enabled 标志有真实语义。
    saved = rule.enabled
    rule.enabled = False
    try:
        assert rule.evaluate(99999.0) is None, "disabled 规则越界不应 firing"
    finally:
        rule.enabled = saved
    assert rule.enabled is True, "测试不得遗留禁用状态"


def test_list_alerts_shape(client, tmp_path):
    """/v1/alerts 形状正确：count / firing_count 为 int，alerts 为 list。"""
    c, headers = client
    r = c.get("/v1/alerts", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body["count"], int), body
    assert isinstance(body["firing_count"], int), body
    assert isinstance(body["alerts"], list), body


def test_list_alerts_requires_auth(client, tmp_path):
    """未带令牌 → 401。"""
    c, _headers = client
    r = c.get("/v1/alerts")
    assert r.status_code == 401, r.text


def test_alert_rules_requires_auth(client, tmp_path):
    """未带令牌 → 401。"""
    c, _headers = client
    r = c.get("/v1/alerts/rules")
    assert r.status_code == 401, r.text


def _member_headers() -> dict:
    """成员（L1）真实 bearer 令牌，与网关 validator 同一签发/校验路径。"""
    from src.security.jwt_handler import create_access_token

    token, _ = create_access_token(
        "test-member", metadata={"kind": "human", "scope": "L1"}
    )
    return {"Authorization": f"Bearer {token}"}


def test_member_is_forbidden_on_global_alert_surfaces(client, tmp_path):
    """成员（L1）读整机告警必须 403 + 中文原因，且不回显内部路径/堆栈。

    ``Alert`` 没有归属字段，无法安全地把全局告警裁剪成「与自己相关」；控制台的
    系统状态页本就 ``requires: 'root'``，成员界面不可达。
    """
    c, _headers = client
    for path in ("/v1/alerts", "/v1/alerts/rules"):
        r = c.get(path, headers=_member_headers())
        assert r.status_code == 403, f"{path} 成员应 403，实际 {r.status_code}: {r.text}"
        detail = r.json()["detail"]
        assert isinstance(detail, str), detail
        assert "管理员权限" in detail, detail
        for leak in ("Traceback", "src/gateway", "src\\gateway", ".py", "C:\\", "/Users/"):
            assert leak not in r.text, f"{path} 403 响应泄漏了内部信息：{leak!r}"


def test_alerts_honest_empty_when_no_failures(client, tmp_path):
    """诚实空态：无执行失败时 count==0、firing_count==0、alerts==[]，不是错误。"""
    c, headers = client
    r = c.get("/v1/alerts", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 0, body
    assert body["firing_count"] == 0, body
    assert body["alerts"] == [], body
