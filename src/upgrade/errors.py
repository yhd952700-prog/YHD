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


class DiskSpaceError(UpgradeError):
    """There is not enough free space; the upgrade stopped before mutation."""


class ApplyError(UpgradeError):
    """Applying the release failed (the manager has already rolled back)."""


class RollbackError(UpgradeError):
    """Restoring the previous version failed."""


class BackupNotFound(RollbackError):
    """The pre-upgrade backup (or the old version package inside it) is missing."""


class BackupCorrupt(RollbackError):
    """A backup exists but its SHA256 no longer matches, so it is unusable."""


class NotConfiguredError(UpgradeError):
    """No update source was configured, so checking is impossible."""


class SelfCheckError(UpgradeError):
    """The post-install self-check failed; the manager has rolled back."""


class SnapshotError(UpgradeError):
    """A pre-upgrade data snapshot could not be resolved or exported."""
