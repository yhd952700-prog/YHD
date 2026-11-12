"""U1 — host-command enablement gate tests (default OFF / DENY)."""

import pytest

from src.ai.host_command.enablement import (
    ENV_VAR,
    describe,
    is_enabled,
    parse_enablement,
    reload,
)


@pytest.fixture(autouse=True)
def _clean_gate(monkeypatch):
    monkeypatch.delenv(ENV_VAR, raising=False)
    reload()
    yield
    reload()


def test_default_is_disabled():
    assert is_enabled() is False


def test_parse_enablement_true_values():
    for v in ("true", "1", "yes", "on", "TRUE"):
        assert parse_enablement(v) is True


def test_parse_enablement_false_values():
    for v in ("", "false", "no", "off", "banana", None):
        assert parse_enablement(v) is False


def test_enabled_when_set(monkeypatch):
    monkeypatch.setenv(ENV_VAR, "true")
    reload()
    assert is_enabled() is True


def test_reload_picks_up_change(monkeypatch):
    assert is_enabled() is False
    monkeypatch.setenv(ENV_VAR, "1")
    reload()
    assert is_enabled() is True
    monkeypatch.delenv(ENV_VAR)
    reload()
    assert is_enabled() is False


def test_describe_reports_state():
    d = describe()
    assert d["env_var"] == ENV_VAR
    assert d["enabled"] is False
