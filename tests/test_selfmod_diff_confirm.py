"""Diff generation, path policy and human confirmation gates."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.selfmod import (
    ConfirmationRequired,
    FileEdit,
    PathNotAllowed,
    SelfModService,
    StaleProposal,
)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    (root / "app.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (root / "data").mkdir()
    (root / "data" / "state.json").write_text('{"v": 1}', encoding="utf-8")
    return root


def _service(workspace: Path, tmp_path: Path, **kwargs) -> SelfModService:
    # Test-local allowlist: the production default only accepts the repo's own
    # suite/gates (see tests/test_selfmod_command_whitelist.py). These tests
    # exercise diff/confirm mechanics, so they declare their own probe command.
    kwargs.setdefault("allowed_test_commands", [["python", "-c", "pass"]])
    return SelfModService(workspace, state_dir=tmp_path / "state", **kwargs)


def test_propose_generates_diff_without_touching_workspace(
    workspace: Path, tmp_path: Path
) -> None:
    before = (workspace / "app.py").read_text(encoding="utf-8")
    service = _service(workspace, tmp_path)
    try:
        proposal = service.propose(
            title="add RESULT",
            rationale="test diff rendering",
            edits=[FileEdit("app.py", before + "\nRESULT = 3\n")],
            test_command=["python", "-c", "pass"],
        )
        assert "--- a/app.py" in proposal["diff"]
        assert "+++ b/app.py" in proposal["diff"]
        assert "@@" in proposal["diff"]
        assert "+RESULT = 3" in proposal["diff"]
        assert (workspace / "app.py").read_text(encoding="utf-8") == before
        shown = service.show_diff(proposal["id"])
        assert "提案" in shown and "--- a/app.py" in shown
        assert service.events(proposal["id"])[0]["kind"] == "propose"
    finally:
        service.close()


def test_propose_rejects_absolute_and_traversal_paths(
    workspace: Path, tmp_path: Path
) -> None:
    service = _service(workspace, tmp_path)
    try:
        for bad in ("../escape.py", "/absolute.py", "C:\\evil.py", "data/state.json", ".env"):
            with pytest.raises(PathNotAllowed):
                service.propose(
                    title="bad path",
                    rationale="must be refused",
                    edits=[FileEdit(bad, "x")],
                    test_command=["python", "-c", "pass"],
                )
    finally:
        service.close()


def test_run_requires_confirmation_and_real_token(
    workspace: Path, tmp_path: Path
) -> None:
    service = _service(
        workspace,
        tmp_path,
        default_test_command=["python", "-c", "pass"],
    )
    try:
        proposal = service.propose(
            title="t",
            rationale="r",
            edits=[FileEdit("app.py", "def add(a, b):\n    return a + b\nRESULT = 3\n")],
        )
        with pytest.raises(ConfirmationRequired):
            service.run_on_copy(proposal["id"], "not-confirmed")
        with pytest.raises(ConfirmationRequired):
            service.confirm(proposal["id"], approver="")
        token = service.confirm(proposal["id"], approver="owner")
        assert isinstance(token, str) and len(token) > 20
        with pytest.raises(ConfirmationRequired):
            service.run_on_copy(proposal["id"], "wrong-token")
        # The wrong token attempt must not consume the valid one.
        result = service.run_on_copy(proposal["id"], token)
        assert result["status"] == "passed"
        assert result["real_workspace_modified"] is False
        with pytest.raises(ConfirmationRequired):
            service.run_on_copy(proposal["id"], token)
    finally:
        service.close()


def test_stale_proposal_is_refused(workspace: Path, tmp_path: Path) -> None:
    service = _service(
        workspace,
        tmp_path,
        default_test_command=["python", "-c", "pass"],
    )
    try:
        proposal = service.propose(
            title="stale",
            rationale="r",
            edits=[FileEdit("app.py", "def add(a, b):\n    return a + b\nRESULT = 3\n")],
        )
        (workspace / "app.py").write_text("EXTERNAL_CHANGE = 1\n", encoding="utf-8")
        token = service.confirm(proposal["id"], approver="owner")
        with pytest.raises(StaleProposal):
            service.run_on_copy(proposal["id"], token)
        assert service.get(proposal["id"])["status"] == "rejected"
        assert (workspace / "app.py").read_text(encoding="utf-8") == "EXTERNAL_CHANGE = 1\n"
    finally:
        service.close()


def test_missing_test_command_is_rejected(workspace: Path, tmp_path: Path) -> None:
    service = _service(workspace, tmp_path)
    try:
        proposal = service.propose(
            title="no test",
            rationale="r",
            edits=[FileEdit("app.py", "X = 1\n")],
        )
        token = service.confirm(proposal["id"], approver="owner")
        with pytest.raises(Exception) as exc:
            service.run_on_copy(proposal["id"], token)
        assert "未配置测试命令" in str(exc.value)
        assert service.get(proposal["id"])["status"] == "rejected"
        assert (workspace / "app.py").read_text(encoding="utf-8").startswith("def add")
    finally:
        service.close()
