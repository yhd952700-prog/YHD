#!/usr/bin/env python
"""安装版入口：把「程序目录」与「用户数据目录」分开，再交给一键启动器。

为什么单独一个入口
------------------
仓库里直接跑 ``start-liuhao.bat`` 时，所有运行数据都落在仓库根（身份登记表、
登录凭据、模型配置 SQLite、对话库、审计库、JWT 吊销库……）。装到
``%LOCALAPPDATA%\\LiuHao\\app`` 之后，这些文件必须改落到
``%LOCALAPPDATA%\\LiuHao\\data``，否则：

* 升级覆盖 ``app`` 时会顺带把用户数据一起换掉；
* 卸载时无法把「程序」和「我的数据」分开处理；
* 多用户/多实例共享同一份机器级数据，互相串。

本入口只做三件事，不改任何业务代码：

1. 创建并锁定用户数据目录（可用 ``LIUHAO_DATA_DIR`` 覆盖，便于测试隔离）；
2. 首次生成并持久化机器级密钥（JWT 签名密钥、人类身份注册表完整性密钥），
   之后每次启动原样复用；
3. 先跑首次运行向导（已配置则秒退），再由 ``scripts/start_liuhao.py`` 拉起
   网关 + 驾驶舱并打开浏览器。

除 ``--skip-wizard`` / ``--console`` 外的参数全部原样转交给 ``start_liuhao.py``
（例如 ``--no-browser``、``--backend-only``、``--https``）。

首启默认走**图形化向导**（``scripts/first_run_gui.py``，tkinter）：不读 stdin，
双击 / 计划任务 / ``pythonw`` 都能完成；``--console`` 保留原命令行向导作为
无桌面会话时的回退。
"""
from __future__ import annotations

import contextlib
import os
import secrets
import shutil
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import TextIO

APP_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_ROOT))

sys.dont_write_bytecode = True

#: 运行期启动门禁：数据由更高 schema 写入时拒绝启动（实现见
#: ``src/upgrade/startup_guard.py``）。托盘壳与一键启动都经过
#: ``prepare_environment``，因此在这里接一次即可覆盖两条真实启动路径。
from src.upgrade.startup_guard import (  # noqa: E402
    ALLOW_ENV as DATA_DOWNGRADE_ENV,
    StartupDataBlocked,
    check_startup_data,
)

#: 用户数据根。测试/排障可用 ``LIUHAO_DATA_DIR`` 指到临时目录。
DATA_DIR_ENV = "LIUHAO_DATA_DIR"
DEFAULT_DATA_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "LiuHao" / "data"

#: 安装包里的驾驶舱构建产物（网关单端口直接服务它）。
CONSOLE_DIST = APP_ROOT / "console"

#: 应用级环境文件（JWT/会话密钥等）。位于数据目录，升级不覆盖。
APP_ENV_FILE = "config/app.env"

#: 人类身份注册表的 HMAC 签名密钥。内核在缺失时会**拒绝**加载任何已存身份
#: （fail-closed），所以必须落盘并在每次启动时注入。
INTEGRITY_KEY_FILE = "config/integrity.key"

#: 安装器写下的数据目录 marker（UTF-8 带 BOM）。优先于平台默认值：安装时
#: 用户可能把数据目录选在任意位置（含中文/空格），只有这里能还原。
DATA_DIR_MARKER = APP_ROOT / "data-dir.txt"


def _marker_data_dir() -> Path | None:
    """读取安装器写下的 ``data-dir.txt``，返回数据目录。

    编码规则（与 installer\\liuhao.iss 的写入端一一对应）：

    * 新版（0.1.1 起）：UTF-8 **带 BOM**。BOM 是"这是 UTF-8"的确证，避免把
      UTF-8 中文路径误按系统 ANSI（GBK/cp1252）解码成另一个目录。
    * 兼容旧版（0.1.0 及更早）：无 BOM 的系统 ANSI（中文系统即 GBK）。先用
      UTF-8 尝试；失败再按 ``mbcs`` 解码，与旧写入端一致。
    * 路径可能含中文、空格、``&``/``#``/``()`` 与 emoji：Python 的文件 API
      全程 Unicode，不做任何转义或再编码；只去掉行尾 CRLF 与首尾空白。

    解码后若仍出现替换字符（U+FFFD），说明文件已损坏、或写入端用了本机
    不支持的代码页——此时**不用**这个坏路径（指向错误目录比回落默认值更
    危险），返回 ``None`` 交给安全默认值。
    """
    try:
        raw = DATA_DIR_MARKER.read_bytes()
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


