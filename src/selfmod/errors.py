"""Exception types for controlled self-modification."""

from __future__ import annotations


class SelfModError(Exception):
    """Base class for controlled self-modification failures."""


class PathNotAllowed(SelfModError):
    """A proposed path is outside the workspace or in a protected area."""


class InvalidEdit(SelfModError):
    """The edit cannot be represented safely (binary file / too large)."""


class ProposalNotFound(SelfModError):
    """No proposal with that id exists."""


class ConfirmationRequired(SelfModError):
    """A human confirmation token is required (and must be valid)."""


class StaleProposal(SelfModError):
    """The workspace changed after the proposal was generated."""


class TestCommandMissing(SelfModError):
    """No test command was provided, so a verdict is impossible."""


class TestCommandNotAllowed(SelfModError):
    """The test command is not one of the repository's own test/gate commands."""


class PromotionDisabled(SelfModError):
    """Promotion to the real workspace is disabled (copy-only mode)."""


class SandboxError(SelfModError):
    """The isolated runner could not start the test command."""
