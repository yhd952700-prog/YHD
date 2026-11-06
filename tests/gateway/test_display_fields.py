"""界面可读字段回归：模型清单、事件列表、告警三条链路。

产品反馈：界面出现 ``provider=ollama``、``service.liuhao-internal-service.success``
这类机器值，根因是接口只给机器字段。修复约定是**只增字段**：

* 模型/服务商 → ``display_name`` / ``display_status`` / ``provider_display_name``；
* 任务/事件 → ``display_title`` / ``status_label``；
* 健康/告警/预检 → ``user_message`` + ``technical_detail``（后者留给诊断页）；
* 旧字段（``provider`` / ``type`` / ``severity`` / ``message`` 等）一律保留。

本测试逐条锁住这些形态，并断言旧字段仍在（向后兼容）。
"""

from __future__ import annotations

import os

os.environ.setdefault("LIUHAO_WORKSPACE_ROOT", os.environ.get("LIUHAO_WORKSPACE_ROOT", ""))

import pytest
from fastapi.testclient import TestClient

from src.gateway.display import machine_fragments
from src.gateway.main import get_app


def _auth_headers() -> dict:
    """真实 bearer 令牌（与网关 validator 同一签发/校验路径）。"""
    from src.security import get_jwt_handler

    token, _payload = get_jwt_handler().create_token(
        subject="test-human", metadata={"kind": "human", "scope": "L0"}
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """告警库与模型库都落到临时目录，绝不碰真实数据。"""
    monkeypatch.setenv("LIUHAO_ALERTS_STORE_PATH", str(tmp_path / "alerts.json"))
    monkeypatch.setenv("LIUHAO_MODEL_CONFIG_DB", str(tmp_path / "models.sqlite3"))
    monkeypatch.setenv("LIUHAO_MODEL_CONFIG_KEY", "display-fields-test-key")
    yield


@pytest.fixture
def client():
    from src.observability.alerts import install_production_rules

    install_production_rules()
    app = get_app()
    with TestClient(app) as handle:
        yield handle, _auth_headers()


def test_model_list_has_human_readable_fields(client):
    """模型清单：服务商与启停状态必须是一句话，而不是裸枚举。"""
    handle, headers = client
    created = handle.post(
        "/v1/models",
        headers=headers,
        json={
            "name": "本地小模型",
            "provider": "ollama",
            "model": "llama3.1",
            "base_url": "http://127.0.0.1:11434",
            "enabled": True,
        },
    )
    assert created.status_code == 200, created.text

    body = handle.get("/v1/models", headers=headers).json()
    assert body["count"] >= 1
    item = body["models"][0]

    # 旧字段必须仍在（前端/旧调用方不受影响）。
    assert item["provider"] == "ollama"
    assert item["model"] == "llama3.1"
    assert item["enabled"] is True

    # 新增的人类可读字段。
    assert item["display_name"] == "本地小模型"
    assert item["provider_display_name"] == "本地 Ollama"
    assert item["display_status"] == "本地 Ollama · 已启用"
    assert item["status_label"] == "已启用"
    assert "provider=" not in item["display_status"]

    providers = handle.get("/v1/models/providers", headers=headers).json()["providers"]
    ollama = next(p for p in providers if p["type"] == "ollama")
    assert ollama["display_name"] == "本地 Ollama"
    assert ollama["display_status"] in ("无需 API Key", "需要 API Key")


def test_event_list_has_display_title(client):
    """事件列表：标题与状态是中文短语，机器值仍保留在 type/outcome。"""
    handle, headers = client
    from src.kernels.event import Event, EventScope, get_event_bus

    get_event_bus().publish(
        Event(
            type="account.self_registered",
            source="acceptance",
            data={"outcome": "success", "principal": "self1"},
            scope=EventScope.L0,
        )
    )

    body = handle.get("/v1/events", headers=headers).json()
    event = next(e for e in body["events"] if e["type"] == "account.self_registered")

    # 旧字段保留。
    assert event["type"] == "account.self_registered"
    assert event["data"]["outcome"] == "success"

    # 新增字段。
    assert event["display_title"] == "账号 · 自助注册（成功）"
    assert event["status_label"] == "成功"
    assert "success" not in event["display_title"]
    # 事件主界面字段不能出现 key=value / 点号链 / snake_case 机器串。
    assert machine_fragments(event["display_title"]) == []
    assert machine_fragments(event["status_label"]) == []


def test_alerts_have_user_message_and_technical_detail(client):
    """告警链路：一条人话 + 原始技术细节（后者只进诊断页）。"""
    handle, headers = client
    from src.observability.alerts import get_alert_store
    from src.observability.alerts.models import (
        Alert,
        AlertSeverity,
        AlertState,
        AlertType,
    )

    get_alert_store().emit_alert(
        Alert(
            id="display-alert-1",
            alert_type=AlertType.METRIC_THRESHOLD,
            name="CPU 使用率过高",
            severity=AlertSeverity.HIGH,
            message="cpu_usage=93% > 90%",
            source="metric",
            metric_name="cpu_usage",
            state=AlertState.FIRING,
        ),
        dispatch=False,
    )

    body = handle.get("/v1/alerts", headers=headers).json()
    assert body["user_message"]
    assert body["status_label"] == "正在告警"

    alert = next(a for a in body["alerts"] if a["id"] == "display-alert-1")
    # 旧字段保留。
    assert alert["severity"] == "HIGH"
    assert alert["state"] == "FIRING"
    assert alert["message"] == "cpu_usage=93% > 90%"

    # 新增字段。
    assert alert["severity_label"] == "严重"
    assert alert["status_label"] == "正在告警"
    assert "严重告警" in alert["user_message"]
    assert "CPU 使用率 93%" in alert["user_message"]
    assert "超过阈值 90%" in alert["user_message"]
    # 人话里不得出现 ``cpu_usage=93% > 90%`` 这类机器串；原始串只在技术细节里。
    assert machine_fragments(alert["user_message"]) == []
    assert "cpu_usage=93%" not in alert["user_message"]
    assert "cpu_usage=93%" in alert["technical_detail"]

    rules = handle.get("/v1/alerts/rules", headers=headers).json()["rules"]
    assert rules
    for rule in rules:
        assert rule["severity_label"] in ("提示", "注意", "严重", "紧急")
        assert rule["status_label"] in ("已启用", "未启用")
        assert rule["user_message"]
        assert machine_fragments(rule["user_message"]) == []


def test_health_and_ready_expose_user_message(client):
    """健康链路：每项检查补一句人话，机器字段仍在。"""
    handle, headers = client

    health = handle.get("/v1/health").json()
    assert health["status"] == "ok"  # 旧字段
    assert health["status_label"] == "正常"
    assert "运行" in health["user_message"]
    assert machine_fragments(health["user_message"]) == []
    provider = health["ai_provider"]
    assert provider["type"] and provider["mode"]  # 旧字段
    assert provider["display_name"]
    assert "·" in provider["display_status"]
    assert provider["status_label"]
    assert machine_fragments(provider["display_status"]) == []

    ready = handle.get("/v1/ready")
    assert ready.status_code in (200, 503)
    body = ready.json()
    assert body["user_message"]
    assert body["status_label"] in ("已就绪", "部分可用")
    for name, check in body["checks"].items():
        assert check["status"]  # 旧字段
        assert check["user_message"], name
        assert check["status_label"], name
        assert isinstance(check["technical_detail"], str) and check["technical_detail"], name
        assert machine_fragments(check["user_message"]) == [], name

    subsystems = handle.get("/v1/ready/subsystems", headers=headers).json()
    assert subsystems["user_message"]
    for subsystem in subsystems["subsystems"]:
        assert subsystem["display_name"]
        assert subsystem["user_message"]
        assert machine_fragments(subsystem["user_message"]) == [], subsystem["name"]


def test_preflight_findings_are_readable(client):
    """预检结论："阻断性发现"这类机器词不再裸露，且原 message 留作技术细节。"""
    handle, headers = client
    body = handle.get("/v1/production/preflight", headers=headers).json()

    assert "ok" in body and "findings" in body  # 旧字段
    assert body["user_message"]
    assert body["status_label"] in ("通过", "存在阻断项")
    for finding in body["findings"]:
        assert finding["severity"]  # 旧字段
        assert finding["severity_label"] in ("提示", "注意", "严重", "紧急")
        assert finding["display_title"]
        assert finding["user_message"]
        # 主界面只放人话：字段名/原始 message 不得混入 user_message。
        assert machine_fragments(finding["user_message"]) == []
        assert machine_fragments(finding["display_title"]) == []
        assert finding["message"] not in finding["user_message"]
        assert finding["message"] in finding["technical_detail"]
        assert isinstance(finding["technical_detail"], str) and finding["technical_detail"]