def _data_dir() -> Path:
    raw = (os.environ.get(DATA_DIR_ENV) or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    marker = _marker_data_dir()
    if marker is not None:
        return marker
    return DEFAULT_DATA_DIR.resolve()


def _app_version() -> str:
    """从随包的 ``pyproject.toml`` 读版本（安装目录自带，升级器也用它）。"""
    try:
        import tomllib

        with open(APP_ROOT / "pyproject.toml", "rb") as handle:
            return str(tomllib.load(handle)["project"]["version"])
    except Exception:
        return "unknown"


#: 启动器自己的日志。和网关日志（``logs/liuhao.log``）分开：这一份专门记录
#: "首启向导有没有跑完、卡在哪、为什么退出"，即使网关一次都没起来也要有。
LAUNCHER_LOG_NAME = "logs/launcher.log"

#: 启动器日志的绝对路径（``prepare_environment`` 成功后才有值）。
LAUNCHER_LOG: Path | None = None

#: 第一次接管 stdout/stderr 之前的原始流。首启向导换数据目录后会再次调用
#: :func:`prepare_environment`，必须从原始流重新包一层，否则会变成
#: Tee(Tee(console))，日志与窗口里的每行都重复两遍。
_RAW_STDOUT: TextIO | None = None
_RAW_STDERR: TextIO | None = None
_LOG_HANDLE: TextIO | None = None

#: 允许运维显式覆盖数据目录路径的开关（默认关闭）。
#:
#: 为什么默认关闭：安装版把身份登记表/凭据/审计库全部钉在数据目录，但
#: ``setdefault`` 会让**机器级**环境变量悄悄改写这些位置——实测一台机器上
#: 残留的 ``LIUHAO_HUMAN_IDENTITIES_DB`` 能把已安装产品指回开发仓库，
#: 结果向导以为"已有账号"、用户却一个都登不进去。必须显式 opt-in 才让
#: 外部变量覆盖，否则一律回到数据目录并打印被忽略的值。
ALLOW_ENV_OVERRIDE = "LIUHAO_ALLOW_ENV_OVERRIDE"


class _Tee:
    """把 stdout/stderr 同时写进启动器日志（行缓冲、逐行 flush）。

    ``isatty`` 透传真实终端，首启向导据此判断"有没有人能在窗口里输入"。
    """

    def __init__(self, stream: TextIO, log_file: TextIO) -> None:
        self._stream = stream
        self._log = log_file

    def write(self, data: str) -> int:
        self._stream.write(data)
        self._log.write(data)
        self._log.flush()
        return len(data)

    def flush(self) -> None:
        try:
            self._stream.flush()
        finally:
            self._log.flush()

    def isatty(self) -> bool:
        return bool(getattr(self._stream, "isatty", lambda: False)())

    @property
    def encoding(self) -> str:
        return getattr(self._stream, "encoding", "utf-8")

    def fileno(self) -> int:
        return self._stream.fileno()


def start_launcher_log(data_dir: Path) -> Path:
    """把本进程的 stdout/stderr 落盘到 ``<data>/logs/launcher.log``。

    在任何向导/网关代码之前调用：这样"向导没跑完""端口被占""子进程秒退"
    都会留下可查的文本，而不是用户双击后只有一闪而过的黑框。

    可重复调用（首启向导可能先选另一个数据目录）：每次都从**原始**流重新
    包一层，并换到新目录的日志文件，不会叠出双份输出。
    """
    global LAUNCHER_LOG, _RAW_STDOUT, _RAW_STDERR, _LOG_HANDLE
    if _RAW_STDOUT is None:
        _RAW_STDOUT = sys.stdout
    if _RAW_STDERR is None:
        _RAW_STDERR = sys.stderr
    if sys.stdout is None:  # pythonw / 无控制台的快捷方式
        _RAW_STDOUT = open(os.devnull, "w", encoding="utf-8")
        # 子进程（网关 / node）据此加 CREATE_NO_WINDOW 并把输出写进日志，
        # 否则从 pythonw 拉起 console 子系统的 python.exe 会再弹一个黑窗。
        os.environ["LIUHAO_HEADLESS"] = "1"
    if sys.stderr is None:
        sys.stderr = sys.stdout
    raw_out = _RAW_STDOUT if _RAW_STDOUT is not None else sys.stdout
    raw_err = _RAW_STDERR if _RAW_STDERR is not None else raw_out

    log_path = data_dir / LAUNCHER_LOG_NAME
    log_path.parent.mkdir(parents=True, exist_ok=True)
    # 有界 + 脱敏：launcher.log 会原样承接 argv、数据目录、子进程输出，
    # 无上限时一次长期驻留就能把磁盘写满，重定向发包还会带出本机用户名路径。
    from src.logging_config import RotatingTextWriter

    handle = RotatingTextWriter(log_path)
    if _LOG_HANDLE is not None:
        try:
            _LOG_HANDLE.close()
        except OSError:
            pass
    _LOG_HANDLE = handle
    handle.write(
        f"\n===== {datetime.now().isoformat(timespec='seconds')} launch =====\n"
    )
    handle.write(f"  python: {sys.executable}\n")
    handle.write(f"  version: {_app_version()}\n")
    handle.write(f"  argv:   {' '.join(sys.argv)}\n")
    handle.write(f"  data:   {data_dir}\n")
    handle.flush()
    sys.stdout = _Tee(raw_out, handle)
    sys.stderr = _Tee(raw_err, handle)
    LAUNCHER_LOG = log_path
    return log_path



def _ensure_layout(data_dir: Path) -> None:
    for rel in ("config", "workspace", "data", "logs", "data/observability"):
        (data_dir / rel).mkdir(parents=True, exist_ok=True)


#: 旧便携版（无安装器）把运行数据写在程序目录；升级到安装版时一次性搬走。
#: 只在目标不存在时搬，避免覆盖安装版已经产生的数据。
_LEGACY_FILES = (
    ("memory_store.db", "memory_store.db"),
    ("memory_store.db-wal", "memory_store.db-wal"),
    ("memory_store.db-shm", "memory_store.db-shm"),
    ("personal_context.db", "personal_context.db"),
    ("personal_context.db-wal", "personal_context.db-wal"),
    ("personal_context.db-shm", "personal_context.db-shm"),
    (".liuhao_jwt_revocation.db", "liuhao_jwt_revocation.db"),
    ("config/auth_secrets.json", "config/auth_secrets.json"),
    ("config/human_identities.json", "config/human_identities.json"),
)


def _merge_legacy_dir(source_dir: Path, dest_dir: Path) -> int:
    """把旧目录里的文件逐个搬到目标目录，目标已有时保守判断是否删重复。

    升级场景下数据目录可能已经存在（例如上次启动已经建过 observability），
    整目录比对会因「目标目录存在」而整批跳过，遗留文件就永远留在程序目录。
    这里按文件粒度处理：

    * 目标不存在 → 搬过去；
    * 目标存在、大小相同且目标更新 → 认定目标为准，删掉旧副本；
    * 其余情况（大小不同/旧副本更新）→ 保留，宁可留冗余也不丢数据。
    """
    moved = 0
    for entry in source_dir.rglob("*"):
        if not entry.is_file():
            continue
        destination = dest_dir / entry.relative_to(source_dir)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            shutil.move(str(entry), str(destination))
            moved += 1
            continue
        try:
            if (
                entry.stat().st_size == destination.stat().st_size
                and destination.stat().st_mtime >= entry.stat().st_mtime
            ):
                entry.unlink()
                moved += 1
        except OSError:
            pass
    for directory in sorted(source_dir.rglob("*"), reverse=True):
        if directory.is_dir():
            try:
                directory.rmdir()
            except OSError:
                pass
    return moved


def _migrate_legacy_state(data_dir: Path) -> None:
    """把旧便携版散落在程序目录里的用户数据搬进数据目录。

    这是升级安全的一部分：老用户重装后账号/对话/知识库还在，且不再污染
    程序目录。任何一步失败都不阻断启动（只提示）。
    """
    moved: list[str] = []
    for rel, dest_rel in _LEGACY_FILES:
        source = APP_ROOT / rel
        destination = data_dir / dest_rel
        if not source.exists():
            continue
        if not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(destination))
            moved.append(f"{rel} → {dest_rel}")
            continue
        # 目标已存在（上次启动已搬过）：大小一致且目标更新时，旧副本可直接删除，
        # 否则保留，宁可留冗余也不丢数据。
        try:
            if (
                source.stat().st_size == destination.stat().st_size
                and destination.stat().st_mtime >= source.stat().st_mtime
            ):
                source.unlink()
                moved.append(f"{rel}（重复）→ 已删除旧副本")
        except OSError:
            pass

    legacy_data = APP_ROOT / "data"
    if legacy_data.is_dir():
        count = _merge_legacy_dir(legacy_data, data_dir / "data")
        if count:
            moved.append(f"data/* → data/data/*（{count} 个文件）")

    legacy_logs = APP_ROOT / "logs"
    if legacy_logs.is_dir():
        count = _merge_legacy_dir(legacy_logs, data_dir / "logs")
        if count:
            moved.append(f"logs/* → logs/*（{count} 个文件）")

    legacy_env = APP_ROOT / ".env"
    app_env = data_dir / APP_ENV_FILE
    if legacy_env.is_file() and not app_env.exists():
        shutil.copy2(legacy_env, app_env)
        moved.append(".env → config/app.env")
        try:
            legacy_env.unlink()
        except OSError:
            pass

    for directory in (legacy_data, legacy_logs):
        try:
            directory.rmdir()
        except OSError:
            pass

    if moved:
        print(f"[migrate] 旧便携版数据已迁移 {len(moved)} 项：")
        for item in moved[:12]:
            print(f"          {item}")


