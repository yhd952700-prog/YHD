"""队列类存储的统一落点与一次性迁移（tasks.db / tasks.journal / reminders.db / outbox）。

为什么单独一个模块
==================
这五处此前各自拼路径，且默认写 ``~/.liuhao/queue``：只设 ``LIUHAO_DATA_DIR`` 时
任务队列、幂等 journal、提醒库、出站队列仍然落在用户主目录。改路径不难，难的是
**迁移不能丢数据**：新位置没有、旧位置有时，直接以空库启动会让正在排队/正在跑的
任务消失。所以这里把「解析 + 迁移 + 校验」收成一个入口，五处调用同一个函数。

解析顺序（调用时求值，不要在导入时求值）
--------------------------------------
1. 该存储的显式环境变量（``LIUHAO_TASK_DB`` 等）；
2. ``LIUHAO_DATA_DIR`` → ``<data>/queue/<filename>``；
3. ``LIUHAO_WORKSPACE_ROOT`` → ``<workspace>/queue/<filename>``（安装版既有契约）；
4. 历史默认 ``~/.liuhao/queue/<filename>``。

``LIUHAO_DATA_DIR`` 排在 workspace 之前：数据根是这套隔离的单一根，安装版启动器
本来就把 workspace 放在 ``<data>/workspace`` 下，所以两者都设时队列落在
``<data>/queue``；只设 workspace（老部署）时行为逐字不变。

迁移规则（不能丢数据，也不能两套并存）
------------------------------------
* 新位置已有 → 用新位置，**旧文件保留不删**（两边都有以新为准）；
* 新位置缺失、旧位置存在 → 在**跨进程排他锁**内做双重检查 → 复制到带 pid/随机
  后缀的临时文件 → 校验 → fsync → ``os.replace`` 原子落位。
  SQLite 用 backup API（能把 WAL 里尚未 checkpoint 的行一起带走），校验表集合与
  逐表行数；文本文件校验逐字节内容。校验通过后旧文件仍然保留。
* 迁移失败（含校验不一致）→ 记 ``ERROR`` 日志并**继续使用旧位置**，绝不以空库
  启动，也不留半截新文件（临时文件与 sidecar 一并清掉）。

并发安全（为什么必须加锁）
------------------------
只做 ``target.exists()`` 检查属于 check-then-act：两个进程同时启动时会各迁一次，
共用同一个临时名互相删文件，甚至 A 已落位后 B 的 rename 失败退回旧库——一个进程
写新库、一个写旧库（split-brain）。所以整个临界区由
``src/common/atomic_write.exclusive_file_lock`` 串行化，锁文件
``<target>.migrate.lock`` 与目标路径绑定、跨进程可见；进锁后**重新检查**
``target.exists()``（等锁期间别人可能已经迁完）。锁由操作系统在进程退出/被强杀时
自动释放（``msvcrt`` / ``fcntl`` 均如此），不需要人工删锁文件；只有持锁进程一直
活着且超过 ``MIGRATION_LOCK_TIMEOUT_SECONDS`` 才会抛超时（此时明确报错，绝不
退回旧库造成两套并存）。

进程在「复制到一半」被强杀会留下 ``<target>.<pid>.<hex>.migrating`` 及其
``-wal/-shm/-journal``。这些残留由持锁方在每次迁移前做一次**有界**清理：只删
「命名完全匹配本模块模式 + pid 已不存在 + mtime 超过
``ORPHAN_STAGING_MIN_AGE_SECONDS``（默认 1 小时，可用
``LIUHAO_QUEUE_STAGING_MIN_AGE_SECONDS`` 覆盖）」的文件；pid 仍存活的、刚创建
的、以及目标库本身及其 sidecar 一律不碰。判不准 pid 是否存活时按存活处理。
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Dict, Optional

from src.common.atomic_write import _fsync_directory, _replace_with_retry, exclusive_file_lock
from src.common.data_paths import PathLike, data_root, resolve_store_path
# 进程存活探测与启动清扫共用同一判定（见 ``src/common/pid_probe.py``）：
# 两边各写一份容易漂移，宽松的一边会误删正在运行实例的临时文件。
from src.common.pid_probe import pid_is_alive as _pid_is_alive

logger = logging.getLogger(__name__)

QUEUE_DIRNAME = "queue"
_SQLITE_KIND = "sqlite"
_TEXT_KIND = "text"

#: 迁移临界区的跨进程锁等待上限（秒）。超时即明确报错，绝不退回旧库。
MIGRATION_LOCK_TIMEOUT_SECONDS = 30.0

#: 孤儿 staging 的最小年龄（秒）。持锁清理只删比这更旧的残留，避免把另一个
#: 进程刚创建、仍在写的临时文件当垃圾。可用环境变量
#: ``LIUHAO_QUEUE_STAGING_MIN_AGE_SECONDS`` 覆盖（保守默认 1 小时）。
ORPHAN_STAGING_MIN_AGE_SECONDS = 3600.0
_ORPHAN_STAGING_ENV = "LIUHAO_QUEUE_STAGING_MIN_AGE_SECONDS"

#: 单次清理最多检查的匹配项数：异常目录（成千上万个残留）不会拖死启动。
_ORPHAN_SCAN_LIMIT = 64


def legacy_queue_dir() -> Path:
    """历史默认落点：``~/.liuhao/queue``。"""
    return Path.home() / ".liuhao" / QUEUE_DIRNAME


def _workspace_queue_dir() -> Optional[Path]:
    raw = (os.environ.get("LIUHAO_WORKSPACE_ROOT") or "").strip()
    if not raw:
        return None
    return Path(raw).expanduser().resolve() / QUEUE_DIRNAME


def _previous_default(filename: str) -> Path:
    """旧代码在未设数据根时会用的位置（workspace 优先，否则用户主目录）。"""
    return (_workspace_queue_dir() or legacy_queue_dir()) / filename


def _explicit(env_var: Optional[str]) -> bool:
    return bool(env_var) and bool((os.environ.get(env_var) or "").strip())


def resolve_queue_file(
    filename: str, *, env_var: Optional[str] = None, kind: str = _SQLITE_KIND
) -> str:
    """返回队列存储文件的绝对路径，必要时把旧位置的数据迁移过来。"""
    legacy = _previous_default(filename)
    target = Path(
        resolve_store_path(
            f"{QUEUE_DIRNAME}/{filename}",
            str(legacy),
            env_var=env_var,
        )
    )
    if data_root() is None or _explicit(env_var):
        target.parent.mkdir(parents=True, exist_ok=True)
        return str(target)
    return str(_migrate_if_needed(target, legacy, kind=kind))


def resolve_sqlite_store(
    filename: str,
    legacy_default: PathLike,
    *,
    env_var: Optional[str] = None,
    workspace: bool = False,
    label: str = "存储",
) -> str:
    """通用 SQLite 单文件存储的落点解析（与队列复用同一套迁移引擎）。

    供 ``automation_tickets.db`` / ``verify_metrics.db`` / 工作流检查点 /
    双签证据库这类**单文件数据库**使用：它们此前各自拼 ``cwd`` 或仓库根目录，
    设置了 ``LIUHAO_DATA_DIR`` 也照写不误。这里统一走
    ``resolve_store_path``，并复用 :func:`_migrate_if_needed` 的
    「锁内双重检查 → backup API 复制 → 表集合/行数校验 → 原子落位 → 旧文件保留 →
    失败退回旧位置」流程。
    """
    legacy = Path(legacy_default).expanduser()
    target = Path(
        resolve_store_path(
            filename,
            str(legacy),
            env_var=env_var,
            workspace=workspace,
        )
    )
    if target == legacy or data_root() is None or _explicit(env_var):
        target.parent.mkdir(parents=True, exist_ok=True)
        return str(target)
    return str(_migrate_if_needed(target, legacy, kind=_SQLITE_KIND, label=label))


def _migrate_if_needed(
    target: Path,
    legacy: Path,
    *,
    kind: str,
    label: str = "队列存储",
    lock_description: str = "队列存储迁移",
) -> Path:
    """在跨进程锁内完成「检查 → 复制 → 校验 → 落位」，返回最终使用的路径。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = f"{target}.migrate.lock"
    try:
        with exclusive_file_lock(
            lock_path,
            timeout=MIGRATION_LOCK_TIMEOUT_SECONDS,
            description=lock_description,
        ):
            # 持锁期间不可能有别的进程正在迁移，此刻留下的 staging 都是孤儿；
            # 仍然用「pid 已死 + mtime 够旧」双重条件，避免 pid 复用误删。
            _cleanup_orphan_staging(target)
            # 双重检查：等锁期间另一个进程可能已经迁完（或已初始化）。
            if target.exists():
                if legacy.exists():
                    logger.info(
                        "%s同时存在于新旧位置，使用新位置 %s（旧文件 %s 保留未删）",
                        label,
                        target,
                        legacy,
                    )
                return target
            if not legacy.exists():
                # 两边都没有：正常初始化，由调用方建库。
                return target
            try:
                _copy_and_verify(legacy, target, kind=kind)
            except Exception as exc:  # noqa: BLE001 - 失败必须退回旧位置，不能空库启动
                logger.error(
                    "%s迁移失败（%s → %s）：%s: %s；继续使用旧位置 %s，"
                    "不会以空库启动。请检查磁盘空间/权限后重试。",
                    label,
                    legacy,
                    target,
                    type(exc).__name__,
                    exc,
                    legacy,
                )
                return legacy
            logger.info("%s已迁移到 %s；旧文件 %s 已保留未删。", label, target, legacy)
            return target
    except TimeoutError:
        logger.error(
            "%s迁移锁等待超时（%.0fs）：%s 正被其他进程占用。"
            "请关闭重复启动的实例后重试；锁由系统在进程退出时释放，无需手工删除。",
            label,
            MIGRATION_LOCK_TIMEOUT_SECONDS,
            lock_path,
        )
        raise


