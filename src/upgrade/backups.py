"""Separate code and data backups, plus restore primitives.

Data and code are handled differently on purpose:

* SQLite databases are captured with ``VACUUM INTO`` (the single correct
  snapshot primitive already used by ``src/backup`` and the audit kernel).
  Their ``-wal``/``-shm``/``-journal`` sidecars are deliberately *not* copied:
  replaying a stale WAL against a restored main file corrupts it.
* Everything else under ``data/`` is copied byte-for-byte. No UTF-8 decoding.
* Code is captured as a ZIP of the configured install roots, which is also the
  authoritative source for a full rollback of those roots.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Sequence

from src.common.atomic_write import atomic_write_bytes
from src.kernels.audit.durability import vacuum_into

from .archive import _atomic_stream_copy, atomic_copy_file, safe_relpath, validate_release_zip
from .dataguard import check_sqlite, is_sqlite_file, iter_files
from .errors import BackupCorrupt, BackupNotFound, RollbackError, SnapshotError

_DATA_BACKUP_SCHEMA = "liuhao.data-backup/1"
_CODE_BACKUP_SCHEMA = "liuhao.code-backup/1"
_SNAPSHOT_EXPORT_SCHEMA = "liuhao.snapshot-export/1"
_SQLITE_SIDECARS = ("-wal", "-shm", "-journal")

#: 恢复码交接文件、凭据文件与 DPAPI 密文副本**永不**进入备份或离线导出。
#: 已生成的旧备份不追溯（其中的副本仍是旧行为），但新备份与导出必须为空。
_PROTECTED_BASENAME_PREFIXES = ("recovery_code", "recovery_handoff")
_PROTECTED_BACKUP_NAMES = frozenset(
    {
        "auth_secrets.json",
        # 真实的主密钥文件名。旧版排除集误写成 ``model_config_key.bin``，
        # 导致 ``model_config.key`` 被原样打进备份。
        "model_config.key",
        # 身份库签名密钥与身份库本体：两者都能让持有者伪造/读取本机身份。
        "integrity.key",
        "human_identities.json",
        # 兼容 0.1.x 曾用的错误文件名：新代码不会再生成，但旧安装里可能残留。
        "model_config_key.bin",
    }
)


def is_protected_state_file(relpath: str) -> bool:
    """该相对路径是否属于“不得打包”的恢复码/凭据类文件。"""
    name = str(relpath or "").replace("\\", "/").rsplit("/", 1)[-1].lower()
    if not name:
        return False
    if name.startswith(_PROTECTED_BASENAME_PREFIXES):
        return True
    return name in _PROTECTED_BACKUP_NAMES


def _protected_files_in(directory: Path) -> List[str]:
    """列出目录内所有“不得打包”的物理文件（相对路径，已排序）。"""
    root = Path(directory)
    found: List[str] = []
    for item in root.rglob("*"):
        if not item.is_file():
            continue
        rel = item.relative_to(root).as_posix()
        if is_protected_state_file(rel):
            found.append(rel)
    return sorted(found)


@dataclass
class DataBackup:
    backup_dir: Path
    manifest_path: Path
    file_count: int
    sqlite_count: int
    total_bytes: int


@dataclass
class CodeBackup:
    backup_dir: Path
    zip_path: Path
    roots: Sequence[str]
    file_count: int
    sha256: str


@dataclass
class SnapshotExport:
    destination: Path
    manifest_path: Path
    backup_id: str
    file_count: int
    sqlite_count: int
    total_bytes: int


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _remove_sqlite_sidecars(path: Path) -> None:
    for suffix in _SQLITE_SIDECARS:
        sidecar = path.with_name(path.name + suffix)
        sidecar.unlink(missing_ok=True)


def verify_backup(backup_dir: Path) -> None:
    """Prove a backup is usable *before* anything is moved or overwritten.

    A rollback that starts restoring and only then discovers a corrupted
    ``code.zip`` would leave the user half-old/half-new. This check runs first,
    so a damaged backup is refused with the current installation untouched.
    """
    backup_dir = Path(backup_dir)
    # 校验即安全门禁：备份目录里只要出现受保护文件，就不允许把它当作可用的
    # 备份（无论是回滚还是导出）。这是“打包后逐项断言”的另一半——创建端已经
    # 拒绝写入，这里再对既有目录做一次物理复查。
    leaked = _protected_files_in(backup_dir)
    if leaked:
        raise BackupCorrupt(
            "备份包含受保护文件（凭据/身份库/模型主密钥），拒绝使用："
            + ", ".join(leaked)
        )
    code_zip = backup_dir / "code.zip"
    code_manifest_path = backup_dir / "code-backup.json"
    if not code_zip.is_file() or not code_manifest_path.is_file():
        raise BackupNotFound(
            "备份中缺少旧版本代码包（code.zip 或 code-backup.json 不存在）"
        )
    try:
        code_manifest = json.loads(code_manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupCorrupt(f"代码备份清单无法读取：{exc}") from exc
    if code_manifest.get("schema") != _CODE_BACKUP_SCHEMA:
        raise BackupCorrupt("代码备份清单 schema 不匹配")
    expected = str(code_manifest.get("sha256", "")).lower()
    actual = _sha256(code_zip)
    if expected and actual != expected:
        raise BackupCorrupt(
            f"代码备份包 SHA256 与记录不符（记录 {expected[:16]}…，实际 {actual[:16]}…）"
        )

    data_manifest_path = backup_dir / "data-backup.json"
    if not data_manifest_path.is_file():
        raise BackupNotFound("备份中缺少数据快照清单（data-backup.json 不存在）")
    try:
        data_manifest = json.loads(data_manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupCorrupt(f"数据备份清单无法读取：{exc}") from exc
    if data_manifest.get("schema") != _DATA_BACKUP_SCHEMA:
        raise BackupCorrupt("数据备份清单 schema 不匹配")
    for entry in data_manifest.get("entries", []):
        kind = str(entry.get("kind") or "")
        if kind not in ("data", "extra"):
            raise BackupCorrupt(f"数据备份条目类型非法：{kind!r}")
        rel = safe_relpath(str(entry.get("relpath") or ""))
        source = backup_dir / kind / rel
        if not source.is_file():
            raise BackupNotFound(f"数据备份缺少文件：{rel}")
        expected = str(entry.get("sha256", "")).lower()
        if expected and _sha256(source) != expected:
            raise BackupCorrupt(f"数据备份文件 SHA256 与记录不符：{rel}")


def _backup_one(source: Path, target: Path, *, sqlite: bool) -> int:
    if sqlite:
        if target.exists():
            raise RollbackError(f"备份目标已存在，拒绝覆盖：{target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        vacuum_into(str(source), str(target))
        errors = check_sqlite(target)
        if errors:
            raise RollbackError(
                "SQLite 快照校验失败：" + "; ".join(errors)
            )
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            target.unlink()
        with open(source, "rb") as src, open(target, "wb") as dst:
            for chunk in iter(lambda: src.read(1024 * 1024), b""):
                dst.write(chunk)
            dst.flush()
            os.fsync(dst.fileno())
    return target.stat().st_size


def _is_under(path: Path, roots: Sequence[Path]) -> bool:
    resolved = path.resolve()
    for root in roots:
        try:
            resolved.relative_to(root)
        except ValueError:
            continue
        return True
    return False


def backup_data(
    app_root: Path,
    data_dir: Path,
    extra_files: Sequence[Path],
    dest_dir: Path,
    exclude_dirs: Sequence[Path] = (),
) -> DataBackup:
    """Back up ``data_dir`` and extra state files into ``dest_dir``."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    data_out = dest_dir / "data"
    extra_out = dest_dir / "extra"
    entries: List[dict] = []
    total = 0
    sqlite_count = 0

    # 升级备份目录与运行期状态目录默认都落在数据目录内（``<data>/upgrade-backups``、
    # ``<data>/upgrade-state``）。遍历数据目录时必须跳过它们：前者会把“正在写入的
    # 备份”当成用户数据再备份一遍；后者的操作锁正被当前进程独占打开，读取会报
    # Permission denied，而它本身也不是用户数据。
    skipped = [dest_dir.resolve()] + [Path(item).resolve() for item in exclude_dirs]
    excluded_protected = 0

    for source in iter_files([data_dir]):
        if _is_under(source, skipped):
            continue
        rel = source.relative_to(data_dir).as_posix()
        safe_relpath(rel)
        if is_protected_state_file(rel):
            # 恢复码 / 凭据 / DPAPI 密文副本：无论用户数据目录怎么布局都不打包。
            excluded_protected += 1
            continue
        sqlite = is_sqlite_file(source)
        target = data_out / rel
        size = _backup_one(source, target, sqlite=sqlite)
        sqlite_count += int(sqlite)
        total += size
        entries.append(
            {
                "kind": "data",
                "relpath": rel,
                "sqlite": sqlite,
                "size": size,
                "sha256": _sha256(target),
            }
        )

    for source in extra_files:
        source = Path(source)
        if not source.is_file():
            continue
        try:
            rel = source.resolve().relative_to(app_root.resolve()).as_posix()
        except ValueError as exc:
            raise RollbackError(f"附加状态文件不在应用目录内：{source}") from exc
        safe_relpath(rel)
        if is_protected_state_file(rel):
            excluded_protected += 1
            continue
        sqlite = is_sqlite_file(source)
        target = extra_out / rel
        size = _backup_one(source, target, sqlite=sqlite)
        sqlite_count += int(sqlite)
        total += size
        entries.append(
            {
                "kind": "extra",
                "relpath": rel,
                "sqlite": sqlite,
                "size": size,
                "sha256": _sha256(target),
            }
        )

    manifest = {
        "schema": _DATA_BACKUP_SCHEMA,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "file_count": len(entries),
        "sqlite_count": sqlite_count,
        "total_bytes": total,
        "excluded_protected": excluded_protected,
        "entries": entries,
    }
    manifest_path = dest_dir / "data-backup.json"
    atomic_write_bytes(
        manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
    )
    # 打包后逐项断言：清单过滤之外再扫一遍物理文件，防止未来新增复制路径
    # 绕过 ``is_protected_state_file``。一旦命中就拒绝导出，绝不留下含密钥的包。
    leaked = _protected_files_in(dest_dir)
    if leaked:
        raise SnapshotError(
            "数据备份包内仍含受保护文件，拒绝导出：" + ", ".join(leaked)
        )
    return DataBackup(
        backup_dir=dest_dir,
        manifest_path=manifest_path,
        file_count=len(entries),
        sqlite_count=sqlite_count,
        total_bytes=total,
    )


