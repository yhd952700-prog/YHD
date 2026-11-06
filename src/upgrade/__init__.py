"""鎏灏自我升级模块（安全、可回滚、用户可见）。

Public entry point::

    from src.upgrade import UpgradeConfig, UpgradeManager, LocalDirectorySource

    manager = UpgradeManager(UpgradeConfig(app_root=".", source=LocalDirectorySource("releases")))
    check = manager.check()
    artifact = manager.download(check.release)
    manager.apply(artifact.path, check.release)

See ``docs/self-upgrade.md`` for the capability boundary and rollback steps.

Exports are resolved lazily (PEP 562) on purpose: the gateway mounts the
upgrade HTTP router during cold start, and eagerly importing manager ->
selfcheck -> startup_probe here would add ~80ms before the first request.
``from src.upgrade import UpgradeManager`` still works unchanged; the import
just happens on first access.
"""

from typing import Any, Dict, Tuple

_LAZY_IMPORTS: Dict[str, Tuple[str, str]] = {
    "cleanup_download": (".downloader", "cleanup_download"),
    "download_release": (".downloader", "download_release"),
    "ApplyError": (".errors", "ApplyError"),
    "BackupCorrupt": (".errors", "BackupCorrupt"),
    "BackupNotFound": (".errors", "BackupNotFound"),
    "ChecksumMismatch": (".errors", "ChecksumMismatch"),
    "DiskSpaceError": (".errors", "DiskSpaceError"),
    "ManifestError": (".errors", "ManifestError"),
    "PackageValidationError": (".errors", "PackageValidationError"),
    "PreflightError": (".errors", "PreflightError"),
    "RollbackError": (".errors", "RollbackError"),
    "SelfCheckError": (".errors", "SelfCheckError"),
    "SignatureError": (".errors", "SignatureError"),
    "SnapshotError": (".errors", "SnapshotError"),
    "UpdateUnreachableError": (".errors", "UpdateUnreachableError"),
    "UpgradeError": (".errors", "UpgradeError"),
    "VersionReadError": (".errors", "VersionReadError"),
    "UpgradeConfig": (".manager", "UpgradeConfig"),
    "UpgradeManager": (".manager", "UpgradeManager"),
    "ApplyResult": (".models", "ApplyResult"),
    "CheckResult": (".models", "CheckResult"),
    "CheckStatus": (".models", "CheckStatus"),
    "DownloadResult": (".models", "DownloadResult"),
    "Phase": (".models", "Phase"),
    "ReleaseInfo": (".models", "ReleaseInfo"),
    "RollbackResult": (".models", "RollbackResult"),
    "HttpManifestSource": (".sources", "HttpManifestSource"),
    "LocalDirectorySource": (".sources", "LocalDirectorySource"),
    "ReleaseSource": (".sources", "ReleaseSource"),
    "build_source": (".sources", "build_source"),
    "select_latest": (".sources", "select_latest"),
    "is_newer": (".versioning", "is_newer"),
    "read_current_version": (".versioning", "read_current_version"),
}

__all__ = sorted(_LAZY_IMPORTS)


def __getattr__(name: str) -> Any:
    try:
        module_name, attribute = _LAZY_IMPORTS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    from importlib import import_module

    value = getattr(import_module(module_name, __name__), attribute)
    globals()[name] = value
    return value


def __dir__() -> list:
    return sorted(set(globals()) | set(_LAZY_IMPORTS))
