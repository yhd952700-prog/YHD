"""鎏灏受控自改代码（设计 + 最小闭环）。

The runtime never edits itself directly. See ``docs/self-upgrade.md`` for the
full workflow, capability boundary and rollback procedure.

Public entry point::

    from src.selfmod import SelfModService, FileEdit, ResourceLimits

    service = SelfModService(".", default_test_command=[...])
    proposal = service.propose(title="...", rationale="...", edits=[FileEdit(...) ])
    print(service.show_diff(proposal["id"]))
"""

from .commands import DEFAULT_ALLOWED_TEST_COMMANDS, GateCommandPolicy, TestCommandPolicy
from .errors import (
    ConfirmationRequired,
    InvalidEdit,
    PathNotAllowed,
    ProposalNotFound,
    PromotionDisabled,
    SandboxError,
    SelfModError,
    StaleProposal,
    TestCommandMissing,
    TestCommandNotAllowed,
)
from .evidence import (
    build_evidence,
    evidence_path,
    read_evidence,
    validate_evidence,
    write_evidence,
)
from .gates import GateReport, GateResult, diff_stat, run_gates
from .models import FileEdit, Proposal, ProposalStatus, ResourceLimits, RunResult
from .patcher import generate_diff, render
from .paths import PathPolicy
from .sandbox import command_from, run_isolated
from .service import SelfModService

__all__ = [
    "ConfirmationRequired",
    "DEFAULT_ALLOWED_TEST_COMMANDS",
    "FileEdit",
    "GateCommandPolicy",
    "GateReport",
    "GateResult",
    "InvalidEdit",
    "PathNotAllowed",
    "PathPolicy",
    "Proposal",
    "ProposalNotFound",
    "ProposalStatus",
    "PromotionDisabled",
    "ResourceLimits",
    "RunResult",
    "SandboxError",
    "SelfModError",
    "SelfModService",
    "StaleProposal",
    "TestCommandPolicy",
    "TestCommandMissing",
    "TestCommandNotAllowed",
    "build_evidence",
    "command_from",
    "diff_stat",
    "evidence_path",
    "generate_diff",
    "read_evidence",
    "render",
    "run_gates",
    "run_isolated",
    "validate_evidence",
    "write_evidence",
]
