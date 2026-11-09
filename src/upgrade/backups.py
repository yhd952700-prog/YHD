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
import logging
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

logger = logging.getLogger("liuhao.upgrade.backups")

#: 数据目录下的运行期目录。里面的文件（实例锁、端口/心跳标记）由正在运行的
#: 进程独占打开，备份时读取会直接 Permission denied；它们也不是用户数据，
#: 升级备份不需要、也不应该还原它们。
_RUNTIME_DIR_NAME = "run"
#: 运行期锁文件后缀。任意层级出现都跳过：``run/instance.lock`` 只是最常见的
#: 一个，业务代码也可能在别处建锁。
_RUNTIME_LOCK_SUFFIX = ".lock"

#: 恢复码交接文件、凭据文件与 DPAPI 密文副本**永不**进入备份或离线导出。
#: 已生成的旧备份不追溯（其中的副本仍是旧行为），但新备份与导出必须为空。
#:
#: 用**前缀**匹配而不是精确名，是为了同时挡住迁移过程中的临时文件
#: （``model_config.key.<pid>.tmp``）和换机改名副本
#: （``integrity.key.unreadable-<时间戳>``）——它们的文件名变了，但内容
#: 仍是本机密钥，泄露后果一样。
_PROTECTED_BASENAME_PREFIXES = (
    "recovery_code",
    "recovery_handoff",
    # JWT 签名密钥（DPAPI 包裹后的 LIUHAO_JWT_SECRET=... 就写在 app.env 里）。
    "app.env",
    # 模型主密钥：明文裸文件、DPAPI 包裹文件、迁移临时文件都不能进备份。
    "model_config.key",
    "model_config_key.bin",
    # 身份库签名密钥：含换机时改名保留的 ``.unreadable-*`` 副本。
    "integrity.key",
    # 凭据/身份库本体：持有者可以伪造或直接读取本机账号。
    "auth_secrets.json",
    "human_identities.json",
    # 审计链 HMAC 密钥：不外传。
    "audit_chain.key",
    # 主密钥（44 B 明文）与移动端管理令牌（43 B 明文）：持有者能解密本机密文
    # 或接管本机 relay 管理面，且都没有跨机价值——同机恢复时磁盘上那份才是
    # 对的，不该由备份覆盖。临时副本（``master.key.<pid>.tmp``）同样挡住。
    "master.key",
    "admin.token",
    # relay 的设备表：每台已配对手机一条 ``secret``（32 B urlsafe）与最近
    # 10 个会话令牌，持有者可以冒充该手机连上本机 relay。同机恢复时磁盘上
    # 那份才是有效的，跨机恢复带过去也没有意义。
    "devices.json",
    # relay 的进程状态（admin/lan 端口、backend 地址、pid、lan_urls）：不含
    # 令牌，但整体属于运行期状态，恢复过来就是错的（端口/pid 是另一台机器
    # 的）。与 admin.token 同目录，一起挡掉，让 ``mobile_access/`` 整个目录
    # 不再成为备份的一部分。
    "relay.json",
)
_PROTECTED_BACKUP_NAMES = frozenset(
    {
        "auth_secrets.json",
        # 真实的主密钥文件名。旧版排除集误写成 ``model_config_key.bin``，
        # 导致 ``model_config.key`` 被原样打进备份。
        "model_config.key",
        # 身份库签名密钥与身份库本体：两者都能让持有者伪造/读取本机身份。
        "integrity.key",
        "human_identities.json",
        # 审计链 HMAC 密钥：不外传。恢复出带密钥的审计库但缺这把钥匙时，
        # 校验会明确降级为“仅结构检查”，而不是静默全绿/全红。
        "audit_chain.key",
        # JWT 签名密钥的落盘位置（含 SECRET_KEY），一律不进备份。
        "app.env",
        # 兼容 0.1.x 曾用的错误文件名：新代码不会再生成，但旧安装里可能残留。
        "model_config_key.bin",
        # 0.1.3 起不进备份的明文机密与 relay 状态。精确名放这里，临时/改名副本
        # 由上面的前缀表挡住（与 model_config.key 的处理方式一致）。
        "master.key",
        "admin.token",
        "devices.json",
        "relay.json",
    }
)


def _is_runtime_state_path(relpath: str) -> bool:
    """该相对路径是否属于运行期状态（不能、也不该进升级备份）。"""
    rel = str(relpath or "").replace("\\", "/").strip("/")
    if not rel:
        return False
    if rel == _RUNTIME_DIR_NAME or rel.startswith(_RUNTIME_DIR_NAME + "/"):
        return True
    return rel.rsplit("/", 1)[-1].lower().endswith(_RUNTIME_LOCK_SUFFIX)


