"""联网检索开关：立即生效 + 开启动作留审计。"""
from __future__ import annotations

import pytest

from src.ai import web_research
from src.gateway import networks


@pytest.fixture
def isolated_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("LIUHAO_PRIVACY_SETTINGS_FILE", str(tmp_path / "privacy.json"))
    monkeypatch.delenv(web_research.ENABLE_ENV, raising=False)
    return tmp_path / "privacy.json"


def test_toggle_takes_effect_immediately_without_restart(isolated_settings):
    assert web_research.enabled() is False

    state = networks.set_research_setting(
        networks.ResearchToggle(enabled=True), principal="user:alice"
    )
    assert state["enabled"] is True
    assert web_research.enabled() is True

    state = networks.set_research_setting(
        networks.ResearchToggle(enabled=False), principal="user:alice"
    )
    assert state["enabled"] is False
    assert web_research.enabled() is False


def test_enabling_writes_audit_with_actor_and_targets(isolated_settings, monkeypatch):
    calls = []

    def fake_emit(event_type, **fields):
        calls.append((event_type, fields))
        return True

    monkeypatch.setattr(networks._audit, "emit", fake_emit)

    networks.set_research_setting(
        networks.ResearchToggle(enabled=True), principal="user:alice"
    )

    assert len(calls) == 1
    event_type, fields = calls[0]
    assert event_type == "p9.privacy.web_research_toggle"
    assert fields["actor"] == "user:alice"
    assert fields["enabled"] is True
    assert fields["requested"] is True
    assert fields["changed"] is True
    for host in web_research.PROVIDER_HOSTS.values():
        assert host in fields["hosts"]


def test_disabling_also_leaves_a_trace(isolated_settings, monkeypatch):
    calls = []
    monkeypatch.setattr(
        networks._audit, "emit", lambda event_type, **fields: calls.append(fields) or True
    )

    networks.set_research_setting(
        networks.ResearchToggle(enabled=False), principal="user:alice"
    )

    assert calls[0]["enabled"] is False
    assert calls[0]["requested"] is False
