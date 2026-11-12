"""Unified deployment-posture detection tests (P0 posture-unify).

These tests prove the real-world defect is fixed: the production container sets
``ENVIRONMENT=production`` (and ``APP_ENV=staging`` in the staging compose), but
the old ``is_production()`` read only ``LIUHAO_ENV`` which was never set -- so
production silently took the non-production branch. The regression that would
have caught the original defect is :func:`test_environment_production_only`.
"""

from __future__ import annotations

import logging

import pytest

from src.security.posture import (
    APP_ENV_ENV,
    ENVIRONMENT_ENV,
    LIUHAO_ENV,
    STRICT_ENV,
    PostureUndeterminable,
    deployment_posture,
    describe_posture,
    is_production,
)

POSTURE_LOGGER = "src.security.posture"


def _blank_env():
    """A mapping where every posture alias is absent (undeterminable)."""
    return {}


# --------------------------------------------------------------------------- #
# The real-world regressions
# --------------------------------------------------------------------------- #
def test_environment_production_only():
    # The actual production reality: only ENVIRONMENT=production is set.
    # This is the test that would have caught today's defect.
    assert is_production(env={ENVIRONMENT_ENV: "production"}) is True
    posture = deployment_posture(env={ENVIRONMENT_ENV: "production"})
    assert posture.is_production is True
    assert posture.source == ENVIRONMENT_ENV
    assert posture.determinable is True


def test_app_env_production_honored():
    # The staging compose defaults APP_ENV to "staging"; if a host declares
    # APP_ENV=production the gate must honor it.
    assert is_production(env={APP_ENV_ENV: "production"}) is True
    posture = deployment_posture(env={APP_ENV_ENV: "production"})
    assert posture.source == APP_ENV_ENV
    assert posture.determinable is True


def test_liuhao_env_takes_precedence():
    # Explicit, app-namespaced override must win over the compose-set vars.
    env = {
        LIUHAO_ENV: "production",
        ENVIRONMENT_ENV: "staging",
        APP_ENV_ENV: "development",
    }
    assert is_production(env=env) is True
    posture = deployment_posture(env=env)
    assert posture.source == LIUHAO_ENV
    assert posture.is_production is True


def test_liuhao_env_production_wins_over_conflicting_env():
    # A stale ENVIRONMENT=staging must NOT downgrade an explicit production set.
    env = {LIUHAO_ENV: "production", ENVIRONMENT_ENV: "staging"}
    posture = deployment_posture(env=env)
    assert posture.is_production is True
    assert posture.source == LIUHAO_ENV
    # The two disagree -> flagged as a conflict for operator visibility.
    assert posture.conflict is True


def test_case_insensitive_prod():
    for raw in ("Prod", "PRODUCTION", "prod"):
        assert is_production(env={LIUHAO_ENV: raw}) is True
    # A non-production value is a *determined* non-production (no warning).
    posture = deployment_posture(env={APP_ENV_ENV: "staging"})
    assert posture.determinable is True
    assert posture.is_production is False


# --------------------------------------------------------------------------- #
# Undeterminable posture (must not be silent)
# --------------------------------------------------------------------------- #
def test_undeterminable_non_strict_warns_and_false(caplog):
    with caplog.at_level(logging.WARNING, logger=POSTURE_LOGGER):
        result = is_production(env=_blank_env())
    assert result is False
    posture = deployment_posture(env=_blank_env())
    assert posture.determinable is False
    assert posture.source is None
    # The key property: a silent False is gone -- a warning was emitted.
    assert any("UNDETERMINABLE" in r.message for r in caplog.records)


def test_undeterminable_strict_raises():
    with pytest.raises(PostureUndeterminable):
        is_production(env=_blank_env(), strict=True)
    # Opt-in via the env var also hard-fails (default OFF otherwise).
    with pytest.raises(PostureUndeterminable):
        is_production(env={STRICT_ENV: "1"})
    with pytest.raises(PostureUndeterminable):
        is_production(env={STRICT_ENV: "true"})


def test_strict_off_default_does_not_raise():
    # Default (no opt-in) must remain compatible with dev/test runs.
    assert is_production(env=_blank_env()) is False


# --------------------------------------------------------------------------- #
# Observability + isolation
# --------------------------------------------------------------------------- #
def test_describe_posture_provenance():
    env = {ENVIRONMENT_ENV: "production"}
    report = describe_posture(env=env)
    assert report["is_production"] is True
    assert report["source"] == ENVIRONMENT_ENV
    assert report["determinable"] is True
    assert report["conflict"] is False
    assert set(report["vars"].keys()) == {LIUHAO_ENV, ENVIRONMENT_ENV, APP_ENV_ENV}
    assert report["vars"][ENVIRONMENT_ENV] == "production"


def test_env_param_does_not_mutate_process_environ():
    before = dict(os_environ_snapshot())
    # Passing an explicit mapping must not touch os.environ.
    is_production(env={APP_ENV_ENV: "production"})
    after = dict(os_environ_snapshot())
    assert before == after


def os_environ_snapshot():
    import os

    return {k: v for k, v in os.environ.items()
            if k in (LIUHAO_ENV, ENVIRONMENT_ENV, APP_ENV_ENV, STRICT_ENV)}