def export_data_snapshot(
    backup_dir: Path,
    dest_dir: Path,
    *,
    backup_meta: dict | None = None,
) -> SnapshotExport:
    """Copy a pre-upgrade data snapshot into a standalone directory.

    The export is byte-for-byte the backup (each file's SHA256 is verified
    before and after the copy), plus a ``snapshot.json`` describing it. It never
    touches the live database; the destination must not exist so an accidental
    second export cannot overwrite the first.
    """
    backup_dir = Path(backup_dir)
    dest = Path(dest_dir)
    manifest_path = backup_dir / "data-backup.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SnapshotError(f"数据快照清单无法读取：{exc}") from exc
    if manifest.get("schema") != _DATA_BACKUP_SCHEMA:
        raise SnapshotError("数据快照清单 schema 不匹配")
    if dest.exists():
        raise SnapshotError(f"目标目录已存在，拒绝覆盖：{dest}")

    dest.mkdir(parents=True, exist_ok=False)
    entries_out: List[dict] = []
    total = 0
    sqlite_count = 0
    excluded_protected = 0
    try:
        for entry in manifest.get("entries", []):
            kind = str(entry.get("kind") or "")
            if kind not in ("data", "extra"):
                raise SnapshotError(f"数据快照条目类型非法：{kind!r}")
            rel = safe_relpath(str(entry.get("relpath") or ""))
            if is_protected_state_file(rel):
                # 旧备份的清单里可能还列着恢复码/凭据：导出层必须过滤，
                # 不能“清单里有就原样复制”。
                excluded_protected += 1
                continue
            source = backup_dir / kind / rel
            if not source.is_file():
                raise SnapshotError(f"数据快照文件缺失：{source}")
            expected = str(entry.get("sha256") or "").lower()
            actual = _sha256(source)
            if expected and actual != expected:
                raise SnapshotError(f"数据快照文件哈希不符：{rel}")
            target = dest / kind / rel
            atomic_copy_file(source, target)
            copied = _sha256(target)
            if copied != actual:
                raise SnapshotError(f"导出后文件哈希不符：{rel}")
            size = target.stat().st_size
            sqlite = bool(entry.get("sqlite"))
            sqlite_count += int(sqlite)
            total += size
            entries_out.append(
                {
                    "kind": kind,
                    "relpath": rel,
                    "sqlite": sqlite,
                    "size": size,
                    "sha256": copied,
                }
            )

        # 打包后断言：目标目录里不得存在任何受保护文件。清单过滤之外再查
        # 一次物理文件，防止未来新增复制路径绕过过滤。
        leaked = _protected_files_in(dest)
        if leaked:
            raise SnapshotError("导出包内仍含受保护文件：" + ", ".join(leaked))

        meta = backup_meta or {}
        snapshot = {
            "schema": _SNAPSHOT_EXPORT_SCHEMA,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "backup_id": meta.get("backup_id") or backup_dir.name,
            "from_version": meta.get("from_version", ""),
            "to_version": meta.get("to_version", ""),
            "source_backup_dir": str(backup_dir),
            "file_count": len(entries_out),
            "sqlite_count": sqlite_count,
            "total_bytes": total,
            "excluded_protected": excluded_protected,
            "entries": entries_out,
        }
        exported_manifest = dest / "snapshot.json"
        atomic_write_bytes(
            exported_manifest,
            json.dumps(snapshot, ensure_ascii=False, indent=2).encode("utf-8"),
        )
    except BaseException:
        shutil.rmtree(dest, ignore_errors=True)
        raise

    return SnapshotExport(
        destination=dest,
        manifest_path=dest / "snapshot.json",
        backup_id=str(snapshot["backup_id"]),
        file_count=len(entries_out),
        sqlite_count=sqlite_count,
        total_bytes=total,
    )