def _load_dotenv_file(path: Path) -> None:
    """把数据目录里的 app.env 载入进程环境（已显式导出的变量优先）。"""
    if not path.is_file():
        return
    try:
        from dotenv import load_dotenv
    except ImportError:  # 运行时已声明 python-dotenv；缺失就退回手写解析
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())
        return
    load_dotenv(path, override=False)


def _ensure_app_env(path: Path) -> None:
    """首次运行时生成机器级密钥并落盘，之后原样复用。

    没有稳定的 JWT 密钥时，每个进程签发/校验自己的临时密钥：重启即全员
    登出，多 worker 时还会互不认账。生成一次并写进数据目录即可。

    ``APP_ENV`` 必须是 ``production``：安装版不是开发环境，用 ``staging`` 会
    在启动输出里打印「Debug API surface ENABLED」这类调试行，还会多开调试
    接口。旧版本已经写成 ``staging`` 的 app.env 在这里就地升级（其余行不动）。

    文件里有 JWT 签名密钥与 Flask ``SECRET_KEY``，写入/升级后立即收紧为
    “仅当前用户”：只靠父目录继承时，profile 目录上任何被授予读权限的主体
    都能拿到签名密钥。
    """
    if path.is_file():
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return
        upgraded = text.replace("APP_ENV=staging", "APP_ENV=production")
        if upgraded != text:
            with contextlib.suppress(OSError):
                path.write_text(upgraded, encoding="utf-8")
        _tighten_private_file(path)
        return
    body = (
        "# 由 LiuHao 安装版首次启动生成；升级不会覆盖本文件。\n"
        "# 修改后重启应用生效。\n"
        "APP_ENV=production\n"
        "LOG_LEVEL=info\n"
        f"LIUHAO_JWT_SECRET={secrets.token_urlsafe(48)}\n"
        f"SECRET_KEY={secrets.token_urlsafe(32)}\n"
    )
    path.write_text(body, encoding="utf-8")
    _tighten_private_file(path)


#: 安装版必须私有的文件（相对数据目录）。启动器写完/迁移完这些文件后立刻
#: 收紧 ACL；它们含 JWT 签名密钥、账号口令校验材料与模型 Key 的主密钥。
_PRIVATE_FILES = (
    APP_ENV_FILE,
    Path("config") / "auth_secrets.json",
    Path("data") / "model_config.key",
)


