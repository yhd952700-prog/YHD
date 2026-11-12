"""HD-06 — retention configuration tests (policy-neutral switch, default NO_DELETE)."""

import os
import importlib

import pytest

from src.kernels.retention import (
    RETENTION_POLICY_ENV,
    RetentionConfig,
    RetentionMode,
    load_retention_config,
    parse_policy_spec,
)


def test_default_is_no_delete_when_unset(monkeypatch):
    monkeypatch.delenv(RETENTION_POLICY_ENV, raising=False)
    cfg = load_retention_config()
    assert cfg.mode is RetentionMode.NO_DELETE
    assert cfg.protect_immutable_originals is True


def test_empty_spec_is_no_delete(monkeypatch):
    monkeypatch.setenv(RETENTION_POLICY_ENV, "")
    assert load_retention_config().mode is RetentionMode.NO_DELETE


def test_parse_no_delete():
    cfg = parse_policy_spec("NO_DELETE")
    assert cfg.mode is RetentionMode.NO_DELETE


def test_parse_archive_only():
    cfg = parse_policy_spec("ARCHIVE_ONLY")
    assert cfg.mode is RetentionMode.ARCHIVE_ONLY


def test_parse_delete_after_days():
    cfg = parse_policy_spec("DELETE_AFTER_DAYS:365")
    assert cfg.mode is RetentionMode.DELETE_AFTER_DAYS
    assert cfg.delete_after_days == 365
    assert "DERIVATIVE" in cfg.note


def test_parse_delete_after_days_rejects_nonpositive():
    with pytest.raises(ValueError):
        parse_policy_spec("DELETE_AFTER_DAYS:0")
    with pytest.raises(ValueError):
        parse_policy_spec("DELETE_AFTER_DAYS:-5")


def test_parse_unknown_mode_raises():
    with pytest.raises(ValueError):
        parse_policy_spec("WIPE_EVERYTHING")


def test_parse_delete_after_days_without_horizon_raises():
    with pytest.raises(ValueError):
        parse_policy_spec("DELETE_AFTER_DAYS:")


def test_env_var_name_is_reserved_and_stable():
    assert RETENTION_POLICY_ENV == "LIUHAO_RETENTION_POLICY"
