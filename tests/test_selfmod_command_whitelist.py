"""The test command that judges a proposal comes from an allowlist.

Before this gate, ``propose`` accepted any ``--test-command``, so a patch author
could hand themselves a green verdict with ``python -c "raise SystemExit(0)"``.
Both tests below pin the deny-by-default behaviour.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

from src.selfmod import (
    FileEdit,
    PathPolicy,
    SelfModService,
    TestCommandNotAllowed as CommandNotAllowed,
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
    return SelfModService(workspace, state_dir=tmp_path / "state", **kwargs)


def test_arbitrary_test_command_is_refused(workspace: Path, tmp_path: Path) -> None:
    service = _service(workspace, tmp_path)
    try:
        with pytest.raises(CommandNotAllowed):
            service.propose(
                title="self-certifying patch",
                rationale="the author must not pick the ruler",
                edits=[FileEdit("app.py", "X = 1\n")],
                test_command=[sys.executable, "-c", "raise SystemExit(0)"],
            )
        assert service.list() == []
    finally:
        service.close()


def test_subset_of_the_suite_is_also_refused(workspace: Path, tmp_path: Path) -> None:
    """Choosing *which* tests run is the same defect one level down."""
    service = _service(workspace, tmp_path)
    try:
        with pytest.raises(CommandNotAllowed):
            service.propose(
                title="narrow the suite",
                rationale="only run the tests I know pass",
                edits=[FileEdit("app.py", "X = 1\n")],
                test_command=["python", "-m", "pytest", "tests/test_one.py"],
            )
    finally:
        service.close()


def test_full_suite_command_is_accepted(workspace: Path, tmp_path: Path) -> None:
    service = _service(workspace, tmp_path)
    try:
        proposal = service.propose(
            title="legitimate patch",
            rationale="measured by the repository's own full suite",
            edits=[FileEdit("app.py", "X = 1\n")],
            test_command=["python", "-m", "pytest"],
        )
        assert proposal["test_command"] == [sys.executable, "-m", "pytest"]
    finally:
        service.close()


def test_invalid_default_test_command_fails_at_construction(
    workspace: Path, tmp_path: Path
) -> None:
    with pytest.raises(CommandNotAllowed):
        _service(
            workspace,
            tmp_path,
            default_test_command=[sys.executable, "-c", "pass"],
        )


def test_legacy_ledger_row_with_arbitrary_command_is_refused(
    workspace: Path, tmp_path: Path
) -> None:
    """Rows written before the allowlist (or edited by hand) must not execute."""
    service = _service(
        workspace,
        tmp_path,
        allowed_test_commands=[["python", "-c", "pass"]],
    )
    try:
        proposal_id = uuid.uuid4().hex
        policy = PathPolicy(workspace)
        base_hashes = {policy.normalise("app.py"): None}
        service.ledger.create(
            proposal_id=proposal_id,
            title="legacy row",
            rationale="predates the allowlist",
            status="pending",
            base_version="0.1.0",
            diff_text="",
            edits=[{"path": "app.py", "content": "X = 1\n", "delete": False}],
            base_hashes=base_hashes,
            test_command=[sys.executable, "-c", "raise SystemExit(0)"],
        )
        token = service.confirm(proposal_id, approver="owner")
        with pytest.raises(CommandNotAllowed):
            service.run_on_copy(proposal_id, token)
        assert service.get(proposal_id)["status"] == "rejected"
        assert (workspace / "app.py").read_text(encoding="utf-8") == ORIGINAL
    finally:
        service.close()
