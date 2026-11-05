"""鎏灏自我升级模块（安全、可回滚、用户可见）。

Public entry point::

    from src.upgrade import UpgradeConfig, UpgradeManager, LocalDirectorySource

    manager = UpgradeManager(UpgradeConfig(app_root=".", source=LocalDirectorySource("releases")))
    check = manager.check()
    artifact = manager.download(check.release)
    manager.apply(artifact.path, check.release)

See ``docs/self-upgrade.md`` for the capability boundary and rollback steps.
"""

from .downloader import cleanup_download, download_release
from .errors import (
    ApplyError,
    BackupCorrupt,
    BackupNotFound,
    ChecksumMismatch,
    DiskSpaceError,
    ManifestError,
    PackageValidationError,
    PreflightError,
    RollbackError,
    SelfCheckError,
    SignatureError,
    SnapshotError,
    UpdateUnreachableError,
    UpgradeError,
    VersionReadError,
)
from .manager import UpgradeConfig, UpgradeManager
from .models import (
    ApplyResult,
    CheckResult,
    CheckStatus,
    DownloadResult,
    Phase,
    ReleaseInfo,
    RollbackResult,
)
from .sources import (
    HttpManifestSource,
    LocalDirectorySource,
    ReleaseSource,
    build_source,
    select_latest,
)
from .versioning import is_newer, read_current_version

__all__ = [
    "ApplyError",
    "ApplyResult",
    "BackupCorrupt",
    "BackupNotFound",
    "CheckResult",
    "CheckStatus",
    "ChecksumMismatch",
    "DiskSpaceError",
    "DownloadResult",
    "HttpManifestSource",
    "LocalDirectorySource",
    "ManifestError",
    "PackageValidationError",
    "Phase",
    "PreflightError",
    "ReleaseInfo",
    "ReleaseSource",
    "RollbackError",
    "RollbackResult",
    "SelfCheckError",
    "SignatureError",
    "SnapshotError",
    "UpdateUnreachableError",
    "UpgradeConfig",
    "UpgradeError",
    "UpgradeManager",
    "VersionReadError",
    "build_source",
    "cleanup_download",
    "download_release",
    "is_newer",
    "read_current_version",
    "select_latest",
]
