"""Self-upgrade orchestration: check -> download -> verify -> backup -> apply -> rollback.

Safety properties enforced here (each is covered by a test):

* nothing is applied before the data preflight passes;
* code and data are backed up separately before the first file is replaced;
* every file install is atomic;
* a failure after mutation automatically restores the previous code and data;
* rollback is driven by the recorded backup, not by a guess;
* every step is written to a SQLite ledger and a user-readable status file.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from packaging.version import Version

from src.common.atomic_write import atomic_write_bytes, atomic_write_text

from .archive import (
    extract_zip_members,
    read_package_version,
    safe_relpath,
    validate_release_zip,
)
from .backups import backup_code, backup_data, restore_code, restore_data
from .dataguard import verify_data_paths
from .downloader import _sha256_file, download_release, verify_signature
from .errors import (
    ApplyError,
    ChecksumMismatch,
    ManifestError,
    PreflightError,
    RollbackError,
    UpdateUnreachableError,
    UpgradeError,
    VersionReadError,
)
from .ledger import UpgradeLedger
from .models import (
    PHASE_TEXT,
    ApplyResult,
    CheckResult,
    CheckStatus,
    DownloadResult,
    Phase,
    ReleaseInfo,
    RollbackResult,
)
from .sources import ReleaseSource, select_latest
from .versioning import is_newer, parse_version, read_current_version

_BACKUP_SCHEMA = "liuhao.upgrade-backup/1"
_DEFAULT_PROTECTED = (
    ".git",
    ".venv",
    "node_modules",
    "data",
    ".env",
    "secrets",
    "*.pem",
    "*.key",
    "*.crt",
)
_DEFAULT_INSTALL_ROOTS = (
    "src",
    "docs",
    "pyproject.toml",
    "requirements.txt",
    "oss-registry.yaml",
)


@dataclass
class UpgradeConfig:
    app_root: str | Path
    data_dir: str | Path | None = None
    backup_root: str | Path | None = None
    state_dir: str | Path | None = None
    install_roots: Sequence[str] = _DEFAULT_INSTALL_ROOTS
    protected_paths: Sequence[str] = _DEFAULT_PROTECTED
    extra_state_files: Sequence[str] = ()
    source: Optional[ReleaseSource] = None
    public_key: Optional[str | bytes] = None
    max_artifact_bytes: int = 2 * 1024 * 1024 * 1024
    max_uncompressed_bytes: int = 2 * 1024 * 1024 * 1024
    allow_same_or_downgrade: bool = False

    def __post_init__(self) -> None:
        self.app_root = Path(self.app_root).resolve()
        self.data_dir = Path(self.data_dir).resolve() if self.data_dir else self.app_root / "data"
        self.backup_root = (
            Path(self.backup_root).resolve()
            if self.backup_root
            else self.app_root / "upgrade-backups"
        )
        self.state_dir = (
            Path(self.state_dir).resolve()
            if self.state_dir
            else self.app_root / "upgrade-state"
        )
        self.install_roots = tuple(str(item) for item in self.install_roots)
        protected = list(str(item) for item in self.protected_paths)
        for managed in (self.backup_root, self.state_dir):
            try:
                rel = managed.relative_to(self.app_root).as_posix()
            except ValueError:
                continue
            if rel not in protected:
                protected.append(rel)
        self.protected_paths = tuple(protected)
        self.extra_state_files = tuple(str(item) for item in self.extra_state_files)


class UpgradeManager:
    def __init__(self, config: UpgradeConfig) -> None:
        self.config = config
        self.config.state_dir.mkdir(parents=True, exist_ok=True)
        self.config.backup_root.mkdir(parents=True, exist_ok=True)
        self.ledger = UpgradeLedger(self.config.state_dir / "upgrade-ledger.db")
        self.status_path = self.config.state_dir / "status.json"
        self._current_version: Optional[str] = None

    # ------------------------------------------------------------------
    # Introspection / user-visible state
    # ------------------------------------------------------------------
    def close(self) -> None:
        self.ledger.close()

    def current_version(self) -> tuple[str, str]:
        result = read_current_version(self.config.app_root)
        self._current_version = result[0]
        return result

    def status(self) -> Dict[str, Any]:
        if not self.status_path.is_file():
            return {"phase": "idle", "message": "尚未执行升级操作", "updated_at": ""}
        try:
            return json.loads(self.status_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return {"phase": "unknown", "message": "状态文件无法读取", "updated_at": ""}

    def history(self, *, limit: int = 50, kind: Optional[str] = None) -> List[Dict[str, Any]]:
        return self.ledger.list_records(kind=kind, limit=limit)

    def _set_status(
        self,
        phase: Phase,
        message: str,
        *,
        target_version: Optional[str] = None,
        backup_id: Optional[str] = None,
        error: Optional[str] = None,
        record_id: Optional[str] = None,
    ) -> None:
        previous = self.status()
        if self._current_version is None:
            try:
                self._current_version = read_current_version(self.config.app_root)[0]
            except VersionReadError:
                self._current_version = None
        payload = {
            "phase": phase.value,
            "phase_text": PHASE_TEXT.get(phase, phase.value),
            "message": message,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "current_version": self._current_version,
            "target_version": target_version or previous.get("target_version"),
            "backup_id": backup_id or previous.get("backup_id"),
            "last_error": error,
            "record_id": record_id or previous.get("record_id"),
        }
        atomic_write_text(
            self.status_path,
            json.dumps(payload, ensure_ascii=False, indent=2),
        )

    def _data_guard_paths(self) -> List[Path]:
        paths = [self.config.data_dir]
        for rel in self.config.extra_state_files:
            paths.append(self.config.app_root / safe_relpath(rel))
        return paths

    # ------------------------------------------------------------------
    # 1. Check
    # ------------------------------------------------------------------
    def check(self) -> CheckResult:
        self._set_status(Phase.CHECKING, "检查中：正在读取当前版本与更新源")
        source = self.config.source
        if source is None:
            result = CheckResult(
                status=CheckStatus.NOT_CONFIGURED,
                message="未配置更新源（本地发布目录或 HTTP 清单），无法检查更新",
                source="",
                checked_at=datetime.now(timezone.utc).isoformat(),
            )
            self._finish_check(result)
            return result

        try:
            current, _ = self.current_version()
        except VersionReadError as exc:
            result = CheckResult(
                status=CheckStatus.VERSION_UNKNOWN,
                message=f"无法确定当前版本，无法检查更新（{exc}）",
                source=source.description,
                checked_at=datetime.now(timezone.utc).isoformat(),
            )
            self._finish_check(result)
            return result

        try:
            load = source.load_manifest()
        except ManifestError as exc:
            result = CheckResult(
                status=CheckStatus.INVALID_MANIFEST,
                message=f"发布清单无效，无法检查更新：{exc}",
                current_version=current,
                source=source.description,
                checked_at=datetime.now(timezone.utc).isoformat(),
            )
            self._finish_check(result)
            return result

        if not load.ok or not load.manifest:
            status = (
                CheckStatus.INVALID_MANIFEST
                if "清单" in load.reason and "无效" in load.reason
                else CheckStatus.UNREACHABLE
            )
            result = CheckResult(
                status=status,
                message=load.reason or "无法检查更新",
                current_version=current,
                source=source.description,
                offline=load.offline,
                checked_at=datetime.now(timezone.utc).isoformat(),
            )
            self._finish_check(result)
            return result

        try:
            latest = select_latest(load.manifest)
        except ManifestError as exc:
            result = CheckResult(
                status=CheckStatus.INVALID_MANIFEST,
                message=f"发布清单无效，无法检查更新：{exc}",
                current_version=current,
                source=source.description,
                checked_at=datetime.now(timezone.utc).isoformat(),
            )
            self._finish_check(result)
            return result

        if is_newer(latest.version, current):
            result = CheckResult(
                status=CheckStatus.UPDATE_AVAILABLE,
                message=f"发现新版本 {latest.version}（当前 {current}）",
                current_version=current,
                latest_version=latest.version,
                release=latest,
                source=source.description,
                checked_at=datetime.now(timezone.utc).isoformat(),
            )
        else:
            result = CheckResult(
                status=CheckStatus.UP_TO_DATE,
                message=f"已是最新版本 {current}",
                current_version=current,
                latest_version=latest.version,
                release=latest,
                source=source.description,
                checked_at=datetime.now(timezone.utc).isoformat(),
            )
        self._finish_check(result)
        return result

    def _finish_check(self, result: CheckResult) -> None:
        record = self.ledger.record(
            kind="check",
            status=result.status.value,
            from_version=result.current_version,
            to_version=result.latest_version,
            reason="" if result.update_available else result.message,
            detail={"result": result.to_dict(), "offline": result.offline},
        )
        if result.status in (CheckStatus.UPDATE_AVAILABLE, CheckStatus.UP_TO_DATE):
            self._set_status(
                Phase.COMPLETED,
                result.message,
                target_version=result.latest_version,
                record_id=record.get("id"),
            )
        else:
            self._set_status(
                Phase.FAILED,
                f"失败：{result.message}",
                error=result.message,
                record_id=record.get("id"),
            )

    # ------------------------------------------------------------------
    # 2. Download + verify
    # ------------------------------------------------------------------
    def download(self, release: ReleaseInfo) -> DownloadResult:
        source = self.config.source
        if source is None:
            raise UpdateUnreachableError("未配置更新源，无法下载发布包")
        self._set_status(
            Phase.DOWNLOADING,
            f"下载中：正在下载 {release.version}",
            target_version=release.version,
        )
        try:
            result = download_release(
                source,
                release,
                public_key=self.config.public_key,
                max_bytes=self.config.max_artifact_bytes,
            )
        except UpgradeError as exc:
            self.ledger.record(
                kind="download",
                status="failed",
                to_version=release.version,
                reason=str(exc),
                detail={"artifact": release.artifact},
            )
            self._set_status(
                Phase.FAILED,
                f"失败：{exc}",
                target_version=release.version,
                error=str(exc),
            )
            raise

        self._set_status(
            Phase.VERIFYING,
            "校验中：SHA256 通过" + (
                "，签名已验证" if result.signature_verified else f"，{result.signature_note}"
            ),
            target_version=release.version,
        )
        record = self.ledger.record(
            kind="download",
            status="verified",
            to_version=release.version,
            reason=result.signature_note,
            artifact_path=result.path,
            detail=result.to_dict(),
        )
        self._set_status(
            Phase.COMPLETED,
            f"完成：{release.version} 已下载并校验（{result.signature_note}）",
            target_version=release.version,
            record_id=record.get("id"),
        )
        return result

    # ------------------------------------------------------------------
    # 3. Apply
    # ------------------------------------------------------------------
    def apply(self, artifact_path: str | Path, release: ReleaseInfo) -> ApplyResult:
        return self._apply(Path(artifact_path), release)

    def _apply(self, artifact: Path, release: ReleaseInfo) -> ApplyResult:
        self._set_status(
            Phase.VERIFYING,
            f"校验中：正在复核 {release.version} 发布包",
            target_version=release.version,
        )
        try:
            current_version, _ = self.current_version()
        except VersionReadError as exc:
            self._fail_before_mutation(release, f"无法确定当前版本：{exc}")
            raise ApplyError(f"无法确定当前版本：{exc}") from exc

        if not self.config.allow_same_or_downgrade and not is_newer(
            release.version, current_version
        ):
            message = (
                f"目标版本 {release.version} 不高于当前版本 {current_version}，拒绝应用"
            )
            self._fail_before_mutation(release, message, from_version=current_version)
            raise ApplyError(message)

        # Re-verify the artifact now: between download and apply the file on
        # disk could have changed.
        try:
            actual_sha = _sha256_file(artifact)
            if actual_sha != release.sha256:
                raise ChecksumMismatch(
                    f"应用前复核 SHA256 失败：清单 {release.sha256}，实际 {actual_sha}"
                )
            if release.size is not None and artifact.stat().st_size != release.size:
                raise ChecksumMismatch(
                    f"应用前复核大小失败：清单 {release.size}，实际 {artifact.stat().st_size}"
                )
            signature_verified, signature_note = verify_signature(
                artifact, release, self.config.public_key
            )
        except UpgradeError as exc:
            self._fail_before_mutation(release, str(exc), from_version=current_version)
            raise ApplyError(str(exc)) from exc

        preflight = verify_data_paths(self._data_guard_paths())
        if not preflight.ok:
            details = "; ".join(
                f"{issue.path}: {issue.detail}" for issue in preflight.issues
            )
            self.ledger.record(
                kind="preflight",
                status="failed",
                from_version=current_version,
                to_version=release.version,
                reason=details,
                detail=preflight.to_dict(),
            )
            self._set_status(
                Phase.FAILED,
                f"失败：升级前数据校验未通过（{details}）",
                target_version=release.version,
                error=details,
            )
            raise PreflightError(f"升级前数据校验未通过：{details}")

        staging = Path(tempfile.mkdtemp(prefix="liuhao-upgrade-stage-"))
        backup_id = ""
        mutation_started = False
        try:
            with zipfile.ZipFile(artifact, "r") as archive:
                members = validate_release_zip(
                    archive,
                    install_roots=self.config.install_roots,
                    protected_prefixes=self.config.protected_paths,
                    max_uncompressed_bytes=self.config.max_uncompressed_bytes,
                )
                installed = extract_zip_members(archive, members, staging)
            staged_version = read_package_version(staging)
            if Version(staged_version) != Version(release.version):
                raise ApplyError(
                    f"发布包版本 {staged_version} 与清单版本 {release.version} 不一致"
                )

            self._set_status(
                Phase.BACKING_UP,
                "备份中：正在分别备份代码与数据",
                target_version=release.version,
            )
            backup_id = self._create_backup(current_version, release, preflight.to_dict())
            # Re-read the metadata immediately: a backup we cannot parse is not
            # a backup we can roll back to, and this must fail before mutation.
            self._read_backup(backup_id)

            self._set_status(
                Phase.APPLYING,
                f"应用中：正在安装 {len(installed)} 个文件",
                target_version=release.version,
                backup_id=backup_id,
            )
            mutation_started = True
            with zipfile.ZipFile(artifact, "r") as archive:
                extract_zip_members(archive, members, self.config.app_root)

            post_version, _ = self.current_version()
            if Version(post_version) != Version(release.version):
                raise ApplyError(
                    f"应用后版本仍为 {post_version}，预期 {release.version}"
                )
            post_preflight = verify_data_paths(self._data_guard_paths())
            if not post_preflight.ok:
                details = "; ".join(
                    f"{issue.path}: {issue.detail}" for issue in post_preflight.issues
                )
                raise ApplyError(f"应用后数据校验未通过：{details}")

            record = self.ledger.record(
                kind="apply",
                status="completed",
                from_version=current_version,
                to_version=release.version,
                reason=signature_note,
                backup_id=backup_id,
                artifact_path=str(artifact),
                detail={
                    "backup_dir": str(self.config.backup_root / backup_id),
                    "installed_files": len(installed),
                    "signature_verified": signature_verified,
                    "signature_note": signature_note,
                    "preflight": preflight.to_dict(),
                },
            )
            self._set_status(
                Phase.COMPLETED,
                f"完成：已升级 {current_version} → {release.version}",
                target_version=release.version,
                backup_id=backup_id,
                record_id=record.get("id"),
            )
            return ApplyResult(
                record_id=record["id"],
                backup_id=backup_id,
                from_version=current_version,
                to_version=release.version,
                installed_files=len(installed),
                data_checked_files=preflight.files_checked,
            )
        except Exception as exc:
            rollback_ok = False
            rollback_error = ""
            if mutation_started and backup_id:
                self._set_status(
                    Phase.ROLLING_BACK,
                    f"回滚中：应用失败，正在恢复 {current_version}",
                    target_version=release.version,
                    backup_id=backup_id,
                )
                try:
                    self._restore_backup(backup_id)
                    restored_version, _ = self.current_version()
                    rollback_ok = Version(restored_version) == Version(current_version)
                    if not rollback_ok:
                        rollback_error = (
                            f"回滚后版本为 {restored_version}，预期 {current_version}"
                        )
                except Exception as rollback_exc:  # noqa: BLE001 - reported to user
                    rollback_error = str(rollback_exc)
            reason = str(exc) + (f"；回滚失败：{rollback_error}" if rollback_error else "")
            record = self.ledger.record(
                kind="apply",
                status="failed_rolled_back" if rollback_ok else "failed",
                from_version=current_version,
                to_version=release.version,
                reason=reason,
                backup_id=backup_id or None,
                artifact_path=str(artifact),
                detail={
                    "mutation_started": mutation_started,
                    "rollback_ok": rollback_ok,
                    "rollback_error": rollback_error,
                    "signature_verified": signature_verified,
                    "signature_note": signature_note,
                },
            )
            if rollback_ok:
                self._set_status(
                    Phase.ROLLED_BACK,
                    f"已回滚：升级失败，已恢复到 {current_version}（原因：{exc}）",
                    target_version=release.version,
                    backup_id=backup_id,
                    error=str(exc),
                    record_id=record.get("id"),
                )
                raise ApplyError(f"应用失败，已自动回滚到 {current_version}：{exc}") from exc
            if mutation_started:
                self._set_status(
                    Phase.FAILED,
                    f"失败：应用失败且回滚失败（{rollback_error}）",
                    target_version=release.version,
                    backup_id=backup_id,
                    error=reason,
                    record_id=record.get("id"),
                )
                raise ApplyError(
                    f"应用失败且回滚失败，当前状态可能不一致：{exc}；回滚错误：{rollback_error}"
                ) from exc
            self._set_status(
                Phase.FAILED,
                f"失败：应用未开始（{exc}）",
                target_version=release.version,
                error=str(exc),
                record_id=record.get("id"),
            )
            raise ApplyError(f"应用失败（未修改任何文件）：{exc}") from exc
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def _fail_before_mutation(
        self,
        release: ReleaseInfo,
        message: str,
        *,
        from_version: Optional[str] = None,
    ) -> None:
        self.ledger.record(
            kind="apply",
            status="failed",
            from_version=from_version,
            to_version=release.version,
            reason=message,
            detail={"mutation_started": False},
        )
        self._set_status(
            Phase.FAILED,
            f"失败：{message}",
            target_version=release.version,
            error=message,
        )

    # ------------------------------------------------------------------
    # Backup / restore internals
    # ------------------------------------------------------------------
    def _create_backup(
        self, from_version: str, release: ReleaseInfo, preflight_detail: Dict[str, Any]
    ) -> str:
        backup_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
        backup_dir = self.config.backup_root / backup_id
        backup_dir.mkdir(parents=True, exist_ok=False)

        code = backup_code(self.config.app_root, self.config.install_roots, backup_dir)
        extra_paths = [
            self.config.app_root / safe_relpath(rel)
            for rel in self.config.extra_state_files
        ]
        data = backup_data(
            self.config.app_root,
            self.config.data_dir,
            extra_paths,
            backup_dir,
        )
        meta = {
            "schema": _BACKUP_SCHEMA,
            "backup_id": backup_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "from_version": from_version,
            "to_version": release.version,
            "code_zip_sha256": code.sha256,
            "code_file_count": code.file_count,
            "data_file_count": data.file_count,
            "data_sqlite_count": data.sqlite_count,
            "data_total_bytes": data.total_bytes,
            "preflight": preflight_detail,
        }
        atomic_write_bytes(
            backup_dir / "backup.json",
            json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8"),
        )
        self.ledger.record(
            kind="backup",
            status="completed",
            from_version=from_version,
            to_version=release.version,
            backup_id=backup_id,
            detail=meta,
        )
        return backup_id

    def _read_backup(self, backup_id: str) -> Dict[str, Any]:
        path = self.config.backup_root / backup_id / "backup.json"
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RollbackError(f"升级备份元数据无法读取：{exc}") from exc
        if meta.get("schema") != _BACKUP_SCHEMA:
            raise RollbackError("升级备份元数据 schema 不匹配")
        return meta

    def _restore_backup(self, backup_id: str) -> None:
        backup_dir = self.config.backup_root / backup_id
        meta = self._read_backup(backup_id)
        expected_sha = str(meta.get("code_zip_sha256", "")).lower()
        zip_path = backup_dir / "code.zip"
        if expected_sha and _sha256_file(zip_path) != expected_sha:
            raise RollbackError("代码备份 ZIP 哈希与备份元数据不符")
        restore_code(
            self.config.app_root,
            backup_dir,
            trash_root=backup_dir / "rollback-trash",
        )
        restore_data(self.config.app_root, self.config.data_dir, backup_dir)

    # ------------------------------------------------------------------
    # 4. Explicit rollback (one click / one command)
    # ------------------------------------------------------------------
    def rollback(
        self,
        *,
        backup_id: Optional[str] = None,
        record_id: Optional[str] = None,
    ) -> RollbackResult:
        self._set_status(Phase.ROLLING_BACK, "回滚中：正在定位升级前备份")
        try:
            # Resolving the backup happens inside the try: "no rollback record"
            # is a failure that must leave the status at 失败, not stuck at
            # 回滚中 forever.
            if record_id:
                record = self.ledger.get(record_id)
                if not record:
                    raise RollbackError(f"升级记录不存在：{record_id}")
                backup_id = record.get("backup_id")
            if not backup_id:
                latest = self.ledger.latest_successful_apply()
                if not latest or not latest.get("backup_id"):
                    raise RollbackError("没有可回滚的升级记录")
                backup_id = str(latest["backup_id"])
            meta = self._read_backup(backup_id)
            from_version = str(meta.get("to_version", ""))
            to_version = str(meta.get("from_version", ""))
            current, _ = self.current_version()
            self._restore_backup(backup_id)
            restored_version, _ = self.current_version()
            if to_version and Version(restored_version) != Version(to_version):
                raise RollbackError(
                    f"回滚后版本为 {restored_version}，预期 {to_version}"
                )
            record = self.ledger.record(
                kind="rollback",
                status="rolled_back",
                from_version=from_version or current,
                to_version=to_version or restored_version,
                backup_id=backup_id,
                detail={
                    "backup_dir": str(self.config.backup_root / backup_id),
                    "current_before_rollback": current,
                    "restored_version": restored_version,
                },
            )
            self._set_status(
                Phase.ROLLED_BACK,
                f"已回滚：{from_version or current} → {to_version or restored_version}",
                target_version=to_version or restored_version,
                backup_id=backup_id,
                record_id=record.get("id"),
            )
            return RollbackResult(
                record_id=record["id"],
                backup_id=backup_id,
                from_version=from_version or current,
                to_version=to_version or restored_version,
                restored_files=int(meta.get("code_file_count", 0))
                + int(meta.get("data_file_count", 0)),
            )
        except Exception as exc:
            record = self.ledger.record(
                kind="rollback",
                status="failed",
                backup_id=backup_id,
                reason=str(exc),
            )
            self._set_status(
                Phase.FAILED,
                f"失败：回滚失败（{exc}）",
                backup_id=backup_id,
                error=str(exc),
                record_id=record.get("id"),
            )
            if isinstance(exc, RollbackError):
                raise
            raise RollbackError(f"回滚失败：{exc}") from exc
