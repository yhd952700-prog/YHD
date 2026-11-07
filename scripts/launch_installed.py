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

除 ``--skip-wizard`` 外的参数全部原样转交给 ``start_liuhao.py``
（例如 ``--no-browser``、``--backend-only``、``--https``）。
"""
from __future__ import annotations

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

# 程序目录视为只读：本进程启动后再导入的模块（start_liuhao / src.desktop /
# logging_config 等）一律不落 __pycache__。环境变量只对子进程生效，当前解释器
# 的字节码开关必须在导入前就地关掉——否则首启后程序目录会多出若干 .pyc。
sys.dont_write_bytecode = True

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


def _data_dir() -> Path:
    raw = (os.environ.get(DATA_DIR_ENV) or "").strip()
    return Path(raw).expanduser().resolve() if raw else DEFAULT_DATA_DIR.resolve()


#: 启动器自己的日志。和网关日志（``logs/liuhao.log``）分开：这一份专门记录
#: "首启向导有没有跑完、卡在哪、为什么退出"，即使网关一次都没起来也要有。
LAUNCHER_LOG_NAME = "logs/launcher.log"

#: 启动器日志的绝对路径（``prepare_environment`` 成功后才有值）。
LAUNCHER_LOG: Path | None = None

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
    """
    global LAUNCHER_LOG
    log_path = data_dir / LAUNCHER_LOG_NAME
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = log_path.open("a", encoding="utf-8", buffering=1)
    handle.write(
        f"\n===== {datetime.now().isoformat(timespec='seconds')} launch =====\n"
    )
    handle.write(f"  python: {sys.executable}\n")
    handle.write(f"  argv:   {' '.join(sys.argv)}\n")
    handle.write(f"  data:   {data_dir}\n")
    handle.flush()
    if sys.stdout is None:  # pythonw / 无控制台的快捷方式
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = sys.stdout
    sys.stdout = _Tee(sys.stdout, handle)
    sys.stderr = _Tee(sys.stderr, handle)
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
    """
    if path.is_file():
        return
    body = (
        "# 由 LiuHao 安装版首次启动生成；升级不会覆盖本文件。\n"
        "# 修改后重启应用生效。\n"
        "APP_ENV=staging\n"
        "LOG_LEVEL=info\n"
        f"LIUHAO_JWT_SECRET={secrets.token_urlsafe(48)}\n"
        f"SECRET_KEY={secrets.token_urlsafe(32)}\n"
    )
    path.write_text(body, encoding="utf-8")


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


def _configure_paths(data_dir: Path) -> None:
    """把内核与网关的持久化位置全部指向数据目录。

    默认**强制**落在数据目录：机器级/用户级残留的 ``LIUHAO_*`` 变量不得把
    已安装产品指回开发仓库（见 :data:`ALLOW_ENV_OVERRIDE`）。运维/测试若确实
    需要重定向，显式设置 ``LIUHAO_ALLOW_ENV_OVERRIDE=1`` 后原变量优先。
    """
    paths = {
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
    for key, value in paths.items():
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
    log_root = str(data_dir / "logs").replace("\\", "/")
    text = text.replace(
        "filename: logs/liuhao.log", f"filename: {log_root}/liuhao.log"
    ).replace(
        "filename: logs/error.log", f"filename: {log_root}/error.log"
    )
    target.write_text(text, encoding="utf-8")
    return target


class DataDirectoryUnavailable(RuntimeError):
    """数据目录不可写（只读、磁盘满、被占用）——fail-closed，不降级运行。"""


def prepare_environment() -> tuple[Path, Path]:
    """准备安装版运行环境（幂等，可被多个入口重复调用）。

    返回 ``(数据目录, 运行时日志配置)``。安装版所有入口（一键启动与托盘自启）
    都必须先走这里，否则 ``src.desktop`` 会把数据写进程序目录。
    """
    # 程序目录视为只读：不往里面写 __pycache__（子进程继承该变量）。
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    sys.dont_write_bytecode = True

    data_dir = _data_dir()

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

    # 旧便携版数据搬运是**尽力而为**：某个旧文件正被其它程序打开时，跳过它、
    # 继续启动（数据还在原处，下次再搬）。这不属于"数据目录不可写"。
    try:
        _migrate_legacy_state(data_dir)
    except OSError as exc:
        print(f"[warn] 旧数据迁移未完成（不会阻断启动）：{exc}", file=sys.stderr)

    # 目录一建好就把 stdout/stderr 落盘：后面向导怎么死、端口怎么冲突，
    # 用户都能在 <数据目录>/logs/launcher.log 里看到。
    start_launcher_log(data_dir)

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
    os.environ.setdefault("APP_ENV", "staging")
    os.environ.setdefault("LOG_LEVEL", "info")

    _configure_paths(data_dir)
    _ensure_identity_registry(data_dir)
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


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)

    try:
        data_dir, logging_config = prepare_environment()
    except DataDirectoryUnavailable as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        print("        已停止启动，未写入任何运行数据。", file=sys.stderr)
        return 3

    # 任何未捕获异常都必须：①打印给人看 ②落进 launcher.log ③返回非零。
    # 这条兜底存在的理由就是现场那次"双击之后什么都没有"。
    try:
        return _run(data_dir, logging_config, args)
    except KeyboardInterrupt:
        print()
        print("[warn] 已取消启动（Ctrl+C）。", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001 - 启动器必须兜住一切
        traceback.print_exc()
        print(f"[FAIL] 启动失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        print(f"        完整日志：{LAUNCHER_LOG}", file=sys.stderr)
        return 1


def _run(data_dir: Path, logging_config: Path, args: list[str]) -> int:
    skip_wizard = "--skip-wizard" in args
    forwarded = [a for a in args if a != "--skip-wizard"]

    print("=" * 62)
    print("鎏灏 LiuHao AI OS — 安装版")
    print(f"  程序目录  {APP_ROOT}")
    print(f"  数据目录  {data_dir}")
    print("=" * 62)

    # 首次启动（或身份/模型缺失时）进入向导；已配置则自动跳过。
    if not skip_wizard:
        import scripts.first_run_wizard as wizard

        # 首启必须在**能输入**的窗口里完成。无控制台时（pythonw、计划任务、
        # 隐藏窗口）不要抛 EOFError，也不要假装启动成功：直接说清楚从哪启动。
        if wizard.needs_setup() and not wizard.stdin_is_interactive():
            print("[FAIL] 首次运行需要在命令行窗口里完成向导，但当前没有可交互的终端。")
            print("       请从开始菜单/桌面快捷方式启动（会打开命令行窗口），")
            print("       或在「命令提示符」里执行：")
            print(f'       "{APP_ROOT / "LiuHao.bat"}"')
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