def _staging_path(target: Path) -> Path:
    """带 pid + 随机后缀的临时名：两个进程不会踩同一个临时文件。"""
    return target.with_name(
        f"{target.name}.{os.getpid()}.{secrets.token_hex(4)}.migrating"
    )


def _orphan_min_age_seconds() -> float:
    """孤儿清理的年龄阈值；环境变量非法时回落默认值并告警一次。"""
    raw = (os.environ.get(_ORPHAN_STAGING_ENV) or "").strip()
    if not raw:
        return ORPHAN_STAGING_MIN_AGE_SECONDS
    try:
        value = float(raw)
    except ValueError:
        logger.warning(
            "%s=%r 不是合法秒数，使用默认 %.0fs", _ORPHAN_STAGING_ENV, raw,
            ORPHAN_STAGING_MIN_AGE_SECONDS,
        )
        return ORPHAN_STAGING_MIN_AGE_SECONDS
    if value <= 0:
        logger.warning(
            "%s=%r 必须为正数，使用默认 %.0fs", _ORPHAN_STAGING_ENV, raw,
            ORPHAN_STAGING_MIN_AGE_SECONDS,
        )
        return ORPHAN_STAGING_MIN_AGE_SECONDS
    return value


def _staging_name_pattern(target: Path) -> re.Pattern[str]:
    """只匹配本模块产出的临时名及其 sidecar，绝不匹配目标库与 ``-wal/-shm``。"""
    return re.compile(
        rf"^{re.escape(target.name)}\.(\d+)\.([0-9a-f]+)"
        r"\.migrating(?:\-(?:wal|shm|journal))?$"
    )


