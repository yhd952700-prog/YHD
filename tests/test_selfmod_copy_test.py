"""Copy-isolated test execution, promote/rollback, timeout and resource limits."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from src.selfmod import (
    ConfirmationRequired,
    FileEdit,
    PromotionDisabled,
    ResourceLimits,
    SelfModError,
    SelfModService,
)

ORIGINAL = "def add(a, b):\n    return a + b\n"


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    (root / "app.py").write_text(ORIGINAL, encoding="utf-8")
    return root


def _service(workspace: Path, tmp_path: Path, **kwargs) -> SelfModService:
    # Each drill declares the exact probe command it uses; the production default
    # allowlist only accepts the repository's own suite/gates.
    kwargs.setdefault("allowed_test_commands", _probe_allowlist())
    return SelfModService(workspace, state_dir=tmp_path / "state", **kwargs)


def _timeout_command() -> list[str]:
    return [sys.executable, "-c", "import time; time.sleep(30)"]


def _cpu_command() -> list[str]:
    return [sys.executable, "-c", "while True: pass"]


def _env_command() -> list[str]:
    code = (
        "import os, pathlib; "
        "print('LEAKED' if os.environ.get('LIUHAO_SELFTEST_API_KEY') else 'STRIPPED'); "
        "pathlib.Path('marker.txt').write_text('copy-only')"
    )
    return [sys.executable, "-c", code]


def _probe_allowlist() -> list[list[str]]:
    return [_passing_command(), _timeout_command(), _cpu_command(), _env_command()]


def _passing_command() -> list[str]:
    code = (
        "import pathlib, app; "
        "assert app.RESULT == 3, app.RESULT; "
        "assert app.add(2, 3) == 5; "
        "pathlib.Path('marker.txt').write_text('copy-only')"
    )
    return [sys.executable, "-c", code]


def _proposal_content() -> str:
    return "RESULT = 3\n\n" + ORIGINAL


def test_copy_pass_leaves_real_workspace_untouched(
    workspace: Path, tmp_path: Path
) -> None:
    service = _service(workspace, tmp_path)
    try:
        proposal = service.propose(
            title="add RESULT",
            rationale="verified on a copy",
            edits=[FileEdit("app.py", _proposal_content())],
            test_command=_passing_command(),
        )
        token = service.confirm(proposal["id"], approver="owner")
        result = service.run_on_copy(proposal["id"], token)
        assert result["status"] == "passed"
        assert result["real_workspace_modified"] is False
        assert result["test"]["returncode"] == 0
        assert result["test"]["timed_out"] is False
        assert (workspace / "app.py").read_text(encoding="utf-8") == ORIGINAL
        assert not (workspace / "marker.txt").exists()
        stored = service.get(proposal["id"])
        assert stored["status"] == "passed"
        assert stored["test_summary"]["passed"] is True
    finally:
        service.close()


def test_failing_tests_reject_and_keep_real_workspace(
    workspace: Path, tmp_path: Path
) -> None:
    service = _service(workspace, tmp_path)
    try:
        proposal = service.propose(
            title="wrong value",
            rationale="must fail",
            edits=[FileEdit("app.py", "RESULT = 4\n\n" + ORIGINAL)],
            test_command=_passing_command(),
        )
        token = service.confirm(proposal["id"], approver="owner")
        result = service.run_on_copy(proposal["id"], token)
        assert result["status"] == "rejected"
        assert "测试失败" in result["reason"]
        assert result["test"]["returncode"] != 0
        assert (workspace / "app.py").read_text(encoding="utf-8") == ORIGINAL
        assert not (workspace / "marker.txt").exists()
    finally:
        service.close()


def test_timeout_kills_test_and_rejects(workspace: Path, tmp_path: Path) -> None:
    service = _service(
        workspace,
        tmp_path,
        limits=ResourceLimits(timeout_seconds=2.0, memory_mb=1024),
    )
    try:
        proposal = service.propose(
            title="slow",
            rationale="timeout drill",
            edits=[FileEdit("app.py", "X = 1\n")],
            test_command=_timeout_command(),
        )
        token = service.confirm(proposal["id"], approver="owner")
        result = service.run_on_copy(proposal["id"], token)
        assert result["status"] == "rejected"
        assert result["test"]["timed_out"] is True
        assert result["test"]["limit_reason"] == "timeout"
        assert result["test"]["duration_seconds"] < 10
        assert "超时" in result["reason"]
        assert (workspace / "app.py").read_text(encoding="utf-8") == ORIGINAL
    finally:
        service.close()


def test_cpu_limit_is_enforced(workspace: Path, tmp_path: Path) -> None:
    service = _service(
        workspace,
        tmp_path,
        limits=ResourceLimits(
            timeout_seconds=20.0,
            memory_mb=1024,
            cpu_seconds=0.5,
            poll_interval_seconds=0.05,
        ),
    )
    try:
        proposal = service.propose(
            title="cpu",
            rationale="resource ceiling drill",
            edits=[FileEdit("app.py", "X = 1\n")],
            test_command=_cpu_command(),
        )
        token = service.confirm(proposal["id"], approver="owner")
        result = service.run_on_copy(proposal["id"], token)
        assert result["status"] == "rejected"
        assert result["test"]["limit_reason"] == "cpu"
        assert "CPU" in result["reason"]
        assert (workspace / "app.py").read_text(encoding="utf-8") == ORIGINAL
    finally:
        service.close()


def test_promotion_disabled_by_default(workspace: Path, tmp_path: Path) -> None:
    service = _service(workspace, tmp_path)
    try:
        proposal = service.propose(
            title="promote",
            rationale="needs explicit mode",
            edits=[FileEdit("app.py", _proposal_content())],
            test_command=_passing_command(),
        )
        test_token = service.confirm(proposal["id"], approver="owner")
        result = service.run_on_copy(proposal["id"], test_token)
        assert result["status"] == "passed"
        promote_token = service.confirm(
            proposal["id"], approver="owner", stage="promote"
        )
        with pytest.raises(PromotionDisabled):
            service.finalize(proposal["id"], promote_token)
        assert (workspace / "app.py").read_text(encoding="utf-8") == ORIGINAL
    finally:
        service.close()


def test_promote_requires_passed_status_and_a_second_token(
    workspace: Path, tmp_path: Path
) -> None:
    service = _service(workspace, tmp_path, allow_promote=True)
    try:
        proposal = service.propose(
            title="promote",
            rationale="two-stage confirmation",
            edits=[FileEdit("app.py", _proposal_content())],
            test_command=_passing_command(),
        )
        with pytest.raises(ConfirmationRequired):
            service.confirm(proposal["id"], approver="owner", stage="promote")

        test_token = service.confirm(proposal["id"], approver="owner")
        result = service.run_on_copy(proposal["id"], test_token)
        assert result["status"] == "passed"

        promote_token = service.confirm(
            proposal["id"], approver="owner", stage="promote"
        )
        with pytest.raises(ConfirmationRequired):
            service.finalize(proposal["id"], "wrong-token")
        assert (workspace / "app.py").read_text(encoding="utf-8") == ORIGINAL

        applied = service.finalize(proposal["id"], promote_token)
        assert applied["status"] == "applied"
        assert applied["backup_dir"]
        assert (workspace / "app.py").read_text(encoding="utf-8") == _proposal_content()

        rolled = service.rollback(proposal["id"])
        assert rolled["status"] == "rolled_back"
        assert (workspace / "app.py").read_text(encoding="utf-8") == ORIGINAL
        assert service.events(proposal["id"])[0]["kind"] == "rollback"
    finally:
        service.close()


def test_credential_shaped_env_vars_are_not_inherited(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The test child must not see inherited ``*_API_KEY`` style variables."""
    monkeypatch.setenv("LIUHAO_SELFTEST_API_KEY", "must-not-leak")
    service = _service(workspace, tmp_path)
    try:
        proposal = service.propose(
            title="env isolation",
            rationale="credential-shaped variables are dropped",
            edits=[FileEdit("app.py", ORIGINAL + "\nX = 1\n")],
            test_command=_env_command(),
        )
        token = service.confirm(proposal["id"], approver="owner")
        result = service.run_on_copy(proposal["id"], token)
        assert result["status"] == "passed"
        assert "STRIPPED" in result["test"]["output_tail"]
        assert "LEAKED" not in result["test"]["output_tail"]
        assert not (workspace / "marker.txt").exists()
    finally:
        service.close()


def test_rollback_requires_an_applied_proposal(workspace: Path, tmp_path: Path) -> None:
    service = _service(workspace, tmp_path)
    try:
        proposal = service.propose(
            title="not applied",
            rationale="rollback must refuse",
            edits=[FileEdit("app.py", _proposal_content())],
            test_command=_passing_command(),
        )
        with pytest.raises(SelfModError):
            service.rollback(proposal["id"])
        assert (workspace / "app.py").read_text(encoding="utf-8") == ORIGINAL
    finally:
        service.close()