def _tighten_private_file(path: Path) -> bool:
    """把单个文件收紧为“仅当前用户”，并用读回的 DACL 复核。

    复核失败只告警、不阻断启动：这里不是凭据是否可用的判定点（模型 Key 的
    读写路径自身 fail-closed），而是把“本来应该私有”的文件纠正回来。告警
    必须带上路径，方便用户在权限异常时定位。
    """
    if not path.is_file():
        return True
    from src.common.windows_acl import acl_is_current_user_only, tighten_current_user_only

    tightened = tighten_current_user_only(path)
    verified = acl_is_current_user_only(path)
    if not tightened or not verified:
        print(
            f"[warn] 无法把 {path} 的 ACL 收紧为仅当前用户（读回复核失败）；"
            "该文件含敏感凭据，请检查父目录权限或以当前用户身份重新启动。",
            file=sys.stderr,
        )
        return False
    return True


def _tighten_private_files(data_dir: Path) -> None:
    """启动器落盘/迁移后统一收紧私有文件的 ACL（幂等）。"""
    for relative in _PRIVATE_FILES:
        _tighten_private_file(data_dir / relative)


def _ensure_integrity_key(path: Path) -> str:
    """读取或生成人类身份注册表签名密钥（实现见 ``src/common/integrity_key``）。

    三条路径（全新安装 / 旧数据升级 / 密钥丢失但身份库仍在）的判定与可读
    提示都在那里；这里只负责把数据目录位置传进去。
    """
    from src.common.integrity_key import ensure_integrity_key

    return ensure_integrity_key(data_dir=path.parent.parent)


def _env_override_allowed() -> bool:
    return (os.environ.get(ALLOW_ENV_OVERRIDE) or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _same_path(left: str, right: str) -> bool:
    try:
        return os.path.normcase(os.path.realpath(left)) == os.path.normcase(
            os.path.realpath(right)
        )
    except OSError:
        return False


def _pin_path(key: str, value: Path) -> None:
    """把 ``key`` 钉到数据目录；外部变量只有显式 opt-in 才能覆盖。"""
    target = str(value)
    current = (os.environ.get(key) or "").strip()
    if current and not _env_override_allowed():
        if not _same_path(current, target):
            print(
                f"[config] 忽略外部设置的 {key}={current}"
                f"（安装版固定使用数据目录），改用 {target}；"
                f"确需覆盖请设置 {ALLOW_ENV_OVERRIDE}=1。",
                file=sys.stderr,
            )
        os.environ[key] = target
        return
    if not current:
        os.environ[key] = target


def _drop_path_env(key: str) -> None:
    """清掉外部继承的路径变量（安装版不接受外部重定向，除非显式 opt-in）。"""
    current = (os.environ.get(key) or "").strip()
    if not current or _env_override_allowed():
        return
    print(
        f"[config] 忽略外部设置的 {key}={current}"
        f"（安装版固定使用数据目录）；"
        f"确需覆盖请设置 {ALLOW_ENV_OVERRIDE}=1。",
        file=sys.stderr,
    )
    os.environ.pop(key, None)


def data_path_env(data_dir: Path) -> dict[str, Path]:
    """数据目录 → 各持久化位置的环境变量（启动器与首启向导共用一份）。

    首启向导会在拉起网关**之前**写账号与模型，必须和启动器落到同一个目录，
    否则用户刚建的账号在网关里看不到。映射只在这里维护，避免两边漂移。
    """
    return {
        # 数据目录本身：子进程（网关 / 升级器 / 向导）必须与启动器解析到同一处，
        # 否则安装版升级会去 %LOCALAPPDATA% 找数据。
        "LIUHAO_DATA_DIR": data_dir,
        # 员工、目标、项目、工作区文件、JWT 吊销库
        "LIUHAO_WORKSPACE_ROOT": data_dir / "workspace",
        # 人类身份登记表 + 登录凭据
        "LIUHAO_HUMAN_IDENTITIES_FILE": data_dir / "config" / "human_identities.json",
        "LIUHAO_AUTH_SECRETS_FILE": data_dir / "config" / "auth_secrets.json",
        # 模型配置（API Key 以 AES-256-GCM 加密落盘）
        "LIUHAO_MODEL_CONFIG_DB": data_dir / "data" / "model_registry.sqlite3",
        "LIUHAO_MODEL_CONFIG_KEY_FILE": data_dir / "data" / "model_config.key",
        # 对话历史 / 审计证据 / JWT 吊销
        "CONVERSATION_DB_PATH": data_dir / "conversation_store.db",
        "AUDIT_DB_PATH": data_dir / "audit_store.db",
        "AUDIT_DB_BACKUP_PATH": data_dir / "audit_store.db.backup",
        "LIUHAO_JWT_REVOCATION_DB": data_dir / "liuhao_jwt_revocation.db",
        # 记忆 / 个人上下文 / 知识库向量 / 告警
        "MEMORY_DB_PATH": data_dir / "memory_store.db",
        "PERSONAL_CONTEXT_DB_PATH": data_dir / "personal_context.db",
        "LIUHAO_KNOWLEDGE_DB": data_dir / "data" / "knowledge_vectors.sqlite3",
        "LIUHAO_ALERTS_STORE_PATH": data_dir / "data" / "observability" / "alerts.json",
    }


def _configure_paths(data_dir: Path) -> None:
    """把内核与网关的持久化位置全部指向数据目录。

    默认**强制**落在数据目录：机器级/用户级残留的 ``LIUHAO_*`` 变量不得把
    已安装产品指回开发仓库（见 :data:`ALLOW_ENV_OVERRIDE`）。运维/测试若确实
    需要重定向，显式设置 ``LIUHAO_ALLOW_ENV_OVERRIDE=1`` 后原变量优先。
    """
    for key, value in data_path_env(data_dir).items():
        _pin_path(key, value)
    # 网关单端口模式需要显式指向驾驶舱产物，否则会去找仓库里的
    # apps/console/console/dist。
    if CONSOLE_DIST.is_dir():
        os.environ.setdefault("LIUHAO_CONSOLE_DIST", str(CONSOLE_DIST))


def _ensure_identity_registry(data_dir: Path) -> None:
    """首启先放一张空登记表。

    内核在登记表**不存在**时会往 stderr 打一句 "no human identity can
    approve"，首次运行的人看到会以为安装坏了。空表语义完全相同（没有可登录
    账号），但不会误导；向导随后会走"创建管理员账号"。
    """
    target = data_dir / "config" / "human_identities.json"
    if target.exists():
        return
    try:
        target.write_text('{\n  "humans": []\n}\n', encoding="utf-8")
    except OSError as exc:
        print(f"[warn] 无法初始化身份登记表 {target}: {exc}", file=sys.stderr)


def _ensure_logging_config(data_dir: Path) -> Path:
    """在数据目录生成运行时日志配置，把两个日志文件钉在数据目录。

    仓库里的 ``config/logging.yaml`` 用相对路径 ``logs/liuhao.log`` 与
    ``logs/error.log``。安装版若原样使用，日志会落进程序目录（卸载时被删、
    升级时被覆盖），所以复制一份并把路径替换为数据目录的绝对路径。
    """
    target = data_dir / "config" / "logging.yaml"
    template = APP_ROOT / "config" / "logging.yaml"
    if target.is_file() or not template.is_file():
        return target
    text = template.read_text(encoding="utf-8")
    # YAML 里绝对路径必须加引号：路径可能含空格、`#`（"a #1" 会被当成注释）、
    # `:` 等 YAML 元字符。这里统一转正斜杠（Windows API 同样接受），
    # 再包一层双引号，生成的是合法 YAML 标量而不是裸字符串。
    log_root = str(data_dir / "logs").replace("\\", "/").replace('"', '\\"')
    text = text.replace(
        "filename: logs/liuhao.log", f'filename: "{log_root}/liuhao.log"'
    ).replace(
        "filename: logs/error.log", f'filename: "{log_root}/error.log"'
    )
    target.write_text(text, encoding="utf-8")
    return target


class DataDirectoryUnavailable(RuntimeError):
    """数据目录不可写（只读、磁盘满、被占用）——fail-closed，不降级运行。"""


#: 数据目录里最深的运行期相对路径（``data/observability/alerts.jsonl`` 等）。
#: 数据目录不参与安装复制，但首次启动就会写到这些位置，同样受 Windows 260
#: 字符上限约束；安装器与启动器都用同一组常量判定（实测 255 字符的数据目录
#: 在 mkdir config 时就报 WinError 206）。
DATA_REL_MAX = 30
MAX_PATH_LIMIT = 259

#: Win32 设备名：`CON`/`NUL` 这类目录 CreateDir 报英文错误或"假成功"，
#: 之后建子目录才失败，必须提前用中文拒绝。
_RESERVED_DEVICE_NAMES = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"COM{i}" for i in range(1, 10)]
    + [f"LPT{i}" for i in range(1, 10)]
)


