"""Data contracts for the self-upgrade module."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from .filemanifest import FileEntry, manifest_digest


class CheckStatus(str, Enum):
    UPDATE_AVAILABLE = "update_available"
    UP_TO_DATE = "up_to_date"
    NOT_CONFIGURED = "not_configured"
    UNREACHABLE = "unreachable"
    INVALID_MANIFEST = "invalid_manifest"
    VERSION_UNKNOWN = "version_unknown"


CHECK_STATUS_TEXT: Dict[CheckStatus, str] = {
    CheckStatus.UPDATE_AVAILABLE: "发现新版本",
    CheckStatus.UP_TO_DATE: "已是最新版本",
    CheckStatus.NOT_CONFIGURED: "未配置更新源，无法检查更新",
    CheckStatus.UNREACHABLE: "无法检查更新",
    CheckStatus.INVALID_MANIFEST: "发布清单无效，无法检查更新",
    CheckStatus.VERSION_UNKNOWN: "无法确定当前版本，无法检查更新",
}


class Phase(str, Enum):
    """User-visible upgrade phases (the exact text is in PHASE_TEXT)."""

    CHECKING = "checking"
    DOWNLOADING = "downloading"
    VERIFYING = "verifying"
    BACKING_UP = "backing_up"
    APPLYING = "applying"
    SELF_CHECKING = "self_checking"
    ROLLING_BACK = "rolling_back"
    COMPLETED = "completed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


PHASE_TEXT: Dict[Phase, str] = {
    Phase.CHECKING: "检查中",
    Phase.DOWNLOADING: "下载中",
    Phase.VERIFYING: "校验中",
    Phase.BACKING_UP: "备份中",
    Phase.APPLYING: "应用中",
    Phase.SELF_CHECKING: "自检中",
    Phase.ROLLING_BACK: "回滚中",
    Phase.COMPLETED: "完成",
    Phase.FAILED: "失败",
    Phase.ROLLED_BACK: "已回滚",
}


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    artifact: str
    sha256: str
    size: Optional[int] = None
    signature: Optional[str] = None
    signature_algorithm: Optional[str] = None
    notes: str = ""
    released_at: Optional[str] = None
    #: 包内每个文件的路径 / SHA256 / 大小；apply 前逐项校验。
    files: Optional[Tuple[FileEntry, ...]] = None
    #: 该版本写入的数据 schema 版本；缺失＝旧清单，按保守规则推断。
    data_schema_version: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "artifact": self.artifact,
            "sha256": self.sha256,
            "size": self.size,
            "signature": bool(self.signature),
            "signature_algorithm": self.signature_algorithm,
            "notes": self.notes,
            "released_at": self.released_at,
            "files_count": len(self.files) if self.files else 0,
            "file_manifest_sha256": (
                manifest_digest(self.files) if self.files else None
            ),
            "data_schema_version": self.data_schema_version,
        }


@dataclass
class CheckResult:
    status: CheckStatus
    message: str
    current_version: Optional[str] = None
    latest_version: Optional[str] = None
    release: Optional[ReleaseInfo] = None
    source: str = ""
    offline: bool = False
    checked_at: str = ""

    @property
    def update_available(self) -> bool:
        return self.status is CheckStatus.UPDATE_AVAILABLE

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "message": self.message,
            "current_version": self.current_version,
            "latest_version": self.latest_version,
            "release": self.release.to_dict() if self.release else None,
            "source": self.source,
            "offline": self.offline,
            "checked_at": self.checked_at,
        }


@dataclass
class DownloadResult:
    path: str
    sha256: str
    size: int
    release: ReleaseInfo
    signature_verified: bool
    signature_note: str
    temporary: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "size": self.size,
            "release": self.release.to_dict(),
            "signature_verified": self.signature_verified,
            "signature_note": self.signature_note,
            "temporary": self.temporary,
        }


@dataclass
class ApplyResult:
    record_id: str
    backup_id: str
    from_version: str
    to_version: str
    installed_files: int
    data_checked_files: int
    message: str = "升级完成"
    self_check_passed: bool = True
    self_check_issues: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "record_id": self.record_id,
            "backup_id": self.backup_id,
            "from_version": self.from_version,
            "to_version": self.to_version,
            "installed_files": self.installed_files,
            "data_checked_files": self.data_checked_files,
            "message": self.message,
            "self_check_passed": self.self_check_passed,
            "self_check_issues": self.self_check_issues,
        }


@dataclass
class RollbackResult:
    record_id: str
    backup_id: str
    from_version: str
    to_version: str
    restored_files: int
    message: str = "已回滚到升级前版本"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "record_id": self.record_id,
            "backup_id": self.backup_id,
            "from_version": self.from_version,
            "to_version": self.to_version,
            "restored_files": self.restored_files,
            "message": self.message,
        }


@dataclass
class DataGuardIssue:
    path: str
    kind: str
    detail: str

    def to_dict(self) -> Dict[str, str]:
        return {"path": self.path, "kind": self.kind, "detail": self.detail}


@dataclass
class DataGuardReport:
    ok: bool
    files_checked: int = 0
    sqlite_checked: int = 0
    json_checked: int = 0
    yaml_checked: int = 0
    issues: List[DataGuardIssue] = field(default_factory=list)
    checked_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "files_checked": self.files_checked,
            "sqlite_checked": self.sqlite_checked,
            "json_checked": self.json_checked,
            "yaml_checked": self.yaml_checked,
            "issues": [issue.to_dict() for issue in self.issues],
            "checked_at": self.checked_at,
        }
