"""人类身份注册表签名密钥的自举与安全落盘。

为什么需要这个模块
------------------
身份内核在 ``LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY`` 未设置时会**拒绝**
登记表里的每一行（fail-closed，HC-11 / U6）。对普通用户来说，这意味着
「装好了、进去了、却一个人都登不上」——不是错误提示不够好，而是根本
没有可用身份。把「生成密钥」压在运维身上，等于把首次使用变成一次配置
作业。

本模块把这件事收进启动路径：无环境变量时生成一个密钥，写到**数据目录**
（不是程序目录、不进安装包），并把权限收紧到当前用户。三条路径都要有
明确行为：

* **全新安装**：没有登记表或登记表为空 → 静默生成密钥；
* **已有数据升级**：登记表里有旧版（无 ``integrity`` 标签）的行 → 生成
  密钥后为这些行补签，并打印一行可读说明；
* **密钥缺失但已有签名身份库**：无法验证旧行时**不静默锁死**——生成新
  密钥供后续注册使用，同时打印恢复步骤（找回旧密钥 / 重新注册）。

密钥值本身**从不**打印到控制台或日志，只写进权限收紧的文件。
"""
from __future__ import annotations

import getpass
import json
import logging
import os
import secrets
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

logger = logging.getLogger("liuhao.common.integrity_key")

INTEGRITY_KEY_ENV = "LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY"
DATA_DIR_ENV = "LIUHAO_DATA_DIR"
IDENTITY_FILE_ENV = "LIUHAO_HUMAN_IDENTITIES_FILE"
IDENTITY_BACKEND_ENV = "LIUHAO_HUMAN_IDENTITIES_BACKEND"
IDENTITY_DB_ENV = "LIUHAO_HUMAN_IDENTITIES_DB"

KEY_FILE_REL = Path("config") / "integrity.key"
REGISTRY_FILE_REL = Path("config") / "human_identities.json"

_SQLITE_BACKEND = "sqlite"


def default_data_dir() -> Path:
    """平台默认数据目录（与 ``scripts/launch_installed.py`` 保持一致）。"""
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home())
        return base / "LiuHao" / "data"
    base = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    return base / "liuhao"


def resolve_data_dir() -> Path:
    """``LIUHAO_DATA_DIR`` 优先，否则平台默认数据目录。"""
    raw = (os.environ.get(DATA_DIR_ENV) or "").strip()
    return Path(raw).expanduser().resolve() if raw else default_data_dir().resolve()


def _tighten_permissions(path: Path) -> None:
    """把密钥文件收紧到「仅当前用户可读写」。

    POSIX 用 0600；Windows 的 mode 位不构成访问控制，改为去掉继承并用
    ``icacls`` 只授予当前用户。失败只告警，不阻断启动——密钥本身仍已落盘。
    """
    if os.name == "nt":
        user = (os.environ.get("USERNAME") or getpass.getuser() or "").strip()
        if not user:
            logger.warning("无法确定当前 Windows 用户，密钥文件权限未收紧")
            return
        try:
            subprocess.run(
                ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"],
                check=False,
                capture_output=True,
                text=True,
                # icacls 的输出跟随系统语言（中文系统是 GBK）。不钉编码时
                # Python 会按默认编码解码，在 UTF-8 模式下读 GBK 字节会在
                # 后台读取线程里抛 UnicodeDecodeError（栈直接进 launcher.log）。
                # 这里只关心退出码，输出本身不用，替换字符即可。
                encoding="utf-8",
                errors="replace",
            )
        except OSError as exc:  # noqa: BLE001 - 权限收紧是加固，不是启动前提
            logger.warning("收紧密钥文件权限失败（%s）", type(exc).__name__)
        return
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError as exc:  # noqa: BLE001
        logger.warning("收紧密钥文件权限失败（%s）", type(exc).__name__)


def _atomic_write(path: Path, text: str) -> None:
    """同目录临时文件 + ``os.replace``，避免读到写一半的密钥。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
    _tighten_permissions(path)


def _create_key_file(path: Path) -> str:
    """排他创建密钥文件；并发的第二个启动器复用先写入的值。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(3):
        candidate = secrets.token_hex(32)
        try:
            fd = os.open(
                path, os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                stat.S_IRUSR | stat.S_IWUSR,
            )
        except FileExistsError:
            existing = read_key_file(path)
            if existing:
                return existing
            continue
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(candidate + "\n")
        _tighten_permissions(path)
        return candidate
    raise OSError(f"无法创建密钥文件：{path}")


def read_key_file(path: Path) -> str:
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError as exc:  # noqa: BLE001
        logger.warning("读取密钥文件 %s 失败（%s）", path, type(exc).__name__)
        return ""


def _registry_path(registry_override: Optional[Path], data_dir: Path) -> Optional[Path]:
    if registry_override is not None:
        return Path(registry_override)
    raw = (os.environ.get(IDENTITY_FILE_ENV) or "").strip()
    if raw:
        return Path(raw).expanduser()
    candidate = data_dir / REGISTRY_FILE_REL
    return candidate if candidate.is_file() else None


