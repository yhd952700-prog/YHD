"""Readiness probe must be able to report ``not_ready`` (silent-success sweep).

``MetricsApplication.ready()`` used to return
``"ready" if count >= 0 else "not_ready"`` -- a tautology, because SQL
``COUNT(*)`` is never negative. The ``not_ready`` branch was therefore
unreachable: the readiness surface (consumed by the ``/ready`` HTTP endpoint,
``LifecycleManager.startup()`` and the ``main.py ready`` CLI) reported healthy
without probing anything -- a fail-open control that could never fail.

These tests pin the honest behaviour: a healthy store reports ``ready`` and a
genuinely unreachable store reports ``not_ready`` (proving the branch is now
reachable and the old tautology is gone).
"""

from src.api.app import MetricsApplication


def _db_url(tmp_path, name: str) -> str:
    return f"sqlite:///{(tmp_path / name).as_posix()}"


def test_ready_reports_ready_for_a_healthy_store(tmp_path) -> None:
    app = MetricsApplication(_db_url(tmp_path, "ready_ok.db"))

    result = app.ready()

    assert result["status"] == "ready"
    assert result["provider_samples"] >= 0


def test_ready_reports_not_ready_when_store_is_unreachable(tmp_path) -> None:
    # Negative control: prove the ``not_ready`` branch is REACHABLE. Break the
    # store the way a lost DB connection would (close the connection), then
    # assert the probe reports not_ready instead of failing open.
    app = MetricsApplication(_db_url(tmp_path, "ready_broken.db"))
    assert app.ready()["status"] == "ready"

    app.repository._conn.close()

    result = app.ready()
    assert result["status"] == "not_ready"
    assert result["provider_samples"] == 0


def test_is_ready_is_a_real_probe_not_a_tautology(tmp_path) -> None:
    repo = MetricsApplication(_db_url(tmp_path, "ready_probe.db")).repository

    assert repo.is_ready() is True

    repo._conn.close()

    assert repo.is_ready() is False