def _path_name_issue(path: Path) -> str | None:
    """返回路径里第一处 Windows 不可用的目录名说明；全部合法时返回 None。

    只检查**目录段**（不检查文件名）：用户能把数据目录选成任意位置，
    保留设备名与以点/空格结尾的名字在 Win32 层有别名语义，安装器记下的
    路径可能和文件实际落点不一致。
    """
    for part in path.parts[1:]:  # parts[0] 是盘符根（如 D:\）
        if not part or part in ("\\", "/"):
            continue
        if part.endswith(".") or part.endswith(" "):
            return (
                f"「{part}」以点或空格结尾，Windows 会自动去掉结尾字符，"
                "文件实际落在另一个名字下，记录的路径会对不上。"
            )
        stem = part.split(".", 1)[0].upper()
        if stem in _RESERVED_DEVICE_NAMES:
            return (
                f"「{part}」是 Windows 保留设备名（{stem}），不能用作目录名。"
            )
    return None


def prepare_environment() -> tuple[Path, Path]:
    """准备安装版运行环境（幂等，可被多个入口重复调用）。

    返回 ``(数据目录, 运行时日志配置)``。安装版所有入口（一键启动与托盘自启）
    都必须先走这里，否则 ``src.desktop`` 会把数据写进程序目录。
    """
    # 程序目录视为只读：不往里面写 __pycache__（子进程继承该变量）。
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    sys.dont_write_bytecode = True

    data_dir = _data_dir()

    # 路径本身不可用（保留设备名 / 尾点尾空格 / 超长）：必须在任何写入之前
    # 用中文拒绝。这类名字在 Win32 层要么报英文错误、要么"假成功"后建子目录
    # 才失败，裸 traceback 对用户没有可行动信息。
    name_issue = _path_name_issue(data_dir)
    if name_issue:
        raise DataDirectoryUnavailable(
            f"数据目录不可用：{data_dir}。" + name_issue
            + f"请改用普通目录名，或用环境变量 {DATA_DIR_ENV} 指向另一个目录。"
        )
    if len(str(data_dir)) + 1 + DATA_REL_MAX > MAX_PATH_LIMIT:
        raise DataDirectoryUnavailable(
            f"数据目录路径过长：{data_dir}（共 {len(str(data_dir))} 个字符，"
            f"内部最深文件还要再占用 {DATA_REL_MAX} 个字符）。"
            "Windows 单条路径上限为 "
            f"{MAX_PATH_LIMIT + 1} 个字符，首次启动就无法写入。"
            f"请改用更短的目录（例如 D:\\LiuHaoData），或用环境变量 {DATA_DIR_ENV} 指定。"
        )

    # 启动门禁：数据目录由更高 schema 写入时拒绝启动。必须跑在任何写入
    # （目录、app.env、密钥、日志）之前——拒绝时要保证"什么都没改"。
    data_gate = check_startup_data(data_dir, app_root=APP_ROOT)
    if not data_gate.ok:
        # 拒绝说明由 main() 统一打印（避免同一段文字出现两遍）。
        raise StartupDataBlocked(data_gate)
    if data_gate.message:
        print(data_gate.message, file=sys.stderr)

    # 首次启动要在这里写目录、密钥、日志配置。数据目录只读/磁盘满/被别的
    # 进程独占时，裸 OSError 会把用户甩到一段 traceback 上；这里统一转成
    # 可读原因并**拒绝继续启动**（fail-closed）——半套配置启动只会更糟。
    try:
        _ensure_layout(data_dir)
    except OSError as exc:
        raise DataDirectoryUnavailable(
            f"无法创建数据目录 {data_dir}（{type(exc).__name__}: {exc}）。"
            "请确认磁盘没有写满、路径没有被只读挂载，"
            f"或用环境变量 {DATA_DIR_ENV} 指向另一个可写目录。"
        ) from exc

    # 目录"存在"不等于"可写"：父目录被 ACL 拒绝、设了只读或磁盘满时，
    # mkdir(exist_ok=True) 会成功，真正写 app.env/密钥时才炸。这里先在数据
    # 目录本身写一个探针文件，把失败提前成一条中文可行动提示（fail-closed）。
    probe = data_dir / ".liuhao-write-probe.tmp"
    try:
        probe.write_text("probe", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise DataDirectoryUnavailable(
            f"数据目录不可写：{data_dir}（{type(exc).__name__}: {exc}）。"
            "请确认磁盘没有写满、目录没有被设为只读、也没有被其它进程独占；"
            f"或用环境变量 {DATA_DIR_ENV} 指向另一个可写目录。"
        ) from exc

    # 旧便携版数据搬运是**尽力而为**：某个旧文件正被其它程序打开时，跳过它、
    # 继续启动（数据还在原处，下次再搬）。这不属于"数据目录不可写"。
    try:
        _migrate_legacy_state(data_dir)
    except OSError as exc:
        print(f"[warn] 旧数据迁移未完成（不会阻断启动）：{exc}", file=sys.stderr)

    # 目录一建好就把 stdout/stderr 落盘：后面向导怎么死、端口怎么冲突，
    # 用户都能在 <数据目录>/logs/launcher.log 里看到。
    #
    # 日志写不进去（logs 目录只读/被独占/磁盘满）不是致命错误：下面写
    # app.env / 完整性密钥时同样会失败，并统一转成中文可行动提示；先在这里
    # 只给一行警告，避免用户先看到一段 PermissionError 栈。
    try:
        start_launcher_log(data_dir)
    except OSError as exc:
        print(
            f"[warn] 无法写入启动日志 {data_dir / LAUNCHER_LOG_NAME}"
            f"（{type(exc).__name__}: {exc}），继续尝试启动……",
            file=sys.stderr,
        )

    try:
        app_env = data_dir / APP_ENV_FILE
        _ensure_app_env(app_env)
        _load_dotenv_file(app_env)

        integrity_key = _ensure_integrity_key(data_dir / INTEGRITY_KEY_FILE)
    except OSError as exc:
        raise DataDirectoryUnavailable(
            f"数据目录不可写：{data_dir}（{type(exc).__name__}: {exc}）。"
            "请确认磁盘没有写满、目录没有被设为只读、也没有被其它进程独占；"
            f"或用环境变量 {DATA_DIR_ENV} 指向另一个可写目录。"
        ) from exc

    inherited_key = (os.environ.get("LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY") or "").strip()
    if inherited_key and inherited_key != integrity_key and not _env_override_allowed():
        print(
            "[config] 忽略外部设置的 LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY"
            f"（与数据目录 {data_dir / INTEGRITY_KEY_FILE} 内的密钥不一致），"
            f"改用数据目录密钥；确需覆盖请设置 {ALLOW_ENV_OVERRIDE}=1。",
            file=sys.stderr,
        )
    if not inherited_key or not _env_override_allowed():
        os.environ["LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY"] = integrity_key
    os.environ.setdefault("APP_ENV", "production")
    os.environ.setdefault("LOG_LEVEL", "info")

    _configure_paths(data_dir)
    _ensure_identity_registry(data_dir)
    # 迁移/首次启动可能已经生成了 app.env、auth_secrets.json 与模型主密钥；
    # 这里统一再收紧一次（幂等），确保启动子进程前权限已经落到当前用户。
    _tighten_private_files(data_dir)
    try:
        logging_config = _ensure_logging_config(data_dir)
    except OSError as exc:
        raise DataDirectoryUnavailable(
            f"无法写入运行日志配置：{data_dir / 'config' / 'logging.yaml'}（{exc}）"
        ) from exc

    backend = (os.environ.get("LIUHAO_HUMAN_IDENTITIES_BACKEND") or "").strip().lower()
    if backend == "sqlite":
        # 该后端下 DB 路径优先于 JSON 登记表：必须钉住，否则机器上残留的
        # LIUHAO_HUMAN_IDENTITIES_DB 会让向导去读开发仓库里的身份库。
        _pin_path(
            "LIUHAO_HUMAN_IDENTITIES_DB",
            data_dir / "config" / "human_identities.sqlite3",
        )
    else:
        # 未显式选后端时，内核只要看到 DB 路径就会切到 sqlite（"配置了数据库
        # 路径 = 明确意图"）。安装版默认是 JSON 登记表，因此继承来的 DB 路径
        # 必须清掉——既防止误切后端，也防止指向程序目录/开发仓库。
        _drop_path_env("LIUHAO_HUMAN_IDENTITIES_DB")

    # uvicorn 子进程以程序目录为 cwd 启动时，`-m src.gateway.main` 需要能
    # 找到 src；显式补 PYTHONPATH，避免依赖调用方的环境。
    python_path = os.pathsep.join(
        [str(APP_ROOT), os.environ.get("PYTHONPATH", "")]
    ).strip(os.pathsep)
    os.environ["PYTHONPATH"] = python_path

    # 托盘壳拉起网关子进程时用它，避免解析到开发机的 .venv 或裸 python。
    installed_python = APP_ROOT / "runtime" / "python.exe"
    if installed_python.is_file():
        os.environ.setdefault("LIUHAO_DESKTOP_PYTHON", str(installed_python))

    return data_dir, logging_config


def _append_log(path: Path, text: str) -> None:
    """把文本追加进日志；日志自身不可写时静默跳过（不能再掩盖原始错误）。

    ``launcher.log`` 由 :class:`RotatingTextWriter` 承接时已自带轮转与脱敏；
    兜底的 ``bootstrap.log`` 用更小的上限（1 MiB × 2），避免反复失败累积。
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        from src.logging_config import RotatingTextWriter

        RotatingTextWriter(path, maxBytes=1024 * 1024, backupCount=2).write(text)
    except Exception:  # noqa: BLE001 - 兜底日志失败时保持原始异常可见
        pass


def _fallback_log_path() -> Path:
    """兜底日志路径：正常时是数据目录的 launcher.log，取不到就退到 bootstrap.log。"""
    if LAUNCHER_LOG is not None:
        return LAUNCHER_LOG
    try:
        return _data_dir() / LAUNCHER_LOG_NAME
    except Exception:  # noqa: BLE001 - 连数据目录都解析不了时只能写程序目录
        return APP_ROOT / "bootstrap.log"


def report_launch_failure(exc: BaseException) -> int:
    """把「安装不完整 / 启动失败」翻译成中文与下一步可执行动作。

    用户看到的必须是一条能复制执行的命令，而不是十几行英文堆栈；堆栈只进
    launcher.log 与 bootstrap.log，供支持人员定位。
    """
    log_path = _fallback_log_path()
    bootstrap = APP_ROOT / "bootstrap.log"
    message = (
        "[FAIL] 安装文件不完整，请重新安装或修复安装。\n"
        f"       原因：{type(exc).__name__}: {exc}\n"
        "       修复：重新运行安装包，选择「修复安装」；或重新安装到同一位置"
        "（账号与数据会保留，不会被覆盖）。\n"
        f'       命令："{APP_ROOT / "LiuHao.bat"}"\n'
        f"       日志：{log_path}\n"
    )
    print(message, file=sys.stderr)
    detail = message + "".join(
        traceback.format_exception(type(exc), exc, exc.__traceback__)
    )
    _append_log(log_path, detail)
    if bootstrap != log_path:
        _append_log(bootstrap, detail)
    return 1


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)

    # 显式降级开关：转成环境变量，网关/排程等子进程自动继承。
    if "--allow-data-downgrade" in args:
        os.environ[DATA_DOWNGRADE_ENV] = "1"

    try:
        data_dir, logging_config = prepare_environment()
    except StartupDataBlocked as exc:
        # 直接打印完整中文说明（已含备份路径与放行/升级命令），不套"安装不完整"。
        print(exc.message, file=sys.stderr)
        print("       已停止启动，未写入任何运行数据。", file=sys.stderr)
        return 4
    except DataDirectoryUnavailable as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        print("        已停止启动，未写入任何运行数据。", file=sys.stderr)
        return 3
    except Exception as exc:  # noqa: BLE001 - 缺 src/runtime 等安装不完整场景
        # 兜底必须覆盖 prepare_environment：缺模块最早就在这里抛，早于此处的
        # try 只会把英文堆栈原样甩给用户。
        return report_launch_failure(exc)

    # 任何未捕获异常都必须：①打印给人看 ②落进 launcher.log ③返回非零。
    # 这条兜底存在的理由就是现场那次"双击之后什么都没有"。
    try:
        return _run(data_dir, logging_config, args)
    except KeyboardInterrupt:
        print()
        print("[warn] 已取消启动（Ctrl+C）。", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001 - 启动器必须兜住一切
        return report_launch_failure(exc)


def _arg_api_port(args: list[str]) -> int:
    """从转交参数里取 ``--api-port``，让首启预检用真实端口而不是硬编码 8080。"""
    for index, item in enumerate(args):
        if item == "--api-port" and index + 1 < len(args):
            try:
                return int(args[index + 1])
            except ValueError:
                break
        if item.startswith("--api-port="):
            try:
                return int(item.split("=", 1)[1])
            except ValueError:
                break
    try:
        return int((os.environ.get("LIUHAO_API_PORT") or "").strip() or 8080)
    except ValueError:
        return 8080


def _forwarded_with_port(args: list[str], port: int) -> list[str]:
    """把 ``--api-port`` 换成用户刚选的新端口（其余参数原样保留）。"""
    cleaned: list[str] = []
    index = 0
    while index < len(args):
        item = args[index]
        if item == "--api-port":
            index += 2  # 跳过旧的 --api-port 值
            continue
        if item.startswith("--api-port="):
            index += 1
            continue
        cleaned.append(item)
        index += 1
    return [*cleaned, "--api-port", str(port)]


def _run(data_dir: Path, logging_config: Path, args: list[str]) -> int:
    # 图形化首启是默认路径：命令行窗口不再要求输入账号/密码。``--console``
    # 保留原命令行向导，供无桌面会话（计划任务、远程 shell）的排障场景。
    console_wizard = "--console" in args
    skip_wizard = "--skip-wizard" in args
    forwarded = [
        a
        for a in args
        if a not in ("--skip-wizard", "--console", "--allow-data-downgrade")
    ]

    print("正在启动…（无需在此窗口输入任何内容，浏览器会自动打开）")

    if console_wizard and not skip_wizard:
        import scripts.first_run_wizard as wizard

        # 命令行向导必须在**能输入**的窗口里完成。无控制台时（pythonw、
        # 计划任务、隐藏窗口）不要抛 EOFError，也不要假装启动成功。
        if wizard.needs_setup() and not wizard.stdin_is_interactive():
            print("[FAIL] 首次运行需要在命令行窗口里完成向导，但当前没有可交互的终端。")
            print("       请从开始菜单/桌面快捷方式启动（会打开命令行窗口），")
            print("       或在「命令提示符」里执行：")
            print(f'       "{APP_ROOT / "LiuHao.bat"}" --console')
            print(f"       日志：{LAUNCHER_LOG}")
            return 2

        if wizard.run_wizard() != 0:
            print(
                "[FAIL] 首次运行向导未通过，已停止启动（不会打开控制台）。",
                file=sys.stderr,
            )
            print(f"       原因见窗口输出与日志：{LAUNCHER_LOG}", file=sys.stderr)
            return 1

        # 向导"跑完"不等于"能登录"：没有可用账号就不放行，否则用户拿到的是
        # 一个能打开但永远登不进去的页面。
        if not wizard._has_loginable_human():
            print(
                "[FAIL] 向导结束后仍没有可登录的账号，已停止启动。",
                file=sys.stderr,
            )
            print(
                "       请重跑向导，或在程序目录下执行：",
                file=sys.stderr,
            )
            print(
                "       runtime\\python.exe scripts\\register_human_identity.py "
                "--principal <name> --password-stdin",
                file=sys.stderr,
            )
            print(f"       日志：{LAUNCHER_LOG}", file=sys.stderr)
            return 3
    else:
        import scripts.first_run_gui as gui

        if skip_wizard:
            print("[skip] 已按 --skip-wizard 跳过首启检查。")
        elif gui.needs_setup():
            # 首启未完成前不暴露任何控制台地址：先删旧地址文件，再开窗口。
            gui.clear_current_url(data_dir)
            print(
                "首次运行：正在打开图形化设置向导"
                "（数据目录 → 管理员账号 → 选择模型 → 完成前请确认 → 自检结果，共五页）…"
            )
            code = gui.run_wizard(port=_arg_api_port(forwarded))
            if code != 0:
                print(
                    "[FAIL] 首次设置未完成，已停止启动（不会打开控制台）。",
                    file=sys.stderr,
                )
                print(f"       日志：{gui.focus_log_path(data_dir)}", file=sys.stderr)
                return code

            # 用户在向导里可能换了数据目录：重新按新目录准备环境与日志，
            # 否则网关会去旧目录找刚创建的账号。
            chosen = gui.chosen_data_dir()
            if chosen is not None and not _same_path(str(chosen), str(data_dir)):
                data_dir, logging_config = prepare_environment()

            # 双保险：只有真的存在可登录管理员才放行网关。
            if gui.needs_setup():
                print(
                    "[FAIL] 设置结束后仍没有可登录的管理员账号，已停止启动。",
                    file=sys.stderr,
                )
                print(f"       日志：{gui.focus_log_path(data_dir)}", file=sys.stderr)
                return 3
            print(f"已初始化：数据目录 {data_dir}")
            print("浏览器会在服务就绪后自动打开。")
        else:
            print("已初始化：浏览器会自动打开登录页。")

        # 端口冲突的处理统一由 start_liuhao 负责：有桌面就弹「关掉占用程序再试 /
        # 换一个端口继续」，没有桌面就打印明确中文。这里只把向导里选过的端口
        # 透传过去（绝不自动顺延）。
        chosen_port = gui.chosen_api_port()
        if chosen_port is not None:
            forwarded = _forwarded_with_port(forwarded, chosen_port)
        print(f"日志：{LAUNCHER_LOG}")

    import scripts.start_liuhao as launcher

    # 只把「日志 / 子进程 cwd」指向数据目录。``launcher.ROOT`` 必须保持程序目录：
    # ``-m src.gateway`` 的模块导入、``runtime/python.exe``、``console/`` 都靠它。
    # （此前把 ROOT 改成数据目录，子进程 cwd/PYTHONPATH 跟着变成数据目录，
    #  网关一启动就 ``No module named src`` —— 这是安装版"永远起不来"的隐藏根因。）
    launcher.LOG_CONFIG = logging_config
    launcher.LOG_DIR = data_dir / "logs"
    launcher.GATEWAY_CWD = data_dir

    sys.argv = [str(APP_ROOT / "scripts" / "start_liuhao.py"), *forwarded]
    return launcher.main()


if __name__ == "__main__":
    raise SystemExit(main())