def _load_registry(path: Path) -> Tuple[Optional[Union[Dict[str, Any], List[Any]]], List[Dict[str, Any]]]:
    """返回 (原始文档, 行列表)；不可读时返回 (None, [])。"""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - 登记表损坏不是崩溃理由
        logger.warning("读取身份登记表 %s 失败（%s）", path, type(exc).__name__)
        print(
            f"[warn] 身份登记表 {path} 无法解析（{type(exc).__name__}）；"
            "本次不会修改它，请人工检查后再启动。",
            file=sys.stderr,
        )
        return None, []
    if isinstance(payload, dict):
        entries = payload.get("humans")
    elif isinstance(payload, list):
        entries = payload
    else:
        entries = None
    if not isinstance(entries, list):
        return None, []
    return payload, [row for row in entries if isinstance(row, dict)]


def _rewrite_registry(path: Path, payload: Union[Dict[str, Any], List[Any]]) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    _atomic_write(path, text)


def _sign_untagged_rows(path: Path, payload: Union[Dict[str, Any], List[Any]], key: str) -> int:
    """为旧版（无 ``integrity`` 标签）的行补签，返回补签条数。"""
    from src.kernels.identity._persistence import FIELD_INTEGRITY, compute_row_tag

    entries = payload.get("humans") if isinstance(payload, dict) else payload
    if not isinstance(entries, list):
        return 0
    signed = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if str(entry.get(FIELD_INTEGRITY) or "").strip():
            continue
        tag = compute_row_tag(entry, key)
        if tag:
            entry[FIELD_INTEGRITY] = tag
            signed += 1
    if signed:
        _rewrite_registry(path, payload)
    return signed


def _sqlite_registry_exists() -> Optional[Path]:
    if (os.environ.get(IDENTITY_BACKEND_ENV) or "").strip().lower() != _SQLITE_BACKEND:
        return None
    raw = (os.environ.get(IDENTITY_DB_ENV) or "").strip()
    if not raw:
        return None
    candidate = Path(raw).expanduser()
    return candidate if candidate.is_file() and candidate.stat().st_size > 0 else None


def ensure_integrity_key(
    *,
    data_dir: Optional[Path] = None,
    registry_path: Optional[Path] = None,
    announce: bool = True,
) -> str:
    """返回可用的注册表签名密钥，需要时生成并落盘到数据目录。

    优先级：显式环境变量 > 数据目录里的密钥文件 > 新生成。
    新生成时会按登记表状态区分「全新安装 / 旧数据升级 / 密钥丢失」三种路径，
    并把结论以可读中文打印到 stderr（永不打印密钥值）。
    """
    base = Path(data_dir).expanduser().resolve() if data_dir else resolve_data_dir()
    key_path = base / KEY_FILE_REL
    env_key = (os.environ.get(INTEGRITY_KEY_ENV) or "").strip()
    file_key = read_key_file(key_path)

    if env_key:
        if not file_key:
            # 把显式配置的密钥固化到数据目录：下次没有环境变量也能启动，
            # 换机/托盘自启不会因为「环境丢了」变成无人可登录。
            _atomic_write(key_path, env_key + "\n")
        elif file_key != env_key:
            print(
                f"[warn] 环境变量 {INTEGRITY_KEY_ENV} 与 {key_path} 中的密钥不一致，"
                "按环境变量（显式配置优先）执行；若想固定使用文件中的密钥，"
                "请删除该环境变量后重启。",
                file=sys.stderr,
            )
        return env_key

    if file_key:
        return file_key

    key = _create_key_file(key_path)

    registry = _registry_path(registry_path, base)
    rows: List[Dict[str, Any]] = []
    payload: Optional[Union[Dict[str, Any], List[Any]]] = None
    if registry is not None and registry.is_file():
        payload, rows = _load_registry(registry)

    tagged = [row for row in rows if str(row.get("integrity") or "").strip()]
    if tagged:
        # 密钥丢失但库里有已签名记录：明确说明，而不是静默让人登不上。
        print(
            f"[warn] 人类身份登记表 {registry} 中有 {len(tagged)} 条已签名记录，"
            f"但签名密钥缺失（既没有 {key_path}，也没有设置 {INTEGRITY_KEY_ENV}）。\n"
            "       这些旧记录无法通过验证，按 fail-closed 不会被人身份内核接纳。\n"
            "       已生成新密钥供后续注册使用；请选择一种恢复方式：\n"
            "         1) 若备份过旧密钥：恢复到上述文件或设置上述环境变量后重启，旧记录立即恢复；\n"
            "         2) 若确认旧密钥已丢失：旧记录应视为不可信，请重新注册管理员：\n"
            "            python scripts/register_human_identity.py --principal <name> --password-stdin\n"
            "            确认不再需要后，再删除或改名旧的登记表。",
            file=sys.stderr,
        )
        logger.warning(
            "身份登记表有 %d 条已签名记录但密钥缺失；已生成新密钥，旧记录保持拒绝",
            len(tagged),
        )
    elif payload is not None and rows:
        signed = _sign_untagged_rows(registry, payload, key)
        if announce and signed:
            print(
                f"[ok] 身份登记表 {registry} 中有 {signed} 条旧版（无签名）记录；"
                "已用新生成的密钥补签（升级迁移，不授予任何新权限）。",
                file=sys.stderr,
            )
    elif (sqlite_db := _sqlite_registry_exists()) is not None:
        print(
            f"[warn] 当前身份后端为 sqlite（{sqlite_db}），"
            "密钥缺失时库中已签名身份无法验证。已生成新密钥供后续注册；"
            "若旧记录仍需使用，请恢复原密钥，否则需重新注册。",
            file=sys.stderr,
        )

    return key
