"""每日自动备份（6-7 备份自动化）。

每天本地时间 03:00 由本地排程器调用一次（``src/tasks/local_handlers.py``
的 ``liuhao.backup.daily`` 任务），备份范围是本机数据目录：

* ``config/``（配置）、``data/``（记忆与会话数据）、数据目录本身
  （用户数据）、``knowledge/``（知识库，存在时才纳入）；
* 保留最近 7 天（条数与天数双重上限，先到先删）；
* 归档经 AES-256-GCM 加密（先 gzip 再加密，与 ``BackupManager`` 一致）。

加密思路（写进 ``docs/backup-and-restore.md`` 的同一份说明）：

* 归档密钥是每台机器首次备份时随机生成的 32 字节，存在数据目录下的
  ``backup.key`` 里，文件本身经 ``wrap_machine_bound`` 包过 ——
  Windows 上是 DPAPI（绑定本机 + 本用户），Linux 上是
  ``LIUHAO-RAW-1:`` base64（仅开发环境，约等于明文，别当真凭据用）。
* 密钥文件与 ``backups/`` 目录本身被排除在备份源之外：密钥不进包，
  备份也不套备份。
* 换机器打不开旧归档：DPAPI 密钥只认原机原用户，``backup.key`` 拷过去
  也解不开；模型 Key 跟机器主密钥绑定，同样要重填。跨机器迁移走
  口令式账号导出（``src/backup/account_backup.py``），不要直接拷数据目录。
* 同机恢复时归档内的 DPAPI 密文原样有效：备份前机器绑定材料先被还原
  成原始材料入包（``extract_machine_bound_material``），恢复时按标记
  用本机 DPAPI 重包（``restore_machine_bound_material``）。
* 备份与数据在同一块磁盘上：磁盘坏了会一起丢，这不是异地备份；重要
  数据请另外把 ``backups/auto/`` 拷到别的盘或 U 盘。
"""

from __future__ import annotations

import logging
import os
import secrets
from datetime import datetime
from pathlib import Path

from src.backup.backup_manager import (
    BackupManager,
    BackupMetadata,
    BackupSource,
    BackupStatus,
    RetentionPolicy,
    RetentionPolicyType,
)
from src.security.encryption import (
    MachineBoundKeyError,
    unwrap_machine_bound,
    wrap_machine_bound,
)

logger = logging.getLogger(__name__)

#: 归档密钥文件名（在数据目录顶层，不进备份包）。
BACKUP_KEY_FILENAME = "backup.key"
#: 归档密钥的 DPAPI 熵：只用于备份密钥，与模型/会话密钥的熵互不通用。
BACKUP_KEY_DPAPI_PURPOSE = b"liuhao-backup-v1"
#: 自动备份每天跑一次的本地时间（小时）。
AUTO_BACKUP_HOUR = 3
#: 留存：最近 7 天（条数与天数双重上限）。
AUTO_BACKUP_RETENTION_DAYS = 7
AUTO_BACKUP_MAX_BACKUPS = 7
#: 自动备份归档的存放位置（在数据目录下）。
AUTO_BACKUP_SUBDIR = "auto"
#: 恢复前快照的存放位置（在数据目录下，与自动备份分开：恢复时找
#: “最近一份自动备份”不会把它当成备份，自动备份的 7 天留存也不会
#: 把它清掉；快照只留最近 3 份，够回到恢复前的样子就行）。
PRE_RESTORE_SUBDIR = "pre-restore"
#: 恢复前快照留存：最近 3 份（条数与天数双重上限，先到先删）。
PRE_RESTORE_MAX_BACKUPS = 3
PRE_RESTORE_RETENTION_DAYS = 30

# 备份源里顶层要跳过的名字：备份目录本身与归档密钥文件。
AUTO_BACKUP_EXCLUDE_NAMES = frozenset({"backups", BACKUP_KEY_FILENAME})


