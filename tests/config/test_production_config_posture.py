"""Posture-routing test for ``config.production.config.get_config``.

Ensures the production decision honors the unified posture resolver
(``LIUHAO_ENV`` > ``ENVIRONMENT`` > ``APP_ENV``), not ``ENVIRONMENT`` alone —
the same P0 class of bug that ``src/security/posture`` was created to fix.

``get_config`` reads posture via ``is_production()`` (which consults
``os.environ``), so we drive it with ``monkeypatch.setenv`` rather than an
explicit mapping. The ``src.security.posture`` import is lazy inside
``get_config`` and therefore cannot break import-time bootstrapping of the
config module.
"""

from __future__ import annotations

from config.production.config import Environment, get_config


def test_liuhao_env_production_wins_without_environment(monkeypatch):
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setenv("LIUHAO_ENV", "production")
    assert get_config().environment == Environment.PRODUCTION


def test_explicit_environment_still_wins(monkeypatch):
    monkeypatch.delenv("LIUHAO_ENV", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "staging")
    # An explicit argument must bypass the resolver entirely.
    assert get_config("production").environment == Environment.PRODUCTION


def test_environment_var_staging_preserved(monkeypatch):
    monkeypatch.delenv("LIUHAO_ENV", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "staging")
    assert get_config().environment == Environment.STAGING


def test_unset_falls_back_to_development(monkeypatch):
    monkeypatch.delenv("LIUHAO_ENV", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    assert get_config().environment == Environment.DEVELOPMENT