def _restore_locked_sqlite(source: Path, target: Path, busy_timeout_ms: int = 5000) -> None:
    """Restore a database the running service still has open.

    On Windows ``os.replace`` is refused while a WAL-mode database is open
    (``-shm`` is mapped by the live connection, WinError 5/32), so an upgrade
    applied while the service runs could not roll back its data. Writing the
    snapshot through SQLite's online-backup API needs no file replacement and
    is atomic: the destination content is replaced inside one transaction, so a
    failure leaves the previous database intact.
    """
    try:
        src = sqlite3.connect(str(source))
        try:
            dst = sqlite3.connect(str(target))
            try:
                dst.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
    except sqlite3.Error as exc:
        raise RollbackError(
            f"数据库回滚失败（{target} 被运行中的服务占用，且无法通过 SQLite 恢复）：{exc}"
        ) from exc


def restore_data(app_root: Path, data_dir: Path, backup_dir: Path) -> int:
    """Restore the files captured by :func:`backup_data`.

    Files created after the upgrade that were not in the backup are left in
    place; this avoids deleting user data while still restoring every captured
    database/state file to its pre-upgrade bytes.
    """
    manifest_path = backup_dir / "data-backup.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RollbackError(f"数据备份清单无法读取：{exc}") from exc
    if manifest.get("schema") != _DATA_BACKUP_SCHEMA:
        raise RollbackError("数据备份清单 schema 不匹配")
    restored = 0
    sqlite_targets: List[Path] = []
    for entry in manifest.get("entries", []):
        kind = entry.get("kind")
        rel = safe_relpath(str(entry.get("relpath", "")))
        sqlite = bool(entry.get("sqlite"))
        if kind == "data":
            source = backup_dir / "data" / rel
            target = data_dir / rel
        elif kind == "extra":
            source = backup_dir / "extra" / rel
            target = app_root / rel
        else:
            raise RollbackError(f"未知的数据备份条目类型：{kind!r}")
        if not source.is_file():
            raise RollbackError(f"数据备份缺少文件：{source}")
        expected = str(entry.get("sha256", "")).lower()
        if expected and _sha256(source) != expected:
            raise RollbackError(f"数据备份文件哈希不符：{source}")
        target.parent.mkdir(parents=True, exist_ok=True)
        if sqlite:
            try:
                atomic_copy_path(source, target)
            except PermissionError:
                # The service still holds the database open, so the replacement
                # was refused (Windows: WinError 5/32). The old -wal/-shm
                # sidecars are exactly what the live connection needs; deleting
                # them first would discard committed frames and leave the live
                # database unusable ("disk I/O error" once SQLite re-opens it).
                # Restore the content through SQLite instead, privileges intact.
                _restore_locked_sqlite(source, target)
            else:
                # Only after the main file was actually replaced is it safe to
                # drop the sidecars: they describe the *previous* main file. If
                # the replacement had failed, removing them first would leave a
                # half-restored database (old main file, committed WAL frames
                # gone). A failure to clean up here is reported, never ignored.
                try:
                    _remove_sqlite_sidecars(target)
                except OSError as exc:
                    raise RollbackError(
                        f"数据库已替换但旧 WAL/SHM 无法清理（{target}）：{exc}；"
                        "请停止服务后重新执行回滚"
                    ) from exc
            sqlite_targets.append(target)
        else:
            atomic_copy_path(source, target)
        restored += 1
    for target in sqlite_targets:
        errors = check_sqlite(target)
        if errors:
            raise RollbackError(f"恢复后的 SQLite 校验失败：{target}：{'; '.join(errors)}")
    return restored