def format_backup_time(timestamp: float) -> str:
    """把备份的 epoch 时间戳翻成本机本地时间（``YYYY-MM-DD HH:MM``）。

    前端只认字符串时间戳：直接把 float epoch 交给它会显示成“还没备份过”。
    转不成时返回空串，不编一个时间。
    """
    try:
        return datetime.fromtimestamp(float(timestamp)).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def auto_backup_source_paths(data_dir: Path) -> dict:
    """每日自动备份与恢复共用的源映射：恢复必须写回当初备走的位置。

    以前恢复端只拿 ``backup_dir`` 建 manager，不带源映射也不带密钥，
    导致恢复要么解不开归档、要么一个文件都写不回去还报成功。
    """
    source_paths = {
        BackupSource.CONFIG: data_dir / "config",
        BackupSource.MEMORY_DATA: data_dir / "data",
        BackupSource.USER_DATA: data_dir,
    }
    knowledge_dir = data_dir / "knowledge"
    if knowledge_dir.exists():
        source_paths[BackupSource.KNOWLEDGE_BASE] = knowledge_dir
    return source_paths


def _read_backup_key(data_dir: Path) -> bytes:
    """读出现有归档密钥；缺失或解不开时抛中文 ``ValueError``（不新建）。

    恢复路只能读不能建：密钥不在了，旧归档本来就打不开，新建一把
    只会留下一个解不开任何旧备份的空密钥文件。
    """
    key_path = data_dir / BACKUP_KEY_FILENAME
    if not key_path.exists():
        raise ValueError(
            "备份密钥不在了（数据目录里没有 backup.key），旧备份打不开；"
            "恢复前也没法给当前数据备一份，所以这次没敢动手。"
        )
    try:
        plain, _mode = unwrap_machine_bound(
            key_path.read_bytes(), purpose=BACKUP_KEY_DPAPI_PURPOSE
        )
    except MachineBoundKeyError as exc:
        raise ValueError(
            f"备份密钥 {key_path} 无法在本机解开（可能换了机器或用户），"
            "旧归档无法再打开；删掉该文件后会生成新密钥，但旧备份随之作废。"
            "跨机器迁移请走口令式账号导出，不要直接拷数据目录。"
        ) from exc
    if len(plain) != 32:
        raise ValueError(
            f"备份密钥 {key_path} 长度不对（{len(plain)} 字节，应为 32 字节），"
            "请删掉该文件后重新备份，会生成新密钥（旧备份随之作废）。"
        )
    return plain


def load_backup_key(data_dir: Path | str) -> bytes:
    """恢复/校验用的归档密钥：只读现有的，缺失或解不开就抛人话。"""
    return _read_backup_key(Path(data_dir))


