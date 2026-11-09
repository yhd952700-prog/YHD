"""启动时的有界临时文件清扫（只清**本程序自己产出**的命名模式）。

解决什么问题
============
进程被强杀 / 断电时，``tempfile.mkdtemp`` 建出来的升级暂存、自检沙箱、恢复
staging、提醒脚本都不会走到 ``finally`` 清理，于是永久留在 ``%TEMP%``。上一轮
普查已把这类文件归为 **B 类（平台标准临时位置）**，但"合理的位置"不等于"可以
无限堆积"。本模块在启动时做一次**有界**清扫。

安全边界（宁可不删）
------------------
* **只认命名模式**：目录名/文件名必须精确匹配 ``PRODUCT_DIR_PATTERNS`` /
  ``PRODUCT_FILE_PATTERNS``。通用临时目录里的其它文件一律不碰。
* **pid 必须已退出**：模式里带 pid，探测交给 ``src.common.pid_probe``；无法证明
  已退出（权限不足、探测异常、pid 复用疑云）按**存活**处理，跳过。
* **mtime 超过阈值**：默认 7 天（``LIUHAO_TEMP_SWEEP_MIN_AGE_SECONDS`` 可覆盖），
  避免把另一个进程刚刚创建、还在用的目录当垃圾。
* **单次有上限**：最多处理 ``MAX_CANDIDATES`` 个匹配项，异常目录不会拖慢启动。
* **只记日志**：单个删除记 ``debug``，汇总记 ``info``；任何异常都吞掉，启动
  流程不受影响。
* **取证沙箱是唯一例外（没有 pid）**：``liuhao-maintenance-evidence`` 由人工/
  取证脚本创建，不是 ``mkdtemp``，因此用更严的判据——名字必须**完全一致**、
  目录里必须有本产品的内容指纹（``data/master.key`` + 至少一个取证脚本/密文
  条目）、默认保留 30 天、出现 ``KEEP`` / ``.keep`` / ``DO_NOT_DELETE`` /
  ``keep-evidence*`` 永不删除、删除前先原子改名隔离（Windows 上被占用的目录
  改名会失败 → 按"仍在用"保留）。保留期内也会把其中**所有**按设计持密的文件
  （主密钥、凭据库、身份库、`root.token` / `member.token`、模型配置库及其
  WAL/SHM，见 ``_EVIDENCE_SECRET_FILES``）收紧到当前用户，避免"等 30 天期间
  一直裸露"。候选根只有当前用户自己的临时目录
  （``tempfile.gettempdir()`` 与 ``%LOCALAPPDATA%\\Temp``），且只认直接子项。

启动开销（为什么需要缓存）
------------------------
本机 ``%TEMP%`` 实测 33,735 条时，一次裸 ``os.scandir`` 就要 ~100ms。清扫挂在
每个进程启动路径上（CLI / 桌面 / 网关），不能每次启动都付这个钱。因此扫描前
先比一次"临时目录自上次清扫后有没有变化"：

* 变化信号取临时目录自身的 ``st_mtime_ns`` —— 目录里新增/删除条目（正是残留
  产生与清理的方式）都会改变它；文件内容变化不改变，但我们不关心那种情况。
* 缓存落在**数据根**（``<data>/temp-sweep.stamp.json``），不写临时目录本身，
  避免"写缓存又改了临时目录 mtime"的自我失效。
* 缓存最长有效 24h：即使目录没动静，每天也至少完整扫一次，避免"上次扫描时
  pid 还活着、随后退出的残留"被无限期跳过。取证沙箱的判定与**凭证收紧**不受
  缓存影响：每次启动都跑（本机温态实测整轮 ~16ms，其中 8 个持密文件各一次
  ``GetFileSecurity`` 约 2.8ms；旧范围 2 个文件 ~0.6ms）。凭证不能跟着 24h
  缓存延后——取证进程可能在保留期内再次写入新文件，新文件会重新继承孤儿
  SID，多等一天就是多裸露一天。冷启动多出的 pywin32 导入（~44ms）与主密钥
  ACL 自检共用，不因扩大清单而增加。
* 拿不到数据根、读缓存失败、时钟回拨等异常一律退回完整扫描（宁可慢，不可漏
  的根本保障是判据本身，不是缓存）。

两条已知边界（都是结论，不是待办）：

* **"目录内容变了但 mtime 没变"会漏扫一次。** 残留是**顶层条目**，创建/删除
  必然改动父目录 mtime，正常路径不存在这种情况；唯一可能是 NTFS 目录时间戳
  的惰性刷新/隧道复用把两次写入的 mtime 撞成同一个值。此时该残留最多被推迟到
  下一次"临时目录 mtime 变化"或 24h 到期；不会永久滞留，也不会误删——判据本身
  仍要求 pid 已退出且超过保留期。
* **24h 到期后无条件完整扫一次**（即使目录 mtime 没变），并把 ``swept_at``
  重置为本次时间。因此"上次扫描时 pid 还活着、随后退出"的残留，最长等待一个
  24h 周期就会被处理。

为什么生产者要带 pid
--------------------
``mkdtemp(prefix=...)`` 默认只带随机后缀，清理方无法判断归属。给目录名加上
创建者 pid 之后，"是否还在用"就有据可查；这也是本模块能与提醒脚本采用同一套
判据的前提。
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import shutil
import tempfile
import time
from typing import Iterator, Optional, Tuple

from src.common.pid_probe import pid_is_alive

logger = logging.getLogger(__name__)

#: 单次扫描最多处理的匹配项；超过即停（异常目录不会拖死启动）。
MAX_CANDIDATES = 64

#: 默认保留期：7 天。升级/恢复这类操作以分钟计，7 天足以排除"仍在进行"。
DEFAULT_MIN_AGE_SECONDS = 7 * 24 * 3600

#: 覆盖保留期的环境变量（秒，必须为正数；非法值回落默认并记 warning）。
MIN_AGE_ENV = "LIUHAO_TEMP_SWEEP_MIN_AGE_SECONDS"

#: 取证/维护沙箱目录名：人工创建，没有 pid，只有**完全同名**才考虑。
EVIDENCE_DIR_NAME = "liuhao-maintenance-evidence"

#: 取证沙箱默认保留期 30 天（审计留痕按月归档；比 pid 残留长得多）。
DEFAULT_EVIDENCE_MIN_AGE_SECONDS = 30 * 24 * 3600

#: 覆盖取证沙箱保留期的环境变量（秒，必须为正数）。
EVIDENCE_MIN_AGE_ENV = "LIUHAO_EVIDENCE_SWEEP_MIN_AGE_SECONDS"

#: 置 0/off/false/no 可关闭取证沙箱清扫（只影响这一条规则）。
EVIDENCE_SWEEP_ENV = "LIUHAO_EVIDENCE_SWEEP"

#: 目录里出现任一标记 → 永不自动删除（人工保留意愿优先）。
_EVIDENCE_KEEP_MARKERS = ("KEEP", ".keep", "DO_NOT_DELETE", "keep-evidence")

#: 内容指纹：必须先证明这是**本产品**的取证沙箱，而不是同名目录。
_EVIDENCE_ANCHOR = os.path.join("data", "master.key")
_EVIDENCE_MARKERS = (
    "capture_evidence.js",
    "serve_console.js",
    "auth_secrets.json",
    "identities.json",
)

#: 沙箱里**按设计持密**的文件（相对沙箱根的路径）。清单来自对真实沙箱的逐项
#: 枚举，而不是只补三个点名文件：
#:
#: * ``data/master.key``：主密钥，解开模型 API Key 与审计链；
#: * ``auth_secrets.json``：登录凭据库（PBKDF2 记录，仍是口令材料）；
#: * ``identities.json``：身份库（``integrity`` / ``activation_hash`` /
#:   ``recovery_hash``）——能改写它就能改「谁有主权」；
#: * ``root.token`` / ``member.token``：抓取期间签发的 Bearer 令牌，过期前可直接
#:   冒充对应身份；
#: * ``model_config.sqlite3`` 及其 ``-wal`` / ``-shm``：``ai_model_configs`` 表有
#:   ``api_key_enc`` 列，WAL/SHM 是同一页数据的分身，一并收紧。
#:
#: 明确**不在**清单里的同级条目（枚举时逐项确认过，写进 docs/known-gaps.md）：
#: ``capture_evidence.js`` 只在运行时读 token 不内嵌密钥；``alerts.json`` 的
#: receiver 全是 ``console``；``rbac.json`` 零凭据命中；审计/会话/记忆库和脚本
#: 不是按设计的凭据存储；``*.lock`` 是 1 字节空锁；``chrome-profile/`` 是浏览器
#: 自己的目录，递归改写 ACL 会与运行中的浏览器抢文件，另行登记。
_EVIDENCE_SECRET_FILES = (
    _EVIDENCE_ANCHOR,
    "auth_secrets.json",
    "identities.json",
    "root.token",
    "member.token",
    "model_config.sqlite3",
    "model_config.sqlite3-wal",
    "model_config.sqlite3-shm",
)

#: 有界遍历：最多看 512 个条目、4 层（避免异常目录拖慢启动）。
_EVIDENCE_MAX_ENTRIES = 512
_EVIDENCE_MAX_DEPTH = 4

#: 本程序所有自产名称的公共前缀（快速过滤用，见 ``_match_pid``）。
_FAST_PREFIX = "liuhao"

#: 扫描缓存：数据根下记录"临时目录 + 上次清扫时的 mtime"，据此判断能否整轮跳过。
_STAMP_FILENAME = "temp-sweep.stamp.json"

#: 缓存最长有效 24h（见模块 docstring「启动开销」）。
_STAMP_MAX_AGE_SECONDS = 24 * 3600

#: 本程序产出的临时**目录**（名字里带创建者 pid，见各生产者的 ``mkdtemp``）。
_PRODUCT_DIR_PATTERNS: Tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern)
    for pattern in (
        r"^liuhao-upgrade-download-(\d+)-[A-Za-z0-9_]+$",
        r"^liuhao-upgrade-stage-(\d+)-[A-Za-z0-9_]+$",
        r"^liuhao-selfcheck-(\d+)-[A-Za-z0-9_]+$",
        r"^liuhao-restore-stage-(\d+)-[A-Za-z0-9_]+$",
        r"^liuhao-selfmod-copy-(\d+)-[A-Za-z0-9_]+$",
        r"^liuhao_filetool_(\d+)_[A-Za-z0-9_]+$",
    )
)

#: 本程序产出的临时**文件**（同样带 pid）。
_PRODUCT_FILE_PATTERNS: Tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern)
    for pattern in (
        r"^liuhao_reminder_balloon_(\d+)\.ps1$",
        r"^liuhao_reminder_(\d+)_[A-Za-z0-9_]+\.json$",
        r"^liuhao-selfmod-test-(\d+)-[A-Za-z0-9_]+\.log$",
    )
)


def min_age_seconds() -> float:
    """清扫保留期（秒）：环境变量非法时回落默认值并告警一次。"""
    raw = (os.environ.get(MIN_AGE_ENV) or "").strip()
    if not raw:
        return DEFAULT_MIN_AGE_SECONDS
    try:
        value = float(raw)
    except ValueError:
        logger.warning(
            "%s=%r 不是合法秒数，使用默认 %.0fs", MIN_AGE_ENV, raw,
            DEFAULT_MIN_AGE_SECONDS,
        )
        return DEFAULT_MIN_AGE_SECONDS
    if value <= 0:
        logger.warning(
            "%s=%r 必须为正数，使用默认 %.0fs", MIN_AGE_ENV, raw,
            DEFAULT_MIN_AGE_SECONDS,
        )
        return DEFAULT_MIN_AGE_SECONDS
    return value


def evidence_min_age_seconds() -> float:
    """取证沙箱保留期（秒）：非法值回落 30 天并告警一次。"""
    raw = (os.environ.get(EVIDENCE_MIN_AGE_ENV) or "").strip()
    if not raw:
        return DEFAULT_EVIDENCE_MIN_AGE_SECONDS
    try:
        value = float(raw)
    except ValueError:
        logger.warning(
            "%s=%r 不是合法秒数，使用默认 %.0fs", EVIDENCE_MIN_AGE_ENV, raw,
            DEFAULT_EVIDENCE_MIN_AGE_SECONDS,
        )
        return DEFAULT_EVIDENCE_MIN_AGE_SECONDS
    if value <= 0:
        logger.warning(
            "%s=%r 必须为正数，使用默认 %.0fs", EVIDENCE_MIN_AGE_ENV, raw,
            DEFAULT_EVIDENCE_MIN_AGE_SECONDS,
        )
        return DEFAULT_EVIDENCE_MIN_AGE_SECONDS
    return value


def evidence_sweep_enabled() -> bool:
    """默认开启；``LIUHAO_EVIDENCE_SWEEP=0/off/false/no`` 可显式关闭。"""
    raw = (os.environ.get(EVIDENCE_SWEEP_ENV) or "").strip().lower()
    return raw not in {"0", "off", "false", "no"}


def _looks_like_product_evidence_sandbox(path: str) -> bool:
    """内容指纹：``data/master.key`` + 至少一个取证脚本/密文条目。

    同名目录不算数——只有本产品真正跑过、留下这些文件的目录才会被处理。
    """
    if not os.path.isfile(os.path.join(path, _EVIDENCE_ANCHOR)):
        return False
    return any(
        os.path.exists(os.path.join(path, marker))
        for marker in _EVIDENCE_MARKERS
    )


def _has_keep_marker(path: str) -> bool:
    """人工保留标记：任一命中就永不自动删；读失败也按"要保留"处理。"""
    for name in _EVIDENCE_KEEP_MARKERS:
        if os.path.exists(os.path.join(path, name)):
            return True
    try:
        with os.scandir(path) as scanner:
            return any(
                entry.name.lower().startswith("keep-evidence")
                for entry in scanner
            )
    except OSError:
        return True


def _newest_mtime_bounded(path: str) -> Tuple[Optional[float], bool]:
    """有界返回 ``(目录树最新 mtime, 是否因预算截断)``。

    目录自身 mtime 只反映"顶层条目增删"，文件内容重写不会改它；取证沙箱
    可能被重新生成，所以取整棵树（预算内）的最新时间。截断即视为"无法证明
    够旧"，调用方保留，避免误删正在写的证据。
    """
    try:
        newest = os.stat(path).st_mtime
    except OSError:
        return None, False
    seen = 0
    truncated = False
    stack: list[Tuple[str, int]] = [(path, 0)]
    while stack:
        current, depth = stack.pop()
        try:
            scanner = os.scandir(current)
        except OSError:
            continue
        with scanner:
            for entry in scanner:
                seen += 1
                if seen > _EVIDENCE_MAX_ENTRIES:
                    truncated = True
                    break
                try:
                    if entry.is_symlink():
                        continue
                    stat_result = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                if stat_result.st_mtime > newest:
                    newest = stat_result.st_mtime
                if (
                    depth + 1 < _EVIDENCE_MAX_DEPTH
                    and entry.is_dir(follow_symlinks=False)
                ):
                    stack.append((entry.path, depth + 1))
        if truncated:
            break
    return newest, truncated


def _tighten_evidence_secrets(path: str) -> None:
    """保留期内也把沙箱里**所有**持密文件收紧到当前用户。

    对已受保护的文件只做一次 ``GetFileSecurity``（约 0.3ms），不会重复写 DACL；
    收紧失败只告警，下次启动重试——证据已经落盘，读不到才是更大的故障。
    逐文件而不是递归整棵树：``chrome-profile/`` 这类运行中目录不在清单里。
    """
    from src.common.fs_permissions import ensure_restricted_to_current_user

    for relative in _EVIDENCE_SECRET_FILES:
        candidate = os.path.join(path, relative)
        if not os.path.isfile(candidate):
            continue
        try:
            if not ensure_restricted_to_current_user(candidate):
                logger.warning("取证沙箱凭据文件 ACL 未能收紧：%s", candidate)
        except Exception as exc:  # noqa: BLE001 - 启动路径不得抛出
            logger.debug("取证沙箱凭据文件 ACL 收紧失败（%s）：%s", candidate, exc)


def _remove_quarantined(path: str) -> bool:
    """先原子改名隔离再删除；改名失败=仍被占用，按存活保留。"""
    quarantine = f"{path}.deleting-{os.getpid()}-{os.urandom(3).hex()}"
    try:
        os.rename(path, quarantine)
    except OSError as exc:
        logger.debug("取证沙箱可能仍在使用，保留：%s（%s）", path, exc)
        return False
    try:
        shutil.rmtree(quarantine)
    except OSError as exc:
        logger.warning("删除取证沙箱失败，尝试改名回滚：%s（%s）", quarantine, exc)
        with contextlib.suppress(OSError):
            os.rename(quarantine, path)
        return False
    return not os.path.exists(quarantine)


def _evidence_roots(directory: Optional[str]) -> Tuple[str, ...]:
    """取证沙箱的候选根目录（只含**当前用户自己的**临时目录）。

    显式传入 ``directory`` 时只用它（测试与调用方可控）。否则取两个：

    * ``tempfile.gettempdir()``：本进程实际使用的临时目录；
    * ``%LOCALAPPDATA%\\Temp``：Windows 默认的当前用户临时目录。二者可能因
      ``TEMP`` 被重定向而不一致（本机实测 TEMP=D:\\cache\\temp，而残留落在
      ``C:\\Users\\<user>\\AppData\\Local\\Temp``），只看前者会漏掉历史残留。

    仍然只认「完全同名 + 内容指纹」的直接子项，绝不递归或扫描别人的目录。
    """
    if directory:
        return (directory,)
    roots = [tempfile.gettempdir()]
    local = (os.environ.get("LOCALAPPDATA") or "").strip()
    if local:
        roots.append(os.path.join(local, "Temp"))
    seen: set[str] = set()
    unique: list[str] = []
    for root in roots:
        key = os.path.normcase(os.path.abspath(root))
        if key in seen:
            continue
        seen.add(key)
        unique.append(root)
    return tuple(unique)


def _sweep_one_evidence_sandbox(
    path: str, *, age_limit: Optional[float], now: Optional[float]
) -> dict[str, int]:
    empty = {"matched": 0, "removed": 0, "kept": 0}
    if not os.path.isdir(path) or os.path.islink(path):
        return empty
    if not _looks_like_product_evidence_sandbox(path):
        logger.debug("跳过同名但非本产品取证沙箱的目录：%s", path)
        return empty

    stats = {"matched": 1, "removed": 0, "kept": 0}
    _tighten_evidence_secrets(path)
    if _has_keep_marker(path):
        stats["kept"] = 1
        logger.debug("取证沙箱存在保留标记，跳过：%s", path)
        return stats
    newest, truncated = _newest_mtime_bounded(path)
    if truncated or newest is None:
        stats["kept"] = 1
        logger.debug("取证沙箱内容超出扫描预算，按保留处理：%s", path)
        return stats
    cutoff = (now if now is not None else time.time()) - (
        evidence_min_age_seconds() if age_limit is None else age_limit
    )
    if newest > cutoff:
        stats["kept"] = 1
        return stats
    if _remove_quarantined(path):
        stats["removed"] = 1
        logger.info("启动清扫：删除过期取证沙箱 %s（最新内容早于保留期）", path)
    else:
        stats["kept"] = 1
    return stats


def sweep_stale_evidence_sandboxes(
    *,
    directory: Optional[str] = None,
    age_limit: Optional[float] = None,
    now: Optional[float] = None,
) -> dict[str, int]:
    """清扫过期的 ``liuhao-maintenance-evidence``（判据见模块 docstring）。

    不论是否删除，都会先尝试把沙箱里所有持密文件（见
    ``_EVIDENCE_SECRET_FILES``：主密钥、凭据库、身份库、抓取令牌、模型配置库
    及其 WAL/SHM）收紧到当前用户：保留期不是"继续裸露"的借口。
    不传 ``directory`` 时按 :func:`_evidence_roots` 覆盖当前用户的两个临时根。
    """
    stats = {"matched": 0, "removed": 0, "kept": 0}
    if not evidence_sweep_enabled():
        return stats
    for root in _evidence_roots(directory):
        part = _sweep_one_evidence_sandbox(
            os.path.join(root, EVIDENCE_DIR_NAME),
            age_limit=age_limit,
            now=now,
        )
        for key in stats:
            stats[key] += part[key]
    return stats


def _match_pid(name: str, patterns: Tuple[re.Pattern[str], ...]) -> Optional[int]:
    # 先用一次 C 级 startswith 挡掉绝大多数无关项：真实 %TEMP% 动辄三万条，
    # 直接对每条跑 9 个正则会让启动多花 100ms 以上（实测 117ms）。所有自产
    # 模式都以 "liuhao" 开头，所以这一层过滤不改变语义。
    if not name.startswith(_FAST_PREFIX):
        return None
    for pattern in patterns:
        match = pattern.match(name)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                return None
    return None


def _stamp_path() -> Optional[str]:
    """缓存文件绝对路径：数据根下的 ``temp-sweep.stamp.json``。

    数据根按与 ``logging_config.resolve_data_dir`` 相同的契约解析
    （``LIUHAO_DATA_DIR`` > ``%LOCALAPPDATA%/LiuHao/data``），但**不导入那个
    模块**：它会拖进 ``yaml``，让 CLI 启动多付一次不必要的导入成本。
    绝不回退到仓库根 —— 那是上一轮普查里明确要修掉的"写盘落到代码目录"。
    """
    raw = (os.environ.get("LIUHAO_DATA_DIR") or "").strip()
    if raw:
        base = raw
    else:
        base = os.path.join(
            os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
            "LiuHao",
            "data",
        )
    return os.path.join(os.path.abspath(base), _STAMP_FILENAME)


def _dir_mtime_ns(directory: str) -> Optional[int]:
    try:
        return os.stat(directory).st_mtime_ns
    except OSError:
        return None


def _read_stamp(path: str) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError) as exc:
        logger.debug("清扫缓存不可读（%s）：%s", path, exc)
        return None
    return payload if isinstance(payload, dict) else None


def _write_stamp(path: str, directory: str, mtime_ns: int) -> None:
    payload = {"temp_dir": directory, "mtime_ns": mtime_ns, "swept_at": time.time()}
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        os.replace(tmp, path)
    except OSError as exc:
        logger.debug("清扫缓存写入失败（%s）：%s", path, exc)
        with contextlib.suppress(OSError):
            os.unlink(tmp)


def _sweep_is_redundant(directory: str, mtime_ns: Optional[int]) -> bool:
    """临时目录与上次清扫时一模一样、且缓存未过期 → 本轮可以整轮跳过。"""
    if mtime_ns is None:
        return False
    path = _stamp_path()
    if path is None:
        return False
    stamp = _read_stamp(path)
    if not stamp:
        return False
    if os.path.normcase(str(stamp.get("temp_dir"))) != os.path.normcase(directory):
        return False
    if stamp.get("mtime_ns") != mtime_ns:
        return False
    swept_at = stamp.get("swept_at")
    if not isinstance(swept_at, (int, float)):
        return False
    if abs(time.time() - swept_at) > _STAMP_MAX_AGE_SECONDS:
        return False
    return True


def _remember_sweep(directory: str, path: Optional[str] = None) -> None:
    """记录"这个临时目录已经被扫过"；mtime 取清扫**之后**的值。"""
    if path is None:
        path = _stamp_path()
    if path is None:
        return
    mtime_ns = _dir_mtime_ns(directory)
    if mtime_ns is None:
        return
    _write_stamp(path, directory, mtime_ns)


def _iter_candidates(directory: str) -> Iterator[Tuple[str, int, bool]]:
    """产出 ``(路径, pid, 是否目录)``；只认本程序命名模式。"""
    try:
        scanner = os.scandir(directory)
    except OSError as exc:
        logger.debug("临时目录扫描失败（%s）：%s", directory, exc)
        return
    with scanner:
        for entry in scanner:
            pid = _match_pid(entry.name, _PRODUCT_DIR_PATTERNS)
            if pid is not None:
                try:
                    if not entry.is_dir():
                        continue
                except OSError:
                    continue
                yield entry.path, pid, True
                continue
            pid = _match_pid(entry.name, _PRODUCT_FILE_PATTERNS)
            if pid is None:
                continue
            try:
                if entry.is_dir():
                    continue
            except OSError:
                continue
            yield entry.path, pid, False


def _remove(path: str, *, is_dir: bool) -> bool:
    try:
        if is_dir:
            shutil.rmtree(path, ignore_errors=True)
        else:
            os.unlink(path)
    except OSError as exc:
        logger.debug("清理临时产物失败（%s）：%s", path, exc)
        return False
    return not os.path.exists(path)


def sweep_stale_temp_artifacts(
    *,
    directory: Optional[str] = None,
    age_limit: Optional[float] = None,
    max_candidates: int = MAX_CANDIDATES,
    now: Optional[float] = None,
) -> dict[str, int]:
    """扫描并清理「命名匹配 + pid 已退出 + 超过保留期」的临时产物。

    Returns:
        ``{"matched": 匹配数, "removed": 删除数, "kept": 保留数}``。
        ``kept`` 包含"pid 仍存活"和"未到保留期"两类。
    """
    target_dir = directory or tempfile.gettempdir()
    # 临时目录自上次清扫后没有任何变化 → pid 残留不必再遍历（见模块 docstring）。
    # 取证沙箱仍然处理：它只需要对固定路径做几次 stat（实测 ~1ms），而且是
    # "保留期内也要收紧密钥 ACL"的唯一时机，不能跟着 24h 缓存一起延后。
    before_mtime = _dir_mtime_ns(target_dir)
    if _sweep_is_redundant(target_dir, before_mtime):
        logger.debug("临时目录自上次清扫后无变化，跳过本轮扫描：%s", target_dir)
        return sweep_stale_evidence_sandboxes(directory=directory, now=now)
    cutoff = (now if now is not None else time.time()) - (
        min_age_seconds() if age_limit is None else age_limit
    )
    stats = {"matched": 0, "removed": 0, "kept": 0}
    limit = max(1, int(max_candidates))
    candidates = _iter_candidates(target_dir)
    try:
        for path, pid, is_dir in candidates:
            if stats["matched"] >= limit:
                logger.debug("临时产物超过单次上限 %d，本次只处理前 %d 个", limit, limit)
                break
            stats["matched"] += 1
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                stats["kept"] += 1
                continue
            if mtime > cutoff:
                stats["kept"] += 1
                continue
            if pid_is_alive(pid):
                stats["kept"] += 1
                continue
            if _remove(path, is_dir=is_dir):
                stats["removed"] += 1
                logger.debug("已清理临时产物：%s（pid=%s 已退出）", path, pid)
            else:
                stats["kept"] += 1
    finally:
        # 达到上限提前退出时也要立刻释放目录句柄（Windows 上尤其重要）。
        candidates.close()
    if stats["removed"]:
        logger.info(
            "启动清扫：删除 %d 个过期临时产物（pid 已退出且超过 %.0fs）",
            stats["removed"],
            min_age_seconds() if age_limit is None else age_limit,
        )
    # 唯一没有 pid 的自产目录：人工/取证沙箱，判据与保留期见模块 docstring。
    evidence = sweep_stale_evidence_sandboxes(directory=directory, now=now)
    for key in stats:
        stats[key] += evidence[key]
    # 记录本次清扫后的目录状态（包括删除造成的 mtime 变化），供下次启动判断
    # 能否整轮跳过。写缓存失败不影响返回结果。
    _remember_sweep(target_dir)
    return stats


_swept = False


def run_startup_temp_sweep() -> dict[str, int]:
    """进程启动时执行一次；同进程重复调用直接返回空结果。

    绝不让清理失败影响启动：任何异常都只记 ``debug``。
    """
    global _swept
    if _swept:
        return {"matched": 0, "removed": 0, "kept": 0}
    _swept = True
    try:
        return sweep_stale_temp_artifacts()
    except Exception as exc:  # noqa: BLE001 - 清扫失败不阻断启动
        logger.debug("启动临时产物清扫跳过：%s: %s", type(exc).__name__, exc)
        return {"matched": 0, "removed": 0, "kept": 0}


__all__ = [
    "DEFAULT_EVIDENCE_MIN_AGE_SECONDS",
    "DEFAULT_MIN_AGE_SECONDS",
    "EVIDENCE_DIR_NAME",
    "EVIDENCE_MIN_AGE_ENV",
    "EVIDENCE_SWEEP_ENV",
    "MAX_CANDIDATES",
    "MIN_AGE_ENV",
    "evidence_min_age_seconds",
    "evidence_sweep_enabled",
    "min_age_seconds",
    "run_startup_temp_sweep",
    "sweep_stale_evidence_sandboxes",
    "sweep_stale_temp_artifacts",
]
