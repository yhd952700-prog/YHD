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
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_ROOT))

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
    """读取或生成人类身份注册表签名密钥。"""
    existing = (os.environ.get("LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY") or "").strip()
    if existing:
        return existing
    if path.is_file():
        key = path.read_text(encoding="utf-8").strip()
        if key:
            return key
    key = secrets.token_hex(32)
    path.write_text(key + "\n", encoding="utf-8")
    return key


def _configure_paths(data_dir: Path) -> None:
    """把内核与网关的持久化位置全部指向数据目录。

    全部用 ``setdefault``：运维/测试显式导出的变量永远优先。
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
        os.environ.setdefault(key, str(value))
    # 网关单端口模式需要显式指向驾驶舱产物，否则会去找仓库里的
    # apps/console/console/dist。
    if CONSOLE_DIST.is_dir():
        os.environ.setdefault("LIUHAO_CONSOLE_DIST", str(CONSOLE_DIST))


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


def prepare_environment() -> tuple[Path, Path]:
    """准备安装版运行环境（幂等，可被多个入口重复调用）。

    返回 ``(数据目录, 运行时日志配置)``。安装版所有入口（一键启动与托盘自启）
    都必须先走这里，否则 ``src.desktop`` 会把数据写进程序目录。
    """
    # 程序目录视为只读：不往里面写 __pycache__（子进程继承该变量）。
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")

    data_dir = _data_dir()
    _ensure_layout(data_dir)
    _migrate_legacy_state(data_dir)

    app_env = data_dir / APP_ENV_FILE
    _ensure_app_env(app_env)
    _load_dotenv_file(app_env)

    integrity_key = _ensure_integrity_key(data_dir / INTEGRITY_KEY_FILE)
    os.environ.setdefault("LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY", integrity_key)
    os.environ.setdefault("APP_ENV", "staging")
    os.environ.setdefault("LOG_LEVEL", "info")

    _configure_paths(data_dir)
    logging_config = _ensure_logging_config(data_dir)

    # 身份内核在 ``sqlite`` 后端且未显式给 DB 路径时，默认写到程序目录的
    # ``data/human_identities.sqlite3``（源码里以仓库根为锚点）。安装版必须
    # 把它落到用户数据目录；只在用户显式选了 sqlite 后端时才补，避免因为
    # “设置 DB 路径”反而把 JSON 后端切成 sqlite（见内核的解析顺序）。
    if (os.environ.get("LIUHAO_HUMAN_IDENTITIES_BACKEND") or "").strip().lower() == "sqlite":
        os.environ.setdefault(
            "LIUHAO_HUMAN_IDENTITIES_DB",
            str(data_dir / "config" / "human_identities.sqlite3"),
        )

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
    skip_wizard = "--skip-wizard" in args
    forwarded = [a for a in args if a != "--skip-wizard"]

    data_dir, logging_config = prepare_environment()

    print("=" * 62)
    print("鎏灏 LiuHao AI OS — 安装版")
    print(f"  程序目录  {APP_ROOT}")
    print(f"  数据目录  {data_dir}")
    print("=" * 62)

    # 首次启动（或身份/模型缺失时）进入向导；已配置则自动跳过。
    if not skip_wizard:
        import scripts.first_run_wizard as wizard

        if wizard.run_wizard() != 0:
            print("[FAIL] 首次运行向导未通过，已停止启动。", file=sys.stderr)
            return 1

    import scripts.start_liuhao as launcher

    # 让启动器把工作目录/日志/子进程 cwd 全部指向数据目录：网关代码里凡是
    # 写死的相对路径（如 data/observability/alerts.json、logs/）都会落到
    # 用户数据目录，而不是安装目录。
    launcher.ROOT = data_dir
    launcher.LOG_CONFIG = logging_config
    launcher.LOG_DIR = data_dir / "logs"

    sys.argv = [str(APP_ROOT / "scripts" / "start_liuhao.py"), *forwarded]
    return launcher.main()


if __name__ == "__main__":
    raise SystemExit(main())
