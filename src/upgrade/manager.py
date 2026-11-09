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

import contextlib
import errno
import json
import logging
import os
import shutil
import tempfile
import threading
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from packaging.version import Version

from src.common.atomic_write import atomic_write_bytes, atomic_write_text
from src.common.instance_lock import (
    InstanceGuardUnavailable,
    InstanceLock,
    InstanceLockBusy,
)
from src.common.redaction import redact_log_text

from .archive import (
    extract_zip_members,
    is_protected,
    read_package_version,
    safe_relpath,
    validate_release_zip,
)
from .backups import (
    backup_code,
    backup_data,
    export_data_snapshot,
    restore_code,
    restore_data,
    verify_backup,
)
from .console_contract import FrontendContractError, verify_package
from .dataguard import capture_table_inventory, verify_data_paths
from .dataversion import (
    DATA_SCHEMA_VERSION,
    ensure_data_readable,
    read_data_schema,
    stamp_data_schema,
    target_data_schema,
)
from .downloader import _sha256_file, download_release, verify_signature
from .errors import (
    ApplyError,
    BackupCorrupt,
    BackupNotFound,
    ChecksumMismatch,
    DiskSpaceError,
    ManifestError,
    PreflightError,
    RollbackError,
    SelfCheckError,
    SignatureError,
    UpdateUnreachableError,
    UpgradeError,
    VersionReadError,
)
from .filemanifest import verify_file_manifest
from .manifest_trust import (
    ManifestTrustState,
    ManifestTrustStore,
    canonical_manifest_sha256,
    manifest_sequence,
    verify_manifest_signature,
    TRUST_STATE_FILENAME,
)
from .ledger import UpgradeLedger
from .selfcheck import SelfCheckConfig, run_self_check
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
from .versioning import is_newer, read_current_version

_BACKUP_SCHEMA = "liuhao.upgrade-backup/1"
_CHECK_STATE_SCHEMA = "liuhao.update-check/1"
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
#: 升级包允许写入的安装根。必须与 ``packaging/stage_payload.py`` 组装出的
#: 安装目录一致 —— 升级包就是“安装树”，不是“源码仓库”。早先只允许
#: ``src/`` 等目录，导致根级模块（``error_types.py``，被 ``src`` 裸名导入）
#: 与 ``config/`` 永远不进包，干净机器升级后自检直接 ModuleNotFoundError。
#:
#: ``docs`` 仍作为根保留：升级包只带白名单文档（见 packaging/make_release.py），
#: 但清理旧文件时必须能遍历到它，才能把上一版遗留的 500 多个内部文档删掉。
INSTALL_ROOTS = (
    "src",
    "config",
    "scripts",
    "console",
    "assets",
    "docs",
    "capability-registry.yaml",
    "error_types.py",
    "LiuHao.bat",
    "START-HERE.txt",
    "pyproject.toml",
    "requirements.txt",
    "oss-registry.yaml",
    # 合规材料：随升级包更新，避免用户升级后许可证文本停在旧版本。
    "THIRD-PARTY-NOTICES.txt",
    "sbom-cyclonedx.json",
    "licenses",
    # Package-internal version marker; installed at the app root so
    # `versioning.read_current_version` keeps working without pyproject edits.
    "LIUHAO_RELEASE.json",
)
_DEFAULT_INSTALL_ROOTS = INSTALL_ROOTS
_IN_FLIGHT_PHASES = ("backing_up", "applying", "self_checking", "rolling_back")

#: 安装后必须保留的程序目录运行期文件：由启动器/安装器生成，不属于任何发布包。
_STALE_PRESERVE = (
    "config/app.env",
    "config/integrity.key",
    "config/auth_secrets.json",
    "config/human_identities.json",
)
#: 清理旧文件时不碰的后缀：密钥/锁文件，以及无需清理的字节码缓存。
_STALE_SKIP_SUFFIX = (".pyc", ".pyo", ".lock", ".pem", ".key", ".crt")
#: 完全由构建产物决定的安装根：旧文件必须清干净（旧哈希前端资源会让自检回滚）。
_STRICT_PRUNE_ROOTS = ("console",)

#: 上一版安装清单：apply 成功后落 ``<data>/upgrade-state``，下一次升级以它为
#: 唯一删除依据。用户自建文件不在我方任何清单里，因此不会被升级删掉。
INSTALLED_FILES_FILENAME = "installed-files.json"
INSTALLED_FILES_SCHEMA = "liuhao.installed-files/1"

#: 首次升级（还没有上一版清单）的退路：只删我方明确产出、安装版永不使用的
#: 陈旧项。必须与 ``packaging/stage_payload.CONFIG_EXCLUDES`` 的非凭据部分
#: 保持一致；这些路径不可能由用户手写产出，所以按名单删除不会误伤。
#: 目录型排除（``production/``、``external_anchor/``）不在此列：那属于用户
#: 可能往里放东西的位置，宁可保留并报告，也不整棵子树删。
_STALE_FALLBACK_FILES = (
    "config/production.yaml",
    "config/development.yaml",
    "config/testing.yaml",
    "config/docker.env",
    "config/crontab",
    "config/supervisor.conf",
    "config/nginx.conf",
    "config/prometheus.yml",
    "config/grafana-dashboard.json",
    "config/alert-rules.yaml",
)

logger = logging.getLogger(__name__)

#: 升级失败对外的唯一主文案。路径、异常原文、内部命令只进 ``technical_detail``，
#: 由界面"技术详情"折叠展示，且写入前统一过 ``redact_log_text``。
UPGRADE_FAILED_ROLLED_BACK = "升级失败，已回滚。请重试，或导出诊断包。"
UPGRADE_FAILED_NOT_ROLLED_BACK = "升级失败，未能回滚。请重试回滚，或导出诊断包。"
UPGRADE_FAILED_NOT_APPLIED = "升级失败，未修改任何文件。请重试，或导出诊断包。"
UPGRADE_FAILED_DOWNLOAD = "升级失败，新版本没有安装。请检查网络后重试，或导出诊断包。"
ROLLBACK_FAILED = "回滚失败。请重试，或导出诊断包。"

#: 安装树标志。``installer/liuhao.iss`` 把解释器装进 ``<app>/runtime``，
#: 源码仓库与便携版没有这个目录。
_INSTALL_MARKER = Path("runtime") / "python.exe"

#: 安装器写在程序目录的数据目录标记。安装时用户可能把数据选在任意位置
#: （含中文/空格），只有它能把真实位置还原回来。
_DATA_DIR_MARKER = Path("data-dir.txt")


def _marker_data_dir(app_root: Path) -> Path | None:
    """读安装器写的 ``data-dir.txt``。

    编码规则与 ``scripts/launch_installed.py::_marker_data_dir`` 一致：新版
    UTF-8 **带 BOM**（BOM 是"这是 UTF-8"的确证，避免把中文路径按系统 ANSI
    解码成另一个目录）；旧版无 BOM 的系统 ANSI（先 UTF-8，失败再 ``mbcs``）。
    解出替换字符说明文件损坏，返回 ``None`` 交给安全默认值。
    """
    marker = app_root / _DATA_DIR_MARKER
    try:
        raw = marker.read_bytes()
    except OSError:
        return None
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return None
    else:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            try:
                text = raw.decode("mbcs")
            except (UnicodeDecodeError, LookupError):
                return None
    text = text.strip().strip('"').strip()
    if not text or "\ufffd" in text:
        return None
    return Path(text).expanduser().resolve()


def default_data_dir(app_root: str | Path) -> Path:
    """按安装器的真实规则解析用户数据目录。

    安装树（``<app>/runtime/python.exe`` 存在）**优先读安装器写下的
    ``data-dir.txt``**——手工或计划任务直接跑 CLI 时没有启动器注入
    ``LIUHAO_DATA_DIR``，只有这里读 marker 才能和真实安装位置一致；读不到
    才回退 ``%LOCALAPPDATA%\\LiuHao\\data``。开发/便携版没有安装器，运行数据
    仍在 ``<app-root>/data``。

    每次解析都用 INFO 日志写明来源（marker / 安装默认 / 便携目录），排障时
    不用再猜 CLI 把数据写到哪去了。
    """
    root = Path(app_root).resolve()
    if (root / _INSTALL_MARKER).is_file():
        marker = _marker_data_dir(root)
        if marker is not None:
            logger.info("数据目录来源：程序目录 data-dir.txt -> %s", marker)
            return marker
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home())
        installed_default = (base / "LiuHao" / "data").resolve()
        logger.info(
            "数据目录来源：安装树默认 %%LOCALAPPDATA%%\\LiuHao\\data -> %s",
            installed_default,
        )
        return installed_default
    portable = root / "data"
    logger.info("数据目录来源：便携版程序目录 -> %s", portable)
    return portable