def _cleanup_orphan_staging(target: Path) -> int:
    """持锁清理「pid 已退出 + 年龄超过阈值」的迁移临时文件，返回删除个数。

    只在 ``_migrate_if_needed`` 的锁内调用：此刻没有别的进程在迁移。仍然保留
    pid 与 mtime 双重条件，挡住 pid 复用、以及未来某条不走锁的写入路径。
    扫描与删除都有上限，异常目录不会拖死启动。
    """
    pattern = _staging_name_pattern(target)
    min_age = _orphan_min_age_seconds()
    now = time.time()
    scanned = 0
    removed = 0
    try:
        entries = list(target.parent.iterdir())
    except OSError as exc:
        logger.debug("扫描迁移临时文件失败（%s）：%s", target.parent, exc)
        return 0
    for entry in entries:
        match = pattern.match(entry.name)
        if not match:
            continue
        scanned += 1
        if scanned > _ORPHAN_SCAN_LIMIT:
            logger.debug(
                "迁移临时文件超过扫描上限 %d，本次只处理前 %d 个",
                _ORPHAN_SCAN_LIMIT, _ORPHAN_SCAN_LIMIT,
            )
            break
        pid = int(match.group(1))
        try:
            mtime = entry.stat().st_mtime
        except OSError:
            continue
        if now - mtime < min_age:
            continue
        if _pid_is_alive(pid):
            continue
        try:
            entry.unlink()
            removed += 1
        except OSError as exc:
            logger.debug("删除孤儿迁移临时文件失败（%s）：%s", entry, exc)
    if removed:
        logger.info(
            "已清理 %d 个孤儿迁移临时文件（pid 已退出且超过 %.0fs）", removed, min_age
        )
    return removed