def atomic_copy_path(source: Path, target: Path) -> None:
    with open(source, "rb") as src:
        _atomic_stream_copy(src, target)


def _iter_code_files(root: Path):
    for candidate in sorted(root.rglob("*")):
        if not candidate.is_file() or candidate.is_symlink():
            continue
        parts = candidate.relative_to(root).parts
        if "__pycache__" in parts or candidate.suffix in (".pyc", ".pyo"):
            continue
        if candidate.name.endswith(_SQLITE_SIDECARS):
            continue
        yield candidate


def backup_code(app_root: Path, roots: Sequence[str], dest_dir: Path) -> CodeBackup:
    dest_dir.mkdir(parents=True, exist_ok=True)
    zip_path = dest_dir / "code.zip"
    file_count = 0
    seen: set[str] = set()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for root_name in roots:
            safe_relpath(root_name)
            root = app_root / root_name
            if not root.exists():
                continue
            if root.is_file():
                arc = safe_relpath(root_name)
                if arc not in seen:
                    archive.write(root, arc)
                    seen.add(arc)
                    file_count += 1
                continue
            for candidate in _iter_code_files(root):
                arc = safe_relpath(candidate.relative_to(app_root).as_posix())
                if arc in seen:
                    continue
                archive.write(candidate, arc)
                seen.add(arc)
                file_count += 1
    if file_count == 0:
        raise RollbackError("代码备份为空：配置的 install roots 不存在")
    with zipfile.ZipFile(zip_path, "r") as check:
        bad = check.testzip()
        if bad:
            raise RollbackError(f"代码备份 ZIP 校验失败：{bad}")

    manifest = {
        "schema": _CODE_BACKUP_SCHEMA,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "roots": list(roots),
        "file_count": file_count,
        "sha256": _sha256(zip_path),
    }
    atomic_write_bytes(
        dest_dir / "code-backup.json",
        json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
    )
    return CodeBackup(
        backup_dir=dest_dir,
        zip_path=zip_path,
        roots=tuple(roots),
        file_count=file_count,
        sha256=manifest["sha256"],
    )