def resolve_data_dir(
    app_root: str | Path, data_dir: str | Path | None = None
) -> Path:
    """显式参数 > ``LIUHAO_DATA_DIR`` > 安装树/便携默认。

    CLI、HTTP API 和直接构造 ``UpgradeConfig`` 的调用方都走这里，避免
    出现“CLI 检查一个库、API 检查另一个库”。
    """
    raw = data_dir if data_dir else os.environ.get("LIUHAO_DATA_DIR")
    if raw:
        resolved = Path(raw).expanduser().resolve()
        logger.info(
            "数据目录来源：%s -> %s",
            "显式参数" if data_dir else "环境变量 LIUHAO_DATA_DIR",
            resolved,
        )
        return resolved
    return default_data_dir(app_root)


#: 运行期状态目录名。历史版本写在程序目录，卸载器不认这些运行期文件，
#: 会残留在 ``<app>/upgrade-state``；现在统一落数据目录。
STATE_DIRNAME = "upgrade-state"
#: 升级备份目录名。未显式配置 ``LIUHAO_UPGRADE_BACKUP_ROOT`` 时落在
#: ``<data>/upgrade-backups``（与数据同盘），状态接口会明确提示同盘风险。
BACKUP_DIRNAME = "upgrade-backups"
#: 备份前剩余空间的最低倍数：备份要同时容纳代码包与数据副本，按 2 倍估算，
#: 避免"备份写到一半把目标盘写满"。
BACKUP_SPACE_HEADROOM = 2.0

#: 旧备份迁移记录（写在状态目录，供设置页提示"旧备份已搬到新位置"）。
BACKUP_MIGRATION_RECORD = "backup-migration.json"


def _volume_anchor(path: str | Path) -> str:
    """返回路径所在盘符（``d:``）；非 Windows 或相对路径返回空串。"""
    return os.path.splitdrive(str(path))[0].lower()