def is_protected_state_file(relpath: str) -> bool:
    """该相对路径是否属于“不得打包”的恢复码/凭据类文件。"""
    name = str(relpath or "").replace("\\", "/").rsplit("/", 1)[-1].lower()
    if not name:
        return False
    # 原子替换的临时文件常以 "." 开头（``.model_config.key.migrate-*``），
    # 去掉前导点后再做前缀匹配，避免把"正在迁移中的密钥"漏进备份。
    name = name.lstrip(".")
    if name.startswith(_PROTECTED_BASENAME_PREFIXES):
        return True
    return name in _PROTECTED_BACKUP_NAMES


#: 旧备份「容忍」判断只针对本补丁新增的这几个名字：此前的备份按当时的
#: 排除集打包，必然含它们。旧备份里的模型密钥/凭据仍然一律拒绝（既有行为）。
_SECRET_BASENAME_PREFIXES = (
    "master.key",
    "admin.token",
    "devices.json",
    "relay.json",
)


def _is_secret_state_path(relpath: str) -> bool:
    name = str(relpath or "").replace("\\", "/").rsplit("/", 1)[-1].lower().lstrip(".")
    return bool(name) and name.startswith(_SECRET_BASENAME_PREFIXES)


def _declares_secrets_excluded(backup_dir: Path) -> bool:
    """备份清单是否声明「按不含密钥口径生成」。读不到按未声明处理。"""
    try:
        payload = json.loads(
            (Path(backup_dir) / "data-backup.json").read_text(encoding="utf-8")
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    return isinstance(payload, dict) and payload.get("secrets_excluded") is True


def _allow_new_master_key() -> bool:
    value = os.environ.get("LIUHAO_RESTORE_ALLOW_NEW_KEY", "")
    return value.strip().lower() in ("1", "true", "yes", "on")


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
        # 旧备份（本补丁之前生成）按当时的排除集打包，必然含 master.key /
        # admin.token。一律拒绝会让所有历史回滚点失效，所以只对「声明了不含
        # 密钥」的备份强制；旧备份放行并告警，恢复路径会跳过这两个文件。
        tolerated = [] if _declares_secrets_excluded(backup_dir) else [
            rel for rel in leaked if _is_secret_state_path(rel)
        ]
        hard = [rel for rel in leaked if rel not in tolerated]
        if hard:
            raise BackupCorrupt(
                "备份包含受保护文件（凭据/身份库/模型主密钥），拒绝使用："
                + ", ".join(hard)
            )
        logger.warning(
            "备份包含旧口径的明文密钥文件（历史备份，恢复时跳过、不写回磁盘）：%s",
            ", ".join(tolerated),
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

    # 运行期状态目录默认落在数据目录内（``<data>/upgrade-state``）；升级备份
    # 在安装版默认落在数据目录**之外**，但便携/开发形态仍是
    # ``<data>/upgrade-backups``。两种形态遍历数据目录时都必须跳过它们：备份
    # 目录会把“正在写入的备份”当成用户数据再备份一遍；状态目录的操作锁正被
    # 当前进程独占打开，读取会报 Permission denied，而它本身也不是用户数据。
    skipped = [dest_dir.resolve()] + [Path(item).resolve() for item in exclude_dirs]
    excluded_protected = 0
    excluded_runtime = 0

    for source in iter_files([data_dir]):
        if _is_under(source, skipped):
            continue
        rel = source.relative_to(data_dir).as_posix()
        safe_relpath(rel)
        if _is_runtime_state_path(rel):
            # 运行期目录/锁文件由正在运行的进程独占打开，读它会 Permission
            # denied；它们也不是用户数据。SQLite 主库仍走 VACUUM INTO 快照，
            # 因此跳过这些文件不会漏掉任何业务数据。
            excluded_runtime += 1
            continue
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
        if _is_runtime_state_path(rel):
            excluded_runtime += 1
            continue
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
        "excluded_runtime": excluded_runtime,
        # 备份口径：master.key / admin.token 已进排除集，不作为文件进包。
        # master_key_present 记录源目录当时是否有主密钥，供恢复端区分
        # 「同机恢复（文件本来就在）」与「跨机恢复（缺密钥必须明确失败）」。
        "secrets_excluded": True,
        "master_key_present": (Path(data_dir) / "master.key").is_file(),
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


def assert_restore_preconditions(data_dir: Path, backup_dir: Path) -> None:
    """恢复前的跨机判定：会不会把旧密文配上一把新密钥。

    备份按「不含密钥」口径生成（``secrets_excluded=true``），而源机当时确实
    有 ``master.key``（``master_key_present=true``），当前数据目录却没有——这就
    是「从另一台机器恢复」。此时若继续，``EncryptionManager`` 会静默生成一把
    新密钥，旧密文只会在使用时刻才解密失败，启动时不会报错；必须在**动任何
    文件之前**明确失败。

    ``manager._restore_backup`` 在替换代码之前调用它；``restore_data`` 自己
    也会再调一次，保证直接调用者同样受保护。清单缺失/不可读时不在这里报错，
    由 ``verify_backup`` / ``restore_data`` 的既有检查负责。
    """
    try:
        manifest = json.loads(
            (Path(backup_dir) / "data-backup.json").read_text(encoding="utf-8")
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return
    if not isinstance(manifest, dict):
        return
    if manifest.get("secrets_excluded") is not True:
        return
    if manifest.get("master_key_present") is not True:
        return
    master_key_path = Path(data_dir) / "master.key"
    if master_key_path.is_file() or _allow_new_master_key():
        return
    raise RollbackError(
        "跨机恢复缺少主密钥：这份备份按「不含密钥」策略生成"
        f"（data-backup.json 里 secrets_excluded=true），而当前数据目录没有 {master_key_path}。\n"
        "继续恢复会为旧密文生成一把新密钥：那些数据只会在使用时刻解密失败，"
        "启动时不会报错。\n"
        f"下一步：从原机复制 <原数据目录>\\master.key 到 {master_key_path} 后重试；\n"
        "若确认要把这份备份当作全新实例（放弃旧密文），设置 "
        "LIUHAO_RESTORE_ALLOW_NEW_KEY=1 后重试。"
    )


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
    # 跨机恢复预检：复制任何文件之前先判定，避免「旧密文 + 新密钥」。
    assert_restore_preconditions(data_dir, backup_dir)
    restored = 0
    skipped_protected = 0
    sqlite_targets: List[Path] = []
    for entry in manifest.get("entries", []):
        kind = entry.get("kind")
        rel = safe_relpath(str(entry.get("relpath", "")))
        if is_protected_state_file(rel):
            # 本机机密（master.key / admin.token / 凭据 / 模型密钥）永不从备份
            # 写回：同机恢复时磁盘上那份才是对的；旧备份里带着的副本也一并跳过。
            skipped_protected += 1
            continue
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
    if skipped_protected:
        logger.info(
            "恢复时跳过受保护文件 %d 个（本机机密不从备份写回）", skipped_protected
        )
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

    被占用的文件会让“整体移走”失败；此时退化为原地恢复：从备份 ZIP 覆盖，
    内容已经一致的成员直接跳过（不去碰被占用的句柄），最后清掉备份清单之外
    的文件。这样即使升级中断在被占用文件上，回滚仍能补回完整的旧版本，而不是
    留下“一半新、一半旧”的树。
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
    existed_before: dict[str, bool] = {}
    left_in_place: dict[str, Path] = {}

    try:
        for root_name in roots:
            current = app_root / root_name
            existed_before[root_name] = current.exists()
            if not existed_before[root_name]:
                continue
            destination = trash / root_name.replace("/", "__")
            if _move_aside(current, destination):
                moved.append((destination, current))
            else:
                # 移不走（文件被占用）：原树留在原地，走原地恢复。
                left_in_place[root_name] = destination

        restored = 0
        with zipfile.ZipFile(zip_path, "r") as archive:
            members = validate_release_zip(
                archive,
                install_roots=roots,
                protected_prefixes=[],
                max_uncompressed_bytes=4 * 1024 * 1024 * 1024,
            )
            for rel, info in members:
                target = app_root / rel
                if _zip_member_matches(target, archive, info):
                    # 已经是旧版本内容（升级还没覆盖到它，或被占用但恰好未变）：
                    # 跳过写入，绝不为了“写一遍”去撞被占用的句柄。
                    continue
                with archive.open(info, "r") as src:
                    _atomic_stream_copy(src, target)
                restored += 1
            prune_failures = _prune_unlisted(
                app_root, roots, {rel for rel, _info in members}
            )
        if prune_failures:
            raise RollbackError(
                "代码回滚不完整，需人工介入："
                + "；".join(prune_failures)
                + f"。旧版本代码包：{zip_path}。"
            )
        return restored
    except RollbackError:
        raise
    except BaseException as exc:
        # 回滚必须回到“应用之前”的完整树，不能留下解压到一半的新旧混合目录。
        # 顺序：先删掉本次解压出来的半成品（它会让 trash 放不回去），再把
        # trash 原样移回；任何一步失败都显式报“需人工介入”并给出残留路径，
        # 绝不静默吞掉。
        failures: List[str] = []
        blocked: set[Path] = set()
        moved_names = {
            str(original.relative_to(app_root).as_posix()) for _d, original in moved
        }
        for _destination, original in moved:
            if not original.exists():
                continue
            try:
                _remove_path(original)
            except OSError as cleanup_exc:
                blocked.add(original)
                failures.append(f"半成品目录清理失败：{original}（{cleanup_exc}）")
        for root_name in roots:
            if root_name not in existed_before:
                # 移动阶段还没轮到它就中断了：这个目录从未被动过，不能删。
                continue
            if existed_before[root_name]:
                if root_name in moved_names:
                    continue
                partial = app_root / root_name
                if partial.exists():
                    copy = left_in_place.get(root_name)
                    detail = f"（回滚前副本：{copy}）" if copy and copy.exists() else ""
                    failures.append(
                        f"该目录无法整体移走，原地恢复未完成：{partial}{detail}"
                    )
                continue
            partial = app_root / root_name
            if not partial.exists():
                continue
            try:
                _remove_path(partial)
            except OSError as cleanup_exc:
                failures.append(f"新增目录清理失败：{partial}（{cleanup_exc}）")
        for destination, original in moved:
            if original in blocked or not destination.exists():
                continue
            try:
                original.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(destination), str(original))
            except OSError as restore_exc:
                failures.append(
                    f"旧版本放回失败：{original}（{restore_exc}；"
                    f"旧版本仍在 {destination}）"
                )
        if failures:
            raise RollbackError(
                "代码回滚失败，需人工介入："
                + "；".join(failures)
                + f"。旧版本代码包：{zip_path}；回滚回收区：{trash}。"
                "请关闭占用文件的进程后，从回收区或代码包手工恢复。"
            ) from exc
        raise RollbackError(f"代码回滚失败：{exc}") from exc


def _move_aside(source: Path, destination: Path) -> bool:
    """把 ``source`` 移入回收区；True 表示已整体移走。

    返回 False 表示只能“复制一份到回收区、原树留在原地”。这里刻意不用
    ``shutil.move``：它在 rename 失败时会 copytree + rmtree，而 rmtree 一旦
    撞上被占用的文件就会删掉一半——这正是“350 个新文件 + 221 个缺失”的来源。
    """
    try:
        os.rename(source, destination)
        return True
    except OSError:
        pass
    try:
        if source.is_dir() and not source.is_symlink():
            shutil.copytree(source, destination, symlinks=True)
        else:
            shutil.copy2(source, destination)
    except (OSError, shutil.Error):
        # 连副本都做不出来：清掉半份副本，仍然走原地恢复。
        try:
            if destination.exists():
                _remove_path(destination)
        except OSError:
            pass
        return False
    return False


def _zip_member_matches(
    target: Path, archive: zipfile.ZipFile, info: zipfile.ZipInfo
) -> bool:
    """``target`` 的内容是否与备份 ZIP 里的该成员逐字节一致。"""
    try:
        if not target.is_file() or target.stat().st_size != info.file_size:
            return False
        expected = hashlib.sha256()
        with archive.open(info, "r") as src:
            for chunk in iter(lambda: src.read(1024 * 1024), b""):
                expected.update(chunk)
        actual = hashlib.sha256()
        with open(target, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                actual.update(chunk)
    except OSError:
        return False
    return actual.digest() == expected.digest()


def _prune_unlisted(
    app_root: Path, roots: Sequence[str], listed: set[str]
) -> List[str]:
    """删掉安装根里备份清单之外的文件（升级中断留下的新版本残留）。"""
    failures: List[str] = []
    for root_name in roots:
        target = app_root / root_name
        if not target.is_dir() or target.is_symlink():
            continue
        for path in target.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(app_root).as_posix()
            if rel in listed:
                continue
            parts = rel.split("/")
            name = parts[-1]
            if "__pycache__" in parts or name.startswith((".", "unins")):
                continue
            try:
                path.unlink()
            except OSError as exc:
                failures.append(f"陈旧文件清理失败：{path}（{exc}）")
    return failures


def _remove_path(path: Path) -> None:
    """删除文件或目录树；符号链接只删链接本身。"""
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()
