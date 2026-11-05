"""鎏灏受控自改代码（设计 + 最小闭环）。

The runtime never edits itself directly. See ``docs/self-upgrade.md`` for the
full workflow, capability boundary and rollback procedure.

Public entry point::

    from src.selfmod import SelfModService, FileEdit, ResourceLimits

    service = SelfModService(".", default_test_command=[...])
    proposal = service.propose(title="...", rationale="...", edits=[FileEdit(...) ])
    print(service.show_diff(proposal["id"]))
"""

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
)
from .models import FileEdit, Proposal, ProposalStatus, ResourceLimits, RunResult
from .patcher import generate_diff, render
from .paths import PathPolicy
from .sandbox import command_from, run_isolated
from .service import SelfModService

__all__ = [
    "ConfirmationRequired",
    "FileEdit",
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
    "TestCommandMissing",
    "command_from",
    "generate_diff",
    "render",
    "run_isolated",
]