def _load_or_create_backup_key(data_dir: Path) -> bytes:
    """读出归档密钥；首次备份时随机生成 32 字节并经机器绑定后存盘。

    密钥文件损坏、长度不对、或本机解不开时抛 ``ValueError``（中文），
    本次备份直接失败，不写无密钥的明文归档。
    """
    key_path = data_dir / BACKUP_KEY_FILENAME
    if key_path.exists():
        return _read_backup_key(data_dir)

    key = secrets.token_bytes(32)
    wrapped = wrap_machine_bound(key, purpose=BACKUP_KEY_DPAPI_PURPOSE)
    data_dir.mkdir(parents=True, exist_ok=True)
    tmp = key_path.with_name(key_path.name + ".tmp")
    try:
        tmp.write_bytes(wrapped)
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass  # Windows 上没有 Unix 权限位，DPAPI 本身就是保护
        os.replace(tmp, key_path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
    logger.info("已生成新的备份密钥：%s", key_path)
    return key


def run_daily_auto_backup(
    data_dir: Path | str, *, description: str = "每日自动备份（本地时间 03:00）"
) -> BackupMetadata:
    """跑一次每日自动备份，返回备份元数据。

    只写 ``<数据目录>/backups/auto/``，不动其它位置；失败时抛异常，
    由排程器记死信（不静默吞掉）。
    """
    data_dir = Path(data_dir)
    key = _load_or_create_backup_key(data_dir)

    manager = BackupManager(
        backup_dir=data_dir / "backups" / AUTO_BACKUP_SUBDIR,
        source_paths=auto_backup_source_paths(data_dir),
        retention_policy=RetentionPolicy(
            policy_type=RetentionPolicyType.BOTH,
            max_backups=AUTO_BACKUP_MAX_BACKUPS,
            max_age_days=AUTO_BACKUP_RETENTION_DAYS,
        ),
        encryption_enabled=True,
        encryption_key=key,
        exclude_names=AUTO_BACKUP_EXCLUDE_NAMES,
    )
    meta = manager.create_backup(description=description)
    logger.info(
        "每日自动备份完成：%s（%s，%s）",
        meta.backup_id,
        meta.size_human,
        meta.file_path,
    )
    return meta


def run_pre_restore_snapshot(
    data_dir: Path | str, *, description: str = "恢复前快照（自动）"
) -> BackupMetadata:
    """恢复前给当前数据备一份，写进 ``<数据目录>/backups/pre-restore/``。

    恢复是大事：动手前先有这份快照，恢复失败或恢复完发现不对，都能
    用它回到恢复前的样子。快照与自动备份同款加密、同款源；密钥用新建
    也行（快照是新写的归档，不靠旧密钥解），只留最近 3 份；写不出来
    就抛异常，调用方必须先停手、不能硬恢复。
    """
    data_dir = Path(data_dir)
    key = _load_or_create_backup_key(data_dir)

    manager = BackupManager(
        backup_dir=data_dir / "backups" / PRE_RESTORE_SUBDIR,
        source_paths=auto_backup_source_paths(data_dir),
        retention_policy=RetentionPolicy(
            policy_type=RetentionPolicyType.BOTH,
            max_backups=PRE_RESTORE_MAX_BACKUPS,
            max_age_days=PRE_RESTORE_RETENTION_DAYS,
        ),
        encryption_enabled=True,
        encryption_key=key,
        exclude_names=AUTO_BACKUP_EXCLUDE_NAMES,
    )
    meta = manager.create_backup(description=description)
    logger.info(
        "恢复前快照完成：%s（%s，%s）",
        meta.backup_id,
        meta.size_human,
        meta.file_path,
    )
    return meta


def _backup_item(meta: BackupMetadata) -> dict:
    """备份列表里一项的真实字段：时间给 epoch 与本地时间文本两份，
    前端按文本显示，epoch 留给以后排序/判断用，不编假时间。"""
    return {
        "backup_id": meta.backup_id,
        "timestamp": meta.timestamp,
        "timestamp_text": format_backup_time(meta.timestamp),
        "size_bytes": meta.size_bytes,
        "encrypted": meta.encrypted,
        "description": meta.description,
        "status": meta.status.value,
    }


def list_local_backups(data_dir: Path | str) -> dict:
    """本机备份文件清单：自动备份 + 恢复前快照，各按时间倒序。

    只列已完成/已校验的；进行中或失败的残次不列出来当“能恢复的备份”。
    校验会把内存态 status 改成 VERIFIED，重启后 sidecar 仍是 COMPLETED，
    两者都算“能恢复”。
    """
    data_dir = Path(data_dir)
    usable = (BackupStatus.COMPLETED, BackupStatus.VERIFIED)
    auto = BackupManager(backup_dir=data_dir / "backups" / AUTO_BACKUP_SUBDIR)
    snapshots = BackupManager(backup_dir=data_dir / "backups" / PRE_RESTORE_SUBDIR)
    return {
        "backups": [
            _backup_item(meta)
            for meta in auto.list_backups()
            if meta.status in usable
        ],
        "snapshots": [
            _backup_item(meta)
            for meta in snapshots.list_backups()
            if meta.status in usable
        ],
    }


def auto_backup_status(data_dir: Path | str) -> dict:
    """自动备份的当前状态（给诊断/文档用，不含任何密钥材料）。

    返回最近一次备份的 ID、时间、大小与是否加密；从来没备过时
    ``latest`` 为 ``None``，不编一个假的。
    """
    data_dir = Path(data_dir)
    manager = BackupManager(backup_dir=data_dir / "backups" / AUTO_BACKUP_SUBDIR)
    latest = manager.latest_backup()
    return {
        "backup_dir": str(data_dir / "backups" / AUTO_BACKUP_SUBDIR),
        "key_exists": (data_dir / BACKUP_KEY_FILENAME).exists(),
        "backup_count": len(manager.list_backups()),
        "latest": None
        if latest is None
        else {
            "backup_id": latest.backup_id,
            "timestamp": latest.timestamp,
            "timestamp_text": format_backup_time(latest.timestamp),
            "size_bytes": latest.size_bytes,
            "encrypted": latest.encrypted,
            "file_path": latest.file_path,
        },
    }