def restore_code(app_root: Path, backup_dir: Path, trash_root: Path) -> int:
    """Replace install roots with the backed-up versions.

    Current roots are moved (not deleted) into ``trash_root`` first, so a
    failure during extraction can put them back.
    """
    manifest_path = backup_dir / "code-backup.json"
    zip_path = backup_dir / "code.zip"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RollbackError(f"代码备份清单无法读取：{exc}") from exc
    if manifest.get("schema") != _CODE_BACKUP_SCHEMA:
        raise RollbackError("代码备份清单 schema 不匹配")
    if _sha256(zip_path) != str(manifest.get("sha256", "")).lower():
        raise RollbackError("代码备份 ZIP 哈希不符，拒绝回滚")
    roots = [safe_relpath(str(item)) for item in manifest.get("roots", [])]
    trash = trash_root / uuid.uuid4().hex
    trash.mkdir(parents=True, exist_ok=False)
    moved: List[tuple[Path, Path]] = []

    try:
        for root_name in roots:
            current = app_root / root_name
            if not current.exists():
                continue
            destination = trash / root_name.replace("/", "__")
            shutil.move(str(current), str(destination))
            moved.append((destination, current))

        restored = 0
        with zipfile.ZipFile(zip_path, "r") as archive:
            members = validate_release_zip(
                archive,
                install_roots=roots,
                protected_prefixes=[],
                max_uncompressed_bytes=4 * 1024 * 1024 * 1024,
            )
            for rel, info in members:
                with archive.open(info, "r") as src:
                    _atomic_stream_copy(src, app_root / rel)
                restored += 1
        return restored
    except BaseException as exc:
        for destination, original in moved:
            if destination.exists() and not original.exists():
                try:
                    shutil.move(str(destination), str(original))
                except OSError:
                    pass
        raise RollbackError(f"代码回滚失败：{exc}") from exc