def same_volume(first: str | Path, second: str | Path) -> bool:
    """两个路径是否在同一块盘上（UNC 共享路径按共享名比较）。"""
    return _volume_anchor(first) == _volume_anchor(second)


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _probe_explicit_backup_root(root: Path) -> bool:
    """对**显式配置**的备份根做真写探针：建目录 + 写文件 + 删掉。

    ``os.access`` 在 Windows 上对 ACL/组策略/漫游重定向不可靠；只有真写才能
    排除"以后备份写不进去"的盘。显式配置（``LIUHAO_UPGRADE_BACKUP_ROOT``）是
    用户的明确意图，探针必须落在该根内，失败就如实报"该盘不可写"，绝不改道。
    """
    try:
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".liuhao-write-probe"
        probe.write_text("probe", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def default_state_dir(
    app_root: str | Path, data_dir: str | Path | None = None
) -> Path:
    """升级状态目录：``<data>/upgrade-state``（不在程序目录内）。"""
    return resolve_data_dir(app_root, data_dir) / STATE_DIRNAME


def default_backup_root(
    app_root: str | Path, data_dir: str | Path | None = None
) -> Path:
    """未显式配置备份根时的默认值：``<data>/upgrade-backups``（同盘）。

    **不再静默挑选其他盘**：跨盘备份必须由 ``LIUHAO_UPGRADE_BACKUP_ROOT`` 显式
    指定。没有配置就按"未启用跨盘备份"处理，备份留在数据目录内，并由
    ``/v1/upgrade/status`` 明确提示同盘风险。便携/开发形态同样自包含，测试与
    开发不得往用户目录或其他盘写数据。
    """
    data = resolve_data_dir(app_root, data_dir)
    fallback = data / BACKUP_DIRNAME
    logger.info(
        "升级备份目录来源：未配置 LIUHAO_UPGRADE_BACKUP_ROOT -> %s（与数据同盘，未启用跨盘备份）",
        fallback,
    )
    return fallback


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
    #: 数据已被更高 schema 版本写过时，是否允许继续安装更低版本。
    allow_data_downgrade: bool = False
    self_check: SelfCheckConfig = field(default_factory=SelfCheckConfig)
    #: 备份根来源：``configured``（显式配置） / ``data_dir``（未配置，与数据同盘） /
    #: ``portable``（便携/开发形态，数据目录在程序目录内）。
    backup_root_source: str = field(init=False, default="")
    #: 显式配置的备份根是否真写通过；未配置时恒为 True。
    backup_root_available: bool = field(init=False, default=True)
    #: 备份根不可用时的中文原因（直接给状态接口展示）。
    backup_root_error: str = field(init=False, default="")

    def __post_init__(self) -> None:
        self.app_root = Path(self.app_root).resolve()
        self.data_dir = resolve_data_dir(self.app_root, self.data_dir)
        if self.backup_root:
            candidate = Path(self.backup_root).expanduser().resolve()
            self.backup_root = candidate
            self.backup_root_source = "configured"
            # 显式配置是用户的明确意图：探针与备份都必须落在这个根里，
            # 失败就如实报不可写，绝不改道到数据目录。
            self.backup_root_available = _probe_explicit_backup_root(candidate)
            if not self.backup_root_available:
                self.backup_root_error = (
                    f"配置的备份盘不可写：{candidate}。备份未启用，升级将被拒绝；"
                    "请检查 LIUHAO_UPGRADE_BACKUP_ROOT 指向的磁盘是否存在、"
                    "是否有写权限、空间是否已满。"
                )
                logger.error("upgrade: %s", self.backup_root_error)
        else:
            self.backup_root = default_backup_root(self.app_root, self.data_dir)
            self.backup_root_source = (
                "portable" if _is_within(self.backup_root, self.app_root) else "data_dir"
            )
        self.state_dir = (
            Path(self.state_dir).resolve()
            if self.state_dir
            else default_state_dir(self.app_root, self.data_dir)
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
        self._migrate_legacy_dirs()
        self.config.state_dir.mkdir(parents=True, exist_ok=True)
        if self.config.backup_root_available:
            self.config.backup_root.mkdir(parents=True, exist_ok=True)
        # 旧默认位置 ``<data>/upgrade-backups`` 里已经有的备份必须搬出数据目录：
        # 数据目录一删，备份跟着没，正是这次要修的问题。放在 mkdir 之后，迁移
        # 记录才能写进状态目录。
        self._migrate_legacy_backups()
        self.ledger = UpgradeLedger(self.config.state_dir / "upgrade-ledger.db")
        self.status_path = self.config.state_dir / "status.json"
        self.check_state_path = self.config.state_dir / "last-check.json"
        # 签名清单的防重放水位线（sequence / 最高可信版本 / 清单摘要）。
        self.manifest_trust_store = ManifestTrustStore(
            self.config.state_dir / TRUST_STATE_FILENAME
        )
        self._current_version: Optional[str] = None
        self._check_trigger = "manual"
        # A process that was killed mid-apply leaves status at 应用中/自检中 and
        # an unfinished `apply/in_progress` ledger row. Recover immediately:
        # never let the user restart into an undecidable "unknown" state.
        self._recover_interrupted_upgrade()

    def _migrate_legacy_dirs(self) -> None:
        """把旧版本写在程序目录的 ``upgrade-state``/``upgrade-backups`` 搬到数据目录。

        卸载器只认识安装清单里的文件，运行期写进程序目录的状态和备份会残留；
        这里一次性迁移。迁移失败（权限、被占用）只告警，绝不让升级入口起不来：
        旧目录原地保留即可，新目录会重新记录。
        """
        legacy_pairs = (
            (self.config.app_root / STATE_DIRNAME, self.config.state_dir),
            (self.config.app_root / BACKUP_DIRNAME, self.config.backup_root),
        )
        for legacy, target in legacy_pairs:
            try:
                if target == self.config.backup_root and not self.config.backup_root_available:
                    continue
                if not legacy.is_dir() or legacy == target or target.exists():
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                legacy.rename(target)
                logger.info("upgrade: migrated legacy dir %s -> %s", legacy, target)
            except OSError as exc:
                logger.warning(
                    "upgrade: legacy dir %s kept in place, ignored (%s)",
                    legacy, exc,
                )
                try:
                    if not target.exists():
                        shutil.copytree(legacy, target, dirs_exist_ok=True)
                        logger.info("upgrade: copied legacy dir %s -> %s", legacy, target)
                        shutil.rmtree(legacy, ignore_errors=True)
                except OSError as copy_exc:
                    logger.warning(
                        "upgrade: cannot migrate legacy dir %s (%s)", legacy, copy_exc,
                    )

    def _migrate_legacy_backups(self) -> None:
        """把旧默认位置 ``<data>/upgrade-backups`` 里的备份搬到新位置。

        选择**自动搬走**而不是"保留提示"或"双写一段时间"：

        * 留着旧副本，等于保留"数据目录一删，备份一起没"的原缺陷；
        * 双写要同时维护两处、回滚时还可能挑到旧目录里那份，出错面更大；
        * 搬走之后只有一个位置可指向，数据丢了时的"打开备份目录"不会指错。

        每份完整备份先复制到 ``.<id>.migrating`` 再改名，中断后重跑会跳过
        已迁移项；任一步失败只告警并保留原样，绝不让升级入口起不来。
        """
        legacy = self.config.data_dir / BACKUP_DIRNAME
        target_root = self.config.backup_root
        if not self.config.backup_root_available:
            # 显式配置的备份盘不可写：不要在旧备份上做任何搬运/删除。
            return
        if legacy == target_root or not legacy.is_dir():
            return
        try:
            children = sorted(child for child in legacy.iterdir() if child.is_dir())
        except OSError as exc:
            logger.warning("upgrade: 旧备份目录 %s 无法读取，保持原样（%s）", legacy, exc)
            return

        moved = 0
        skipped = 0
        for child in children:
            target = target_root / child.name
            if not (child / "backup.json").is_file():
                # 不完整/未知目录：既不删也不搬，避免把半截状态带到新位置。
                logger.warning("upgrade: 旧备份目录 %s 缺少 backup.json，保持原样", child)
                continue
            if (target / "backup.json").is_file():
                # 已经迁移过一次；旧副本内容已在新位置，可以安全删除。
                shutil.rmtree(child, ignore_errors=True)
                skipped += 1
                continue
            staging = target_root / f".{child.name}.migrating"
            try:
                if staging.exists():
                    shutil.rmtree(staging, ignore_errors=True)
                shutil.copytree(child, staging)
                if target.exists():
                    shutil.rmtree(target, ignore_errors=True)
                staging.rename(target)
                shutil.rmtree(child, ignore_errors=True)
                moved += 1
            except OSError as exc:
                logger.warning("upgrade: 迁移旧备份 %s 失败，本次保留原样（%s）", child, exc)
                shutil.rmtree(staging, ignore_errors=True)

        try:
            legacy.rmdir()  # 只在已清空时成功；还有残留就保持目录
        except OSError:
            pass
        if moved or skipped:
            self._record_backup_migration(legacy, target_root, moved, skipped)
        logger.info(
            "upgrade: 旧备份迁移：%s 份搬到 %s，%s 份已存在，源目录 %s",
            moved, target_root, skipped, "已清空" if not legacy.exists() else "仍有残留",
        )

    def _record_backup_migration(
        self, old_root: Path, new_root: Path, moved: int, skipped: int
    ) -> None:
        """写迁移记录，供设置页告诉用户"旧备份已经搬到哪"。"""
        record = {
            "schema": "liuhao.backup-migration/1",
            "from": str(old_root),
            "to": str(new_root),
            "moved": moved,
            "skipped": skipped,
            "at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            atomic_write_text(
                self.config.state_dir / BACKUP_MIGRATION_RECORD,
                json.dumps(record, ensure_ascii=False, indent=2),
            )
        except OSError as exc:
            logger.warning("upgrade: 迁移记录写入失败（%s）", exc)

    def backup_migration(self) -> Optional[Dict[str, Any]]:
        """读旧备份迁移记录；没有迁移过返回 ``None``。"""
        try:
            data = json.loads(
                (self.config.state_dir / BACKUP_MIGRATION_RECORD).read_text(encoding="utf-8")
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def backup_summary(self) -> Dict[str, Any]:
        """备份位置 + 是否与数据同盘 + 最近一次备份（设置页三行信息的唯一来源）。"""
        backups = self.list_backups(limit=500)
        latest = backups[0] if backups else {}
        same = same_volume(self.config.data_dir, self.config.backup_root)
        configured = self.config.backup_root_source == "configured"
        available = self.config.backup_root_available
        if not available:
            label = "备份盘不可写，备份未启用"
            warning = self.config.backup_root_error
        elif same:
            label = "备份与数据在同一磁盘"
            warning = "备份与数据在同一磁盘，磁盘损坏时无法恢复。"
            if not configured:
                warning += (
                    "当前未配置 LIUHAO_UPGRADE_BACKUP_ROOT（未启用跨盘备份）；"
                    "如需跨盘备份，请把它指向另一块磁盘。"
                )
        else:
            label = "跨盘备份已启用"
            warning = ""
        return {
            "root": str(self.config.backup_root),
            "data_dir": str(self.config.data_dir),
            "source": self.config.backup_root_source,
            "configured": configured,
            "available": available,
            "error": self.config.backup_root_error,
            "label": label,
            "warning": warning,
            "same_volume": same,
            "cross_disk": available and not same,
            "data_drive": _volume_anchor(self.config.data_dir).upper(),
            "backup_drive": _volume_anchor(self.config.backup_root).upper(),
            "backup_count": len(backups),
            "last_backup_at": latest.get("created_at"),
            "last_backup_id": latest.get("backup_id"),
            "migration": self.backup_migration(),
        }

    # ------------------------------------------------------------------
    # 互斥：同一安装根只允许一个升级/回滚在跑
    # ------------------------------------------------------------------
    _operation_thread_lock = threading.Lock()

    @contextlib.contextmanager
    def _exclusive(self, action: str, error_cls):
        """跨线程 + 跨进程的升级互斥。

        web 端两个窗口同时点「升级」会落到两个线程（FastAPI 的同步路由跑在
        线程池里），命令行与托盘也可能各起一个进程；升级会先备份、再逐个替换
        文件，两个执行体交错时备份与还原都不可信。这里用 OS 文件锁保证同一
        安装根只有一个执行体，第二个直接得到可读拒绝。
        """
        if not self._operation_thread_lock.acquire(blocking=False):
            raise error_cls(f"另一个{action}正在进行中，请等它结束后重试")
        guard = InstanceLock(self.config.state_dir, subdir="", name="upgrade-op")
        try:
            try:
                guard.acquire()
            except InstanceLockBusy as exc:
                raise error_cls(
                    f"另一个{action}正在进行中（可能是另一个窗口或命令行进程），"
                    "请等它结束后重试"
                ) from exc
            except InstanceGuardUnavailable as exc:
                raise error_cls(f"无法锁定升级工作目录：{exc}") from exc
            try:
                yield
            finally:
                guard.release()
        finally:
            self._operation_thread_lock.release()

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

    def data_schema_status(self) -> Dict[str, Any]:
        """数据 schema 版本与当前代码的兼容性（只读，不修改任何文件）。"""
        state = read_data_schema(self.config.data_dir)
        version = state.version if state else None
        compatible = version is None or version <= DATA_SCHEMA_VERSION
        return {
            "data_dir": str(self.config.data_dir),
            "supported_version": DATA_SCHEMA_VERSION,
            "version": version,
            "written_by": state.written_by if state else "",
            "updated_at": state.updated_at if state else "",
            "compatible": compatible,
            "action": (
                ""
                if compatible
                else (
                    "数据由更高版本写入：当前代码没有该 schema 的读写规则，"
                    "继续使用可能丢数据。请升级到更高版本，或先备份后重建数据。"
                )
            ),
        }

    def _recover_interrupted_upgrade(self) -> None:
        """Resolve a previous process that died mid-upgrade.

        Called from ``__init__`` so a restart (or a plain ``status`` command)
        turns 应用中/自检中/回滚中 into a definite outcome. The backup recorded
        in the unfinished ledger row is verified first, then restored; a bad
        backup is reported as such instead of being pretended away.
        """
        state = self.status()
        phase = str(state.get("phase") or "")
        marker = (
            self.ledger.list_records(kind="apply", status="in_progress", limit=1)
            or [None]
        )[0]
        if phase not in _IN_FLIGHT_PHASES and marker is None and phase != "unknown":
            return

        phase_text = "升级"
        try:
            phase_text = PHASE_TEXT.get(Phase(phase), "升级")
        except ValueError:
            phase_text = "升级"
        backup_id = str((marker or {}).get("backup_id") or "")
        if not backup_id and phase in ("applying", "self_checking", "rolling_back"):
            # Never trust a stale backup id from a previous upgrade: backing_up
            # clears it, so any id present here belongs to the current attempt.
            backup_id = str(state.get("backup_id") or "")
        record_id = str((marker or {}).get("id") or "")
        from_version = str((marker or {}).get("from_version") or "")
        if not from_version and phase in ("applying", "self_checking"):
            from_version = str(state.get("current_version") or "")
        target_version = str((marker or {}).get("to_version") or "")

        if not backup_id:
            reason = (
                f"上次升级在{phase_text}阶段被中断，未能定位升级前备份；"
                "代码与数据可能未被修改，可直接重新执行升级或回滚"
            )
            if marker is not None:
                self.ledger.update(
                    str(marker["id"]),
                    status="interrupted",
                    reason=reason,
                    detail={"mutation_started": False, "interrupted_phase": phase},
                )
            else:
                self.ledger.record(
                    kind="apply",
                    status="interrupted",
                    from_version=from_version or None,
                    reason=reason,
                    detail={"mutation_started": False, "interrupted_phase": phase},
                )
            self._set_status(
                Phase.FAILED,
                "失败："
                + reason
                + f"。请执行：python -m src.upgrade status --app-root {self.config.app_root}",
                error=UPGRADE_FAILED_NOT_ROLLED_BACK,
                technical_detail=reason,
            )
            return

        try:
            if phase == "rolling_back" and marker is None:
                # The status only stored "current version"; the backup knows
                # which version we were rolling back to.
                meta = self._read_backup(backup_id)
                from_version = str(meta.get("from_version") or from_version)
            self._restore_backup(backup_id)
            restored, _ = self.current_version()
            if from_version and Version(restored) != Version(from_version):
                raise RollbackError(
                    f"回滚后版本为 {restored}，预期 {from_version}"
                )
        except (BackupNotFound, BackupCorrupt) as exc:
            reason = (
                f"上次升级在{phase_text}阶段被中断，且升级前备份不可用（{exc}）。"
                f"当前版本可能不完整，未自动修改任何文件。请重新下载 "
                f"{from_version or '旧版本'} 安装包后执行："
                f"python -m src.upgrade upgrade --app-root {self.config.app_root} "
                "--release-dir <发布目录> --allow-same-or-downgrade"
            )
            if record_id:
                self.ledger.update(
                    record_id,
                    status="interrupted_rollback_failed",
                    reason=reason,
                    detail={"interrupted_phase": phase, "backup_id": backup_id},
                )
            self.ledger.record(
                kind="rollback",
                status="failed",
                from_version=from_version or None,
                to_version=target_version or None,
                reason=reason,
                backup_id=backup_id,
                detail={"interrupted_phase": phase, "backup_unusable": True},
            )
            self._set_status(
                Phase.FAILED,
                "失败：" + reason,
                backup_id=backup_id,
                error=UPGRADE_FAILED_NOT_ROLLED_BACK,
                technical_detail=reason,
                record_id=record_id or None,
            )
            return
        except Exception as exc:  # noqa: BLE001 - surfaced to the user
            reason = (
                f"上次升级在{phase_text}阶段被中断，自动回滚失败：{exc}。"
                f"请执行：python -m src.upgrade rollback --app-root "
                f"{self.config.app_root} --backup-id {backup_id}"
            )
            if record_id:
                self.ledger.update(
                    record_id,
                    status="interrupted_rollback_failed",
                    reason=reason,
                    detail={"interrupted_phase": phase, "backup_id": backup_id},
                )
            self.ledger.record(
                kind="rollback",
                status="failed",
                from_version=from_version or None,
                to_version=target_version or None,
                reason=reason,
                backup_id=backup_id,
            )
            self._set_status(
                Phase.FAILED,
                "失败：" + reason,
                backup_id=backup_id,
                error=UPGRADE_FAILED_NOT_ROLLED_BACK,
                technical_detail=reason,
                record_id=record_id or None,
            )
            return

        reason = f"升级进程在{phase_text}阶段被中断，重启后已自动回滚"
        if record_id:
            self.ledger.update(
                record_id,
                status="interrupted_rolled_back",
                reason=reason,
                detail={"interrupted_phase": phase, "backup_id": backup_id},
            )
        rollback_record = self.ledger.record(
            kind="rollback",
            status="rolled_back",
            from_version=from_version or None,
            to_version=restored,
            reason=reason,
            backup_id=backup_id,
            detail={"interrupted_phase": phase},
        )
        self._set_status(
            Phase.ROLLED_BACK,
            f"已回滚：上次升级在{phase_text}阶段被中断，已自动恢复到 "
            f"{restored}（备份 {backup_id}）",
            target_version=restored,
            backup_id=backup_id,
            record_id=rollback_record.get("id"),
        )

    def history(self, *, limit: int = 50, kind: Optional[str] = None) -> List[Dict[str, Any]]:
        return self.ledger.list_records(kind=kind, limit=limit)

    def last_check(self) -> Dict[str, Any]:
        """Last update check time/result, shared by manual and weekly checks."""
        if not self.check_state_path.is_file():
            return {}
        try:
            return json.loads(self.check_state_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return {"status": "unreadable", "message": "上次检查记录无法读取"}

    def annotate_last_check(
        self,
        *,
        notified: bool,
        notify_results: Sequence[Dict[str, Any]] = (),
        next_check_at: Optional[str] = None,
        job_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Add notification details to the last check record (weekly job)."""
        state = self.last_check() or {"schema": _CHECK_STATE_SCHEMA}
        state["notified"] = bool(notified)
        state["notify_results"] = list(notify_results)
        if next_check_at:
            state["next_check_at"] = next_check_at
        if job_id:
            state["job_id"] = job_id
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        atomic_write_text(
            self.check_state_path,
            json.dumps(state, ensure_ascii=False, indent=2),
        )
        return state

    def data_paths(self) -> List[Path]:
        """Public view of the SQLite/state paths guarded by preflight/self-check."""
        return self._data_guard_paths()

    def list_backups(self, *, limit: int = 50) -> List[Dict[str, Any]]:
        """User-visible list of pre-upgrade snapshots, newest first."""
        items: List[Dict[str, Any]] = []
        for meta_path in self.config.backup_root.glob("*/backup.json"):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            if meta.get("schema") != _BACKUP_SCHEMA:
                continue
            backup_dir = meta_path.parent
            items.append(
                {
                    "backup_id": meta.get("backup_id") or backup_dir.name,
                    "created_at": meta.get("created_at", ""),
                    "from_version": meta.get("from_version", ""),
                    "to_version": meta.get("to_version", ""),
                    "code_file_count": int(meta.get("code_file_count") or 0),
                    "data_file_count": int(meta.get("data_file_count") or 0),
                    "data_sqlite_count": int(meta.get("data_sqlite_count") or 0),
                    "data_total_bytes": int(meta.get("data_total_bytes") or 0),
                    "key_tables": meta.get("key_tables") or {},
                    "has_data_snapshot": (backup_dir / "data-backup.json").is_file(),
                    "path": str(backup_dir),
                }
            )
        items.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return items[: max(1, int(limit))]

    def export_snapshot(
        self,
        dest_dir: str | Path,
        *,
        backup_id: Optional[str] = None,
        record_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Export the pre-upgrade data snapshot to a standalone directory."""
        resolved = self._resolve_backup_id(backup_id, record_id)
        meta = self._read_backup(resolved)
        exported = export_data_snapshot(
            self.config.backup_root / resolved,
            Path(dest_dir),
            backup_meta=meta,
        )
        return {
            "backup_id": exported.backup_id,
            "destination": str(exported.destination),
            "manifest_path": str(exported.manifest_path),
            "file_count": exported.file_count,
            "sqlite_count": exported.sqlite_count,
            "total_bytes": exported.total_bytes,
        }

    def _resolve_backup_id(
        self, backup_id: Optional[str], record_id: Optional[str] = None
    ) -> str:
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
        return str(backup_id)

    def _set_status(
        self,
        phase: Phase,
        message: str,
        *,
        target_version: Optional[str] = None,
        backup_id: Optional[str] = None,
        error: Optional[str] = None,
        technical_detail: Optional[str] = None,
        record_id: Optional[str] = None,
        detail: Optional[Dict[str, Any]] = None,
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
            "backup_id": (
                previous.get("backup_id") if backup_id is None else backup_id
            ),
            "last_error": error,
            # 路径 / 异常原文只进这里（且过脱敏），由前端"技术详情"折叠展示；
            # 主文案（message / last_error）只放中文分类结论。
            "technical_detail": (
                redact_log_text(technical_detail) if technical_detail else None
            ),
            "record_id": record_id or previous.get("record_id"),
            # 阶段详情：目前只承载"本次清理了什么、留下了什么"，让升级结果
            # 可追溯；不参与 API 对外裁剪，管理员可通过状态文件查看。
            "detail": detail,
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
    # Disk-space preflight (before the first real byte is written)
    # ------------------------------------------------------------------
    @staticmethod
    def _tree_bytes(paths: Sequence[Path]) -> int:
        total = 0
        for path in paths:
            path = Path(path)
            if path.is_file():
                total += path.stat().st_size
                continue
            if not path.is_dir():
                continue
            for candidate in path.rglob("*"):
                if not candidate.is_file() or candidate.is_symlink():
                    continue
                try:
                    total += candidate.stat().st_size
                except OSError:
                    continue
        return total

    @staticmethod
    def _mb(value: int) -> str:
        return f"{value / (1024 * 1024):.1f} MB"

    def _disk_space_requirement(self, install_bytes: int) -> int:
        install_roots = [self.config.app_root / root for root in self.config.install_roots]
        code_bytes = self._tree_bytes(install_roots)
        data_bytes = self._tree_bytes(self._data_guard_paths())
        # 8 MiB covers per-file temp copies, backup manifests and filesystem
        # slack; it is deliberately conservative so we stop early.
        return install_bytes + code_bytes + data_bytes + 8 * 1024 * 1024

    def _backup_space_requirement(self) -> int:
        """备份盘的独立空间要求：代码 + 数据副本按 ``BACKUP_SPACE_HEADROOM`` 倍估算。"""
        install_roots = [self.config.app_root / root for root in self.config.install_roots]
        code_bytes = self._tree_bytes(install_roots)
        data_bytes = self._tree_bytes(self._data_guard_paths())
        return int((code_bytes + data_bytes) * BACKUP_SPACE_HEADROOM) + 8 * 1024 * 1024

    def _check_disk_space(self, install_bytes: int) -> None:
        current = self._current_version or "原版本"
        if not self.config.backup_root_available:
            raise DiskSpaceError(
                self.config.backup_root_error
                or f"备份盘不可写，备份未启用，升级将被拒绝。备份根：{self.config.backup_root}"
            )
        # 备份前先单独检查备份盘：按"备份体积 × 2 倍余量"拒绝，避免写到一半
        # 把目标盘写满，留下半截备份。
        backup_required = self._backup_space_requirement()
        try:
            backup_usage = shutil.disk_usage(str(self.config.backup_root))
        except OSError as exc:
            raise DiskSpaceError(
                f"备份盘空间检查失败：无法读取 {self.config.backup_root} 的剩余空间（{exc}）。"
                f"备份未启用，升级将被拒绝；尚未修改任何文件，当前版本 {current} 仍完整。"
                f"请检查 LIUHAO_UPGRADE_BACKUP_ROOT 指向的磁盘后重试。"
            ) from exc
        if backup_usage.free < backup_required:
            raise DiskSpaceError(
                f"磁盘空间不足：备份盘（{self.config.backup_root}）备份代码与数据"
                f"按 {BACKUP_SPACE_HEADROOM:g} 倍余量至少需要约 {self._mb(backup_required)}，"
                f"{self.config.backup_root} 仅剩 {self._mb(backup_usage.free)}。"
                f"备份未启用，升级已被拒绝；尚未修改任何文件，当前版本 {current} 仍完整。"
                f"请清理备份盘，或把 LIUHAO_UPGRADE_BACKUP_ROOT 指向空间充足的磁盘后重试。"
            )
        required = self._disk_space_requirement(install_bytes)
        volumes = (
            ("应用目录", self.config.app_root),
            ("备份目录", self.config.backup_root),
            ("临时目录", Path(tempfile.gettempdir())),
        )
        for label, path in volumes:
            try:
                usage = shutil.disk_usage(str(path))
            except OSError:
                continue
            if usage.free < required:
                current = self._current_version or "原版本"
                raise DiskSpaceError(
                    f"磁盘空间不足：升级至少需要约 {self._mb(required)}，"
                    f"{label} {path} 仅剩 {self._mb(usage.free)}。"
                    f"尚未修改任何文件，当前版本 {current} 仍完整。"
                    f"请清理磁盘后重新执行：python -m src.upgrade upgrade "
                    f"--app-root {self.config.app_root}"
                )

    def _disk_error(self, exc: OSError, during: str) -> DiskSpaceError:
        current = self._current_version or "原版本"
        return DiskSpaceError(
            f"磁盘空间不足：{during}写入失败（{exc}）。"
            f"尚未修改任何文件，当前版本 {current} 仍完整。"
            f"请清理磁盘后重新执行：python -m src.upgrade upgrade "
            f"--app-root {self.config.app_root}"
        )

    # ------------------------------------------------------------------
    # 1. Check
    # ------------------------------------------------------------------
    def check(self, *, trigger: str = "manual") -> CheckResult:
        self._check_trigger = str(trigger or "manual")
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

        # 来源可信性：远端清单必须能验证 Ed25519 清单签名（或在首次引导时
        # 配置了固定清单哈希）；签名清单还要过防重放/防降级水位线。任何失败
        # 都**拒绝检查**，绝不降级成“已是最新”。
        try:
            self._establish_manifest_trust(
                load.manifest, latest, source=source
            )
        except UpgradeError as exc:
            result = CheckResult(
                status=CheckStatus.INVALID_MANIFEST,
                message=f"发布清单未通过可信校验，已拒绝检查更新：{exc}",
                current_version=current,
                latest_version=latest.version,
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
        elif self.config.allow_same_or_downgrade:
            # 允许重装/降级时，同版本或更低版本也是可安装目标：用户明确
            # 要求“重装同一版修损坏”或回退到旧版，这里不能提前判成
            # up_to_date，否则 --allow-same-or-downgrade 形同不存在。
            direction = (
                "低于当前版本"
                if is_newer(current, latest.version)
                else "与当前版本相同"
            )
            result = CheckResult(
                status=CheckStatus.UPDATE_AVAILABLE,
                message=f"目标版本 {latest.version} {direction} {current}，"
                "已按允许重装/降级放行",
                current_version=current,
                latest_version=latest.version,
                release=latest,
                source=source.description,
                checked_at=datetime.now(timezone.utc).isoformat(),
            )
        else:
            message = f"已是最新版本 {current}"
            if is_newer(current, latest.version):
                # 清单里的最高版本比当前还低：不是“已是最新”，而是用户
                # 手里版本更高。降级必须显式允许，不能悄悄装。
                message = (
                    f"当前版本 {current} 高于发布清单中的最高版本 {latest.version}；"
                    "如需降级请显式允许（CLI：--allow-same-or-downgrade）"
                )
            result = CheckResult(
                status=CheckStatus.UP_TO_DATE,
                message=message,
                current_version=current,
                latest_version=latest.version,
                release=latest,
                source=source.description,
                checked_at=datetime.now(timezone.utc).isoformat(),
            )
        self._finish_check(result)
        return result

    def _establish_manifest_trust(
        self,
        manifest: Dict[str, Any],
        latest: ReleaseInfo,
        *,
        source: ReleaseSource,
    ) -> None:
        """验证清单来源，并推进防重放/防降级水位线。

        * 远端源且未配置固定哈希：必须有清单签名且能验签（``require=True``）；
        * 远端源且配置了固定哈希：保留旧引导模式，签名可选（验不了就不装懂）；
        * 本地源：签名可选；若带了签名且配置了公钥，同样真验签并拒绝坏签名。

        只有"签名真验证通过"的清单才写水位线；固定哈希引导模式每次发布都要
        换哈希，不产生持久状态。
        """
        remote = bool(getattr(source, "is_remote", False))
        pinned = bool(getattr(source, "manifest_sha256", None))
        verified = verify_manifest_signature(
            manifest, self.config.public_key, require=remote and not pinned
        )
        if not verified:
            return

        sequence = manifest_sequence(manifest)
        if sequence is None:
            raise ManifestError(
                "清单签名有效但缺少 sequence（防重放序号）；"
                "请用 scripts/ops/sign_release_manifest.py 重新签名。"
            )
        digest = canonical_manifest_sha256(manifest)
        prior = self.manifest_trust_store.load()
        if prior is not None:
            if sequence < prior.sequence:
                raise ManifestError(
                    f"清单 sequence 回退（本次 {sequence}，已记录 {prior.sequence}）："
                    "疑似旧清单重放，已拒绝。"
                )
            if sequence == prior.sequence and digest != prior.manifest_sha256:
                raise ManifestError(
                    f"清单 sequence {sequence} 与已记录值相同但内容不一致"
                    "（疑似分叉/重放），已拒绝。"
                )
            if Version(latest.version) < Version(prior.version):
                raise ManifestError(
                    f"清单最高版本 {latest.version} 低于已记录的可信版本 "
                    f"{prior.version}：拒绝降级清单。"
                )

        highest = str(
            max(Version(latest.version), Version(prior.version))
            if prior is not None
            else Version(latest.version)
        )
        if prior is None or sequence > prior.sequence or digest != prior.manifest_sha256:
            self.manifest_trust_store.save(
                ManifestTrustState(
                    sequence=sequence,
                    version=highest,
                    manifest_sha256=digest,
                    updated_at=datetime.now(timezone.utc).isoformat(),
                )
            )

    def _assert_not_replayed_release(self, release: ReleaseInfo, *, phase: str) -> None:
        """远端源：低于水位线的发布包一律拒绝（防重放旧包）。"""
        source = self.config.source
        if source is None or not getattr(source, "is_remote", False):
            return
        prior = self.manifest_trust_store.load()
        if prior is None:
            return
        if Version(release.version) < Version(prior.version):
            raise SignatureError(
                f"拒绝重放旧版本发布包：{release.version} 低于已记录的可信版本 "
                f"{prior.version}（{phase}阶段）。"
            )

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
        state = {
            "schema": _CHECK_STATE_SCHEMA,
            "checked_at": result.checked_at,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "trigger": self._check_trigger,
            "status": result.status.value,
            "message": result.message,
            "current_version": result.current_version,
            "latest_version": result.latest_version,
            "source": result.source,
            "offline": bool(result.offline),
            "notified": False,
            "record_id": record.get("id"),
        }
        atomic_write_text(
            self.check_state_path,
            json.dumps(state, ensure_ascii=False, indent=2),
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
            self._assert_not_replayed_release(release, phase="下载")
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
                error=UPGRADE_FAILED_DOWNLOAD,
                technical_detail=str(exc),
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
    def apply(
        self,
        artifact_path: str | Path,
        release: ReleaseInfo,
        *,
        reuse_backup_id: Optional[str] = None,
    ) -> ApplyResult:
        with self._exclusive("升级操作", ApplyError):
            return self._apply(
                Path(artifact_path), release, reuse_backup_id=reuse_backup_id
            )

    def prepare_backup(self, release: ReleaseInfo) -> str:
        """应用**之前**先落地一份可回滚的升级前备份。

        编排器需要在动手改文件之前就把 ``backup_id`` 写进 journal（否则它
        被杀后恢复时不知道该回到哪一份备份）。这里复用 apply 内部的同一套
        预检 + 备份逻辑；随后把 id 传回给 :meth:`apply` 即可复用，不会重复备份。
        """
        with self._exclusive("升级前备份", ApplyError):
            try:
                current_version, _ = self.current_version()
            except VersionReadError as exc:
                raise ApplyError(f"无法确定当前版本：{exc}") from exc
            preflight = verify_data_paths(self._data_guard_paths())
            if not preflight.ok:
                details = "; ".join(
                    f"{issue.path}: {issue.detail}" for issue in preflight.issues
                )
                raise PreflightError(f"升级前数据校验未通过：{details}")
            key_tables = capture_table_inventory(self._data_guard_paths())
            backup_id = self._create_backup(
                current_version, release, preflight.to_dict(), key_tables=key_tables
            )
            self._read_backup(backup_id)
            return backup_id

    def _apply(
        self,
        artifact: Path,
        release: ReleaseInfo,
        *,
        reuse_backup_id: Optional[str] = None,
    ) -> ApplyResult:
        try:
            self._assert_not_replayed_release(release, phase="应用")
        except UpgradeError as exc:
            self._fail_before_mutation(release, str(exc))
            raise
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

        # Data schema guard. Opening newer data with older code does not fail
        # loudly: the old code simply ignores new tables/columns (a 0.1.0 build
        # on 0.1.1 data shows an empty session list). Refuse before touching
        # anything unless the user explicitly confirms the downgrade.
        try:
            target_schema = target_data_schema(
                release.data_schema_version,
                release_version=release.version,
                current_version=current_version,
            )
            data_schema_before = ensure_data_readable(
                self.config.data_dir,
                target_schema=target_schema,
                allow_downgrade=self.config.allow_data_downgrade,
            )
        except UpgradeError as exc:
            self._fail_before_mutation(release, str(exc), from_version=current_version)
            raise PreflightError(str(exc)) from exc

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
                error=UPGRADE_FAILED_NOT_APPLIED,
                technical_detail=details,
            )
            raise PreflightError(f"升级前数据校验未通过：{details}")

        # Record what each DB contained *before* the upgrade. The post-install
        # self-check compares against this; otherwise a release that silently
        # drops a table would look fine.
        key_tables = capture_table_inventory(self._data_guard_paths())

        # Front-end/back-end contract gate, before any backup or mutation.
        # Without it a package assembled from a tree that has no console build
        # output installs a new backend and leaves the old UI in place: the
        # service is healthy, the browser is stale, and API shape changes show
        # up as random 400s. Refusing here is the only safe option.
        console_gate: Dict[str, Any] = {}
        if (self.config.app_root / "console" / "index.html").is_file():
            try:
                with zipfile.ZipFile(artifact, "r") as contract_archive:
                    stamp = verify_package(contract_archive, release.version)
            except FrontendContractError as exc:
                self._fail_before_mutation(release, str(exc), from_version=current_version)
                raise PreflightError(str(exc)) from exc
            console_gate = {
                "applicable": True,
                "checked": True,
                "api_contract": stamp.get("api_contract"),
                "frontend_version": stamp.get("frontend_version"),
                "console_files": stamp.get("console_files"),
                "console_fingerprint": stamp.get("console_fingerprint"),
            }
        else:
            console_gate = {
                "applicable": False,
                "checked": False,
                "note": "安装根没有 console/（开发/便携布局），前后端契约门禁不适用",
            }

        # File-level gate: the whole-archive SHA256 only proves "these bytes
        # are the published bytes". It cannot prove the package contains every
        # file (a build that forgot src/gateway/chat.py still hashes
        # consistently). Compare against the per-file manifest and refuse on
        # any difference, before any backup or staging.
        file_manifest: Dict[str, Any] = {}
        try:
            with zipfile.ZipFile(artifact, "r") as manifest_archive:
                file_manifest = verify_file_manifest(
                    manifest_archive, release.files, source=f"升级包 {release.version}"
                )
        except (UpgradeError, zipfile.BadZipFile) as exc:
            self._fail_before_mutation(release, str(exc), from_version=current_version)
            raise PreflightError(str(exc)) from exc

        staging: Optional[Path] = None
        backup_id = ""
        mutation_started = False
        marker: Optional[Dict[str, Any]] = None
        pruned: Dict[str, Any] = {}
        try:
            with zipfile.ZipFile(artifact, "r") as archive:
                members = validate_release_zip(
                    archive,
                    install_roots=self.config.install_roots,
                    protected_prefixes=self.config.protected_paths,
                    max_uncompressed_bytes=self.config.max_uncompressed_bytes,
                )
                # Stop here - before any backup, staging or install - when the
                # disk cannot hold the new tree plus both backups.
                self._check_disk_space(
                    sum(int(info.file_size) for _, info in members)
                )
                staging = Path(
                    tempfile.mkdtemp(prefix=f"liuhao-upgrade-stage-{os.getpid()}-")
                )
                try:
                    installed = extract_zip_members(archive, members, staging)
                except OSError as exc:
                    if getattr(exc, "errno", None) == errno.ENOSPC:
                        raise self._disk_error(exc, "解压发布包到临时目录时") from exc
                    raise
            staged_version = read_package_version(staging)
            if Version(staged_version) != Version(release.version):
                raise ApplyError(
                    f"发布包版本 {staged_version} 与清单版本 {release.version} 不一致"
                )

            if reuse_backup_id:
                # 编排器在停服之前就用 prepare_backup() 落好盘并把 id 写进
                # journal；这里只校验它真的可读，绝不重复备份。
                self._set_status(
                    Phase.BACKING_UP,
                    "备份中：复用已落盘的升级前备份",
                    target_version=release.version,
                    backup_id=reuse_backup_id,
                )
                backup_id = reuse_backup_id
                self._read_backup(backup_id)
            else:
                self._set_status(
                    Phase.BACKING_UP,
                    "备份中：正在分别备份代码与数据",
                    target_version=release.version,
                    backup_id="",
                )
                try:
                    backup_id = self._create_backup(
                        current_version,
                        release,
                        preflight.to_dict(),
                        key_tables=key_tables,
                    )
                except OSError as exc:
                    if getattr(exc, "errno", None) == errno.ENOSPC:
                        raise self._disk_error(exc, "写入升级备份时") from exc
                    raise
                # Re-read the metadata immediately: a backup we cannot parse is
                # not a backup we can roll back to, and this must fail before
                # mutation.
                self._read_backup(backup_id)

            # Durable "we are about to touch real files" marker. If this
            # process dies (or is killed) now, the next start sees it and
            # resolves the interrupted upgrade instead of showing "unknown".
            marker = self.ledger.record(
                kind="apply",
                status="in_progress",
                from_version=current_version,
                to_version=release.version,
                reason="",
                backup_id=backup_id,
                artifact_path=str(artifact),
                detail={"mutation_started": False, "phase": "applying"},
            )
            self._set_status(
                Phase.APPLYING,
                f"应用中：正在安装 {len(installed)} 个文件",
                target_version=release.version,
                backup_id=backup_id,
                record_id=marker.get("id"),
            )
            mutation_started = True
            with zipfile.ZipFile(artifact, "r") as archive:
                extract_zip_members(archive, members, self.config.app_root)

            # Overlay extraction never deletes anything: files dropped from the
            # new release (and old hashed console assets) would stay behind and
            # be served alongside the new code. Remove what the new manifest
            # does not declare, then verify the tree again.
            pruned = self._prune_stale_files([rel for rel, _ in members])

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

            self._set_status(
                Phase.SELF_CHECKING,
                "自检中：正在临时端口真实启动服务，并检查数据库与关键表",
                target_version=release.version,
                backup_id=backup_id,
                record_id=marker.get("id"),
            )
            self_check = run_self_check(
                self.config.app_root,
                self._data_guard_paths(),
                expected_tables=key_tables,
                config=self.config.self_check,
            )
            self_check_reason = "; ".join(
                f"{issue.kind}:{issue.path}:{issue.detail}" for issue in self_check.issues
            )
            self.ledger.record(
                kind="selfcheck",
                status="passed" if self_check.ok else "failed",
                from_version=current_version,
                to_version=release.version,
                reason=self_check_reason if not self_check.ok else "",
                backup_id=backup_id,
                detail=self_check.to_dict(),
            )
            if not self_check.ok:
                log_path = self._write_selfcheck_log(self_check)
                suffix = f"；启动日志尾部已保存到 {log_path}" if log_path else ""
                raise SelfCheckError(
                    "升级后自检未通过：" + (self_check_reason or "未知原因") + suffix
                )
            # Do not leave a stale failure log that contradicts this success.
            (self.config.state_dir / "last-selfcheck.log").unlink(missing_ok=True)

            # 记录本版实际安装的文件清单（含 fsync）。下一次升级只删「本清单
            # 有、新版没有」的路径；用户自建文件不在清单里，永远不会被删。
            installed_manifest = self._record_installed_files(release.version, installed)
            if installed_manifest is None:
                logger.warning(
                    "本版安装清单未落盘：下次升级将按白名单清理陈旧配置，用户文件仍不会被删"
                )

            # Mark the data directory with the schema this release writes. The
            # marker is monotonic: a confirmed downgrade never lowers it, so a
            # later downgrade attempt still sees the highest version written.
            try:
                data_schema_after = stamp_data_schema(
                    self.config.data_dir,
                    app_version=release.version,
                    schema_version=max(
                        target_schema,
                        data_schema_before.version if data_schema_before else 0,
                    ),
                )
            except OSError as exc:
                raise ApplyError(f"写入数据 schema 标记失败：{exc}") from exc

            record = self.ledger.update(
                marker["id"],
                status="completed",
                reason=signature_note,
                backup_id=backup_id,
                detail={
                    "backup_dir": str(self.config.backup_root / backup_id),
                    "installed_files": len(installed),
                    "signature_verified": signature_verified,
                    "signature_note": signature_note,
                    "preflight": preflight.to_dict(),
                    "key_tables": key_tables,
                    "frontend_contract": console_gate,
                    "file_manifest": file_manifest,
                    "pruned_files": pruned,
                    "installed_files_manifest": installed_manifest,
                    "data_schema": {
                        "supported": DATA_SCHEMA_VERSION,
                        "target": target_schema,
                        "before": (
                            data_schema_before.to_dict() if data_schema_before else None
                        ),
                        "after": data_schema_after.to_dict(),
                    },
                    "selfcheck": self_check.to_dict(),
                },
            ) or marker
            done_message = (
                f"完成：已安装 {current_version} → {release.version}，需重启后生效"
            )
            if pruned.get("kept_count"):
                done_message += (
                    f"；保留 {pruned['kept_count']} 个不在新包清单里的文件（清单见升级记录）"
                )
            self._set_status(
                Phase.COMPLETED,
                done_message,
                target_version=release.version,
                backup_id=backup_id,
                record_id=record.get("id"),
                detail={"pruned_files": pruned, "installed_files_manifest": installed_manifest},
            )
            return ApplyResult(
                record_id=record["id"],
                backup_id=backup_id,
                from_version=current_version,
                to_version=release.version,
                installed_files=len(installed),
                data_checked_files=preflight.files_checked,
                self_check_passed=bool(self_check.ok),
                self_check_issues=len(self_check.issues),
                pruned_files=tuple(pruned.get("deleted") or ()),
                kept_stale_files=tuple(pruned.get("kept") or ()),
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
                    record_id=(marker or {}).get("id"),
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
            detail = {
                "mutation_started": mutation_started,
                "rollback_ok": rollback_ok,
                "rollback_error": rollback_error,
                "signature_verified": signature_verified,
                "signature_note": signature_note,
                "frontend_contract": console_gate,
                "file_manifest": file_manifest,
                "pruned_files": pruned,
            }
            if marker is not None:
                record = self.ledger.update(
                    marker["id"],
                    status="failed_rolled_back" if rollback_ok else "failed",
                    reason=reason,
                    backup_id=backup_id or None,
                    detail=detail,
                ) or marker
            else:
                record = self.ledger.record(
                    kind="apply",
                    status="failed_rolled_back" if rollback_ok else "failed",
                    from_version=current_version,
                    to_version=release.version,
                    reason=reason,
                    backup_id=backup_id or None,
                    artifact_path=str(artifact),
                    detail=detail,
                )
            if rollback_ok:
                self._set_status(
                    Phase.ROLLED_BACK,
                    f"已回滚：升级失败，已恢复到 {current_version}（原因：{exc}）",
                    target_version=release.version,
                    backup_id=backup_id,
                    error=UPGRADE_FAILED_ROLLED_BACK,
                    technical_detail=str(exc),
                    record_id=record.get("id"),
                )
                raise ApplyError(f"应用失败，已自动回滚到 {current_version}：{exc}") from exc
            if mutation_started:
                self._set_status(
                    Phase.FAILED,
                    f"失败：应用失败且回滚失败（{rollback_error}）",
                    target_version=release.version,
                    backup_id=backup_id,
                    error=UPGRADE_FAILED_NOT_ROLLED_BACK,
                    technical_detail=reason,
                    record_id=record.get("id"),
                )
                raise ApplyError(
                    f"应用失败且回滚失败，当前状态可能不一致：{exc}；回滚错误：{rollback_error}"
                ) from exc
            message = str(exc)
            self._set_status(
                Phase.FAILED,
                f"失败：应用未开始（{message}）",
                target_version=release.version,
                error=UPGRADE_FAILED_NOT_APPLIED,
                technical_detail=message,
                record_id=record.get("id"),
            )
            raise ApplyError(f"应用失败（未修改任何文件）：{message}") from exc
        finally:
            if staging is not None:
                shutil.rmtree(staging, ignore_errors=True)

    def _prune_stale_files(self, keep: Sequence[str]) -> Dict[str, Any]:
        """清理旧版本遗留在安装根里的文件，并报告"留下了什么"。

        删除判据按优先级：

        1. ``console/`` 严格清理——纯构建产物，新版清单里没有的一律删，否则
           旧哈希前端资源会让自检判定"前后端不是同一次构建"并回滚；
        2. 有上一版安装清单（``<data>/upgrade-state/installed-files.json``）时，
           只删「上一版清单有、新版清单没有」的路径。用户自建文件从未进过我方
           清单，因此任何情况下都不会被删；
        3. 首次升级（还没有上一版清单）退化为白名单：只删
           :data:`_STALE_FALLBACK_FILES` 里那批安装版永不使用的陈旧配置，
           不再沿用"父目录被新包覆盖就删"的粗判据。

        没有被删除的旧文件不做静默保留：全部收进 ``kept`` 返回给调用方，写进
        升级记录与阶段详情。
        """
        keep_set = {str(item).replace("\\", "/").strip("/") for item in keep}
        previous = self._load_installed_files()
        mode = "manifest" if previous is not None else "whitelist"

        deleted: List[str] = []
        failed: List[str] = []
        kept: List[str] = []
        for root_name in self.config.install_roots:
            root_rel = str(root_name).replace("\\", "/").strip("/")
            target = self.config.app_root / root_name
            if target.is_file():
                if root_rel not in keep_set:
                    self._classify_stale(
                        root_rel, previous, deleted, failed, kept, strict=False, path=target
                    )
                continue
            if not target.is_dir():
                continue
            strict = root_rel in _STRICT_PRUNE_ROOTS
            for path in target.rglob("*"):
                if not path.is_file():
                    continue
                rel = path.relative_to(self.config.app_root).as_posix()
                if rel in keep_set:
                    continue
                self._classify_stale(
                    rel, previous, deleted, failed, kept, strict=strict, path=path
                )
        if deleted:
            logger.info(
                "升级后清理了 %d 个陈旧文件：%s", len(deleted), ", ".join(deleted[:10])
            )
        if kept:
            logger.info(
                "升级保留了 %d 个不在新包清单里的文件（未删除）：%s",
                len(kept),
                ", ".join(sorted(kept)[:10]),
            )
        return {
            "mode": mode,
            "deleted": sorted(deleted),
            "failed": sorted(failed),
            "kept": sorted(kept),
            "kept_count": len(kept),
        }

    def _classify_stale(
        self,
        rel: str,
        previous: Optional[set[str]],
        deleted: List[str],
        failed: List[str],
        kept: List[str],
        *,
        strict: bool,
        path: Path,
    ) -> None:
        """按当前判据决定一个旧文件：删掉，还是保留并记录。"""
        if not self._stale_candidate(rel):
            # 受保护路径、运行期生成文件、密钥与字节码缓存：既不删也不报告。
            return
        if strict:
            self._delete_stale(path, rel, deleted, failed)
            return
        if previous is not None:
            removable = rel in previous
        else:
            removable = rel in _STALE_FALLBACK_FILES
        if removable:
            self._delete_stale(path, rel, deleted, failed)
        else:
            kept.append(rel)

    def _installed_files_path(self) -> Path:
        return self.config.state_dir / INSTALLED_FILES_FILENAME

    def _load_installed_files(self) -> Optional[set[str]]:
        """读上一版安装清单；不存在 / 损坏 / 格式不符时返回 ``None``。

        返回 ``None`` 表示走白名单退路，绝不把"读不到"当成"清单为空"——否则
        会退化成"清单里没有就删"。
        """
        path = self._installed_files_path()
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as exc:
            logger.warning("读取上一版安装清单失败，本次按白名单清理：%s（%s）", path, exc)
            return None
        try:
            document = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            logger.warning("上一版安装清单无法解析，本次按白名单清理：%s（%s）", path, exc)
            return None
        if not isinstance(document, dict) or document.get("schema") != INSTALLED_FILES_SCHEMA:
            logger.warning("上一版安装清单格式不符，本次按白名单清理：%s", path)
            return None
        raw_files = document.get("files")
        if not isinstance(raw_files, list):
            logger.warning("上一版安装清单缺少 files 列表，本次按白名单清理：%s", path)
            return None
        files = {
            str(item).replace("\\", "/").strip("/")
            for item in raw_files
            if isinstance(item, str) and item.strip()
        }
        return files or None

    def _record_installed_files(
        self, version: str, files: Sequence[str]
    ) -> Optional[str]:
        """把本版实际安装的文件清单原子落盘（含 fsync）。

        写失败不让已成功的升级失败：下一次升级会退化为白名单清理，仍然不会
        删除用户文件；失败只记日志并回传 ``None`` 供升级记录标注。
        """
        payload = {
            "schema": INSTALLED_FILES_SCHEMA,
            "version": version,
            "installed_at": datetime.now(timezone.utc).isoformat(),
            "files": sorted(
                {str(item).replace("\\", "/").strip("/") for item in files if str(item).strip()}
            ),
        }
        path = self._installed_files_path()
        try:
            atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))
        except OSError as exc:
            logger.warning("写入本版安装清单失败（下次升级将按白名单清理）：%s（%s）", path, exc)
            return None
        return str(path)

    def _stale_candidate(self, rel: str) -> bool:
        if is_protected(rel, self.config.protected_paths):
            return False
        if rel in _STALE_PRESERVE:
            return False
        name = rel.rsplit("/", 1)[-1]
        if name.startswith("unins") or name.startswith("."):
            return False
        if "__pycache__" in rel.split("/"):
            return False
        if name.lower().endswith(_STALE_SKIP_SUFFIX):
            return False
        return True

    @staticmethod
    def _delete_stale(
        path: Path, rel: str, deleted: List[str], failed: List[str]
    ) -> None:
        try:
            path.unlink()
            deleted.append(rel)
        except OSError as exc:
            failed.append(f"{rel}: {exc}")
            logger.warning("升级后清理陈旧文件失败：%s（%s）", rel, exc)

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
            error=UPGRADE_FAILED_NOT_APPLIED,
            technical_detail=message,
        )

    def _write_selfcheck_log(self, report: Any) -> Optional[Path]:
        """Persist the redacted startup tail so the user can investigate."""
        startup = getattr(report, "startup", None)
        tail = getattr(startup, "log_tail", "") if startup else ""
        if not tail:
            return None
        path = self.config.state_dir / "last-selfcheck.log"
        header = (
            "# 升级后自检启动日志（脱敏后的尾部）\n"
            f"# checked_at: {getattr(report, 'checked_at', '')}\n"
            f"# failure_kind: {getattr(startup, 'failure_kind', '')}\n"
            f"# reason: {getattr(startup, 'reason', '')}\n"
            f"# port: {getattr(startup, 'port', 0)}\n"
            "# 说明：不包含用户数据目录内容；密钥/令牌已替换为 <已脱敏>。\n\n"
        )
        atomic_write_text(path, header + tail + "\n")
        return path

    # ------------------------------------------------------------------
    # Backup / restore internals
    # ------------------------------------------------------------------
    def _create_backup(
        self,
        from_version: str,
        release: ReleaseInfo,
        preflight_detail: Dict[str, Any],
        key_tables: Optional[Dict[str, List[str]]] = None,
    ) -> str:
        if not self.config.backup_root_available:
            raise DiskSpaceError(
                self.config.backup_root_error
                or f"备份盘不可写，备份未启用，升级已被拒绝。备份根：{self.config.backup_root}"
            )
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
            # 除运行期状态目录外，整个 upgrade-backups 父目录也必须排除：
            # 历史备份（尤其是失败/回滚后留下的 rollback-trash）本身就在数据
            # 目录里，若被再次遍历，会形成嵌套备份，并可能在遍历过程中读到
            # 已被回滚清理删除的文件，导致之后每次 apply 都失败。
            exclude_dirs=(self.config.state_dir, self.config.backup_root),
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
            "key_tables": key_tables or {},
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
        # Verify every backup artefact *before* moving any real file: a
        # corrupted backup must be refused, not "restored" halfway.
        verify_backup(backup_dir)
        expected_sha = str(meta.get("code_zip_sha256", "")).lower()
        zip_path = backup_dir / "code.zip"
        if expected_sha and _sha256_file(zip_path) != expected_sha:
            raise BackupCorrupt(
                "代码备份包 SHA256 与备份元数据不符（备份不可用）"
            )
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
        with self._exclusive("回滚操作", RollbackError):
            return self._rollback(backup_id=backup_id, record_id=record_id)

    def _rollback(
        self,
        *,
        backup_id: Optional[str] = None,
        record_id: Optional[str] = None,
    ) -> RollbackResult:
        self._set_status(Phase.ROLLING_BACK, "回滚中：正在定位升级前备份", backup_id="")
        current = ""
        target_version = ""
        source_version = ""
        try:
            # Resolving the backup happens inside the try: "no rollback record"
            # is a failure that must leave the status at 失败, not stuck at
            # 回滚中 forever.
            backup_id = self._resolve_backup_id(backup_id, record_id)
            hint = self._apply_record_for_backup(backup_id)
            target_version = str((hint or {}).get("from_version") or "")
            meta = self._read_backup(backup_id)
            source_version = str(meta.get("to_version", ""))
            target_version = str(meta.get("from_version", "") or target_version)
            current, _ = self.current_version()
            # Record the resolved backup id before touching files, so a kill
            # during the restore itself is recoverable on next start.
            self._set_status(
                Phase.ROLLING_BACK,
                f"回滚中：正在恢复 {target_version or '升级前版本'}",
                target_version=target_version or None,
                backup_id=backup_id,
                record_id="",
            )
            self._restore_backup(backup_id)
            restored_version, _ = self.current_version()
            if target_version and Version(restored_version) != Version(target_version):
                raise RollbackError(
                    f"回滚后版本为 {restored_version}，预期 {target_version}"
                )
            record = self.ledger.record(
                kind="rollback",
                status="rolled_back",
                from_version=source_version or current,
                to_version=target_version or restored_version,
                backup_id=backup_id,
                detail={
                    "backup_dir": str(self.config.backup_root / backup_id),
                    "current_before_rollback": current,
                    "restored_version": restored_version,
                },
            )
            self._set_status(
                Phase.ROLLED_BACK,
                f"已回滚：{source_version or current} → {target_version or restored_version}",
                target_version=target_version or restored_version,
                backup_id=backup_id,
                record_id=record.get("id"),
            )
            return RollbackResult(
                record_id=record["id"],
                backup_id=backup_id,
                from_version=source_version or current,
                to_version=target_version or restored_version,
                restored_files=int(meta.get("code_file_count", 0))
                + int(meta.get("data_file_count", 0)),
            )
        except Exception as exc:
            current = current or self._safe_current_version()
            old = target_version or "旧版本"
            upgrade_command = (
                f"python -m src.upgrade upgrade --app-root {self.config.app_root} "
                "--release-dir <发布目录> --allow-same-or-downgrade"
            )
            if isinstance(exc, BackupNotFound):
                message = (
                    f"无法回滚：{old} 的升级前备份已不存在（备份可能已被清理），"
                    f"无法用它回滚。当前版本 {current or '未知'} 仍可正常使用，"
                    f"未做任何修改。需要回到 {old}，需要重新下载 {old} 版本安装包，"
                    f"放到发布目录后执行：{upgrade_command}"
                )
            elif isinstance(exc, BackupCorrupt):
                message = (
                    f"无法回滚：{old} 的备份不可用（{exc}）。"
                    f"当前版本 {current or '未知'} 仍完整，未做任何修改，没有假装回滚成功。"
                    f"可以换一个备份：python -m src.upgrade snapshots "
                    f"--app-root {self.config.app_root} --limit 20；"
                    f"或重新下载 {old} 版本安装包后执行：{upgrade_command}"
                )
            else:
                message = f"失败：回滚失败（{exc}）"
            record = self.ledger.record(
                kind="rollback",
                status="failed",
                from_version=source_version or current or None,
                to_version=target_version or None,
                backup_id=backup_id,
                reason=message,
            )
            self._set_status(
                Phase.FAILED,
                message,
                backup_id=backup_id,
                error=ROLLBACK_FAILED,
                technical_detail=message,
                record_id=record.get("id"),
            )
            if isinstance(exc, RollbackError):
                if str(exc) == message:
                    raise
                raise RollbackError(message) from exc
            raise RollbackError(message) from exc

    def _safe_current_version(self) -> str:
        try:
            return read_current_version(self.config.app_root)[0]
        except VersionReadError:
            return ""

    def _apply_record_for_backup(self, backup_id: str) -> Optional[Dict[str, Any]]:
        for item in self.ledger.list_records(kind="apply", limit=200):
            if item.get("backup_id") == backup_id and item.get("status") in (
                "completed",
                "failed_rolled_back",
                "interrupted_rolled_back",
            ):
                return item
        return None