def _sidecars(path: Path) -> tuple[Path, ...]:
    text = str(path)
    return tuple(Path(text + suffix) for suffix in ("-wal", "-shm", "-journal"))


def _clear_staging(staging: Path) -> None:
    """只清理本次调用自己的临时文件及其 sidecar。"""
    for path in (staging, *_sidecars(staging)):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def _fsync_file(path: Path) -> None:
    """把临时文件内容刷到磁盘；Windows 上用 O_RDWR 打开才能 FlushFileBuffers。

    刷不动就记 debug 并继续——落位后仍有下一层保障（原子 rename + 完整性校验），
    但不会假装做过。
    """
    try:
        fd = os.open(str(path), os.O_RDWR)
    except OSError as exc:
        logger.debug("迁移临时文件 fsync 打不开（%s）：%s", path, exc)
        return
    try:
        os.fsync(fd)
    except OSError as exc:
        logger.debug("迁移临时文件 fsync 失败（%s）：%s", path, exc)
    finally:
        os.close(fd)


def _copy_and_verify(source: Path, target: Path, *, kind: str) -> None:
    """复制 + 校验 + fsync + 原子落位。staging 只用本次调用自己的名字。"""
    staging = _staging_path(target)
    _clear_staging(staging)
    try:
        if kind == _SQLITE_KIND:
            expected = _sqlite_signature(source)
            _copy_sqlite(source, staging)
            actual = _sqlite_signature(staging)
            if actual[0] != "ok":
                raise OSError(f"迁移副本完整性校验未通过：{actual[0]}")
            if actual[1] != expected[1]:
                raise OSError(
                    f"迁移副本行数不一致：期望 {expected[1]}，实际 {actual[1]}"
                )
        else:
            payload = source.read_bytes()
            staging.write_bytes(payload)
            if staging.read_bytes() != payload:
                raise OSError(f"迁移副本内容不一致：{target}")
        _fsync_file(staging)
        _replace_with_retry(staging, target)
        # 目录 fsync 让 rename 本身也落盘；POSIX 生效，Windows 不支持这种
        # 打开方式，atomic_write._fsync_directory 里是 best-effort（静默跳过）。
        _fsync_directory(target.parent)
    finally:
        _clear_staging(staging)


def _copy_sqlite(source: Path, staging: Path) -> None:
    """用备份 API 复制，保证 WAL 里未 checkpoint 的行也进副本。"""
    src = sqlite3.connect(str(source), timeout=15.0, isolation_level=None)
    dst = sqlite3.connect(str(staging), timeout=15.0, isolation_level=None)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def _sqlite_signature(path: Path) -> tuple[str, Dict[str, int]]:
    """(integrity_check 结果, 逐表行数)。表集合即键集合，可同时比对结构。"""
    conn = sqlite3.connect(str(path), timeout=15.0)
    try:
        integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master"
                " WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
        ]
        rows = {
            table: int(
                conn.execute(
                    'SELECT COUNT(*) FROM "%s"' % table.replace('"', '""')
                ).fetchone()[0]
            )
            for table in tables
        }
    finally:
        conn.close()
    return integrity, rows


def file_digest(path: Path) -> str:
    """测试/排查用的内容指纹。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()
