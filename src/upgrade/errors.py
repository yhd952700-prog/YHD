"""Exception types for the self-upgrade module.

Every failure is a distinct, catchable type so the caller (CLI, future
console/API surface) can show the user *why* an upgrade stopped instead of a
generic "failed". All messages are written in Chinese because they are
surfaced to the end user verbatim.
"""

from __future__ import annotations


class UpgradeError(Exception):
    """Base class for every upgrade failure."""


class VersionReadError(UpgradeError):
    """The current application version could not be determined."""


class ManifestError(UpgradeError):
    """The release manifest is missing or malformed."""


class UpdateUnreachableError(UpgradeError):
    """The update source could not be reached (offline / HTTP failure)."""


class ChecksumMismatch(UpgradeError):
    """The downloaded artifact does not match the manifest SHA256."""


class SignatureError(UpgradeError):
    """A declared signature is present but cannot be verified."""


class PackageValidationError(UpgradeError):
    """The release archive is unsafe or does not contain the declared version."""


class PreflightError(UpgradeError):
    """Data integrity preflight failed; nothing was modified."""


class ApplyError(UpgradeError):
    """Applying the release failed (the manager has already rolled back)."""


class RollbackError(UpgradeError):
    """Restoring the previous version failed."""


class NotConfiguredError(UpgradeError):
    """No update source was configured, so checking is impossible."""
