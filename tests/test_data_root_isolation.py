"""代码根零新增：只设 ``LIUHAO_DATA_DIR`` 时，运行时不得污染仓库目录。

背景见 ``docs/test-isolation-gap.md``：七个存储各自只认一部分环境变量（会话库只认
``CONVERSATION_DB_PATH``、员工/目标只认 ``LIUHAO_WORKSPACE_ROOT`` …），一次冒烟就会把
``conversation_store.db``、``.liuhao_goals.json`` 写进共享开发目录。修完之后还需要一条
**结构性**断言，否则下一个新存储又会悄悄写回代码根。

两条用例分工：

* ``test_stores_follow_the_data_root`` —— 逐项证明身份/认证/审计/告警/模型注册/隐私/
  情报/ORM 默认库都解析到数据根（不启动网关，失败信息直接指出是哪个存储漏了）；
* ``test_full_flow_leaves_the_code_root_untouched`` —— 真起网关，发消息 / 建员工 /
  建目标，比对代码根快照，证明没有新增或改写任何运行时文件。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

#: 历史上真的写进代码根的运行时文件（见 docs/test-isolation-gap.md 的实测段）。
WATCHED = (
    "conversation_store.db",
    "memory_store.db",
    "personal_context.db",
    ".liuhao_employees.json",
    ".liuhao_goals.json",
    ".planner_state.json",
    ".liuhao_projects.json",
    ".liuhao_execution.journal",
    ".liuhao_artifacts.db",
    ".liuhao_jwt_revocation.db",
    "liuhao_ai_os.db",
    "account_settings.json",
    "audit_store.db",
    "auth_secrets.json",
    "identities.json",
    "alerts.json",
    "model_registry.json",
    "privacy_settings.json",
)

#: pytest / 解释器自己会产生、且与运行时存储无关的顶层项。
IGNORED_TOP_LEVEL = {"__pycache__", ".pytest_cache", "htmlcov", ".coverage"}


def _top_level() -> set[str]:
    return {p.name for p in REPO_ROOT.iterdir() if p.name not in IGNORED_TOP_LEVEL}


def _watched_stamps() -> dict[str, int]:
    stamps: dict[str, int] = {}
    for name in WATCHED:
        path = REPO_ROOT / name
        if path.is_file():
            stamps[name] = path.stat().st_mtime_ns
    return stamps


def _plugin_files() -> set[str]:
    directory = REPO_ROOT / "plugins"
    return {p.name for p in directory.glob("*.json")} if directory.is_dir() else set()


def _under(path: os.PathLike | str, root: Path) -> bool:
    return Path(path).resolve().is_relative_to(root.resolve())


@pytest.fixture
def client():
    """真实 lifespan 的网关客户端（与 test_ai_management_api.py 同构）。"""
    from fastapi.testclient import TestClient

    from src.gateway.main import get_app

    with TestClient(get_app()) as test_client:
        yield test_client


def test_stores_follow_the_data_root(monkeypatch, tmp_path):
    """每一个"有默认落盘位置"的存储都必须服从 ``LIUHAO_DATA_DIR``。"""
    data = tmp_path / "data"
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("LIUHAO_DATA_DIR", str(data))
    # 队列/主密钥的旧位置是 ``~/.liuhao``，隔离掉真实用户目录，避免用例把开发机上的
    # 真实队列复制进临时数据根。
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    # conftest 为并行隔离显式设过这些变量；这里统一清掉，才能证明"只设数据根"也够。
    for var in (
        "LIUHAO_WORKSPACE_ROOT",
        "AUDIT_DB_PATH",
        "AUDIT_KERNEL_HWM_PATH",
        "AUDIT_HWM_PATH",
        "LIUHAO_MASTER_KEY_FILE",
        "LIUHAO_TASK_DB",
        "LIUHAO_TASK_JOURNAL",
        "LIUHAO_REMINDERS_DB",
        "LIUHAO_REMINDER_OUTBOX",
        "LIUHAO_ALERTS_STORE_PATH",
        "LIUHAO_HUMAN_IDENTITIES_FILE",
        "LIUHAO_HUMAN_IDENTITIES_DB",
        "LIUHAO_HUMAN_IDENTITIES_BACKEND",
        "LIUHAO_AUTH_SECRETS_FILE",
        "LIUHAO_JWT_REVOCATION_DB",
        "LIUHAO_MODEL_REGISTRY_FILE",
        "LIUHAO_PRIVACY_SETTINGS_FILE",
        "LIUHAO_ACCOUNT_SETTINGS_FILE",
        "DATABASE_URL",
        "OSI_DB_PATH",
    ):
        monkeypatch.delenv(var, raising=False)

    # 审计 HWM 的单例可能已被其他用例按旧数据根创建过，先重置（用例结束自动恢复）。
    import src.security.audit_logger as audit_logger
    import src.security.audit_policy as audit_policy

    monkeypatch.setattr(audit_policy, "_global_audit_kernel", None)
    monkeypatch.setattr(audit_logger, "_default_logger", None)

    from src.ai.web_research import _settings_path
    from src.api.app import MetricsApplication
    from src.api.providers_metrics import (
        ProviderMetricsRepository,
        default_metrics_database_url,
    )
    from src.automation.tools import _ticket_db_path
    from src.gateway.auth import resolve_secret_store
    from src.kernels.audit.dual_signature import DualSignatureStore
    from src.security.secret_store import resolve_secret_store_dir
    from src.workflow.checkpoint import CheckpointManager
    from src.integrations.orm_models import default_database_url
    from src.intel.store import IntelStore
    from src.kernels.audit import AuditStore
    from src.kernels.identity._persistence import resolve_human_identity_store
    from src.models.registry import ModelRegistry
    from src.observability.alerts.store import AlertStore
    from src.security.audit_logger import get_crypto_audit_logger
    from src.security.audit_policy import get_audit_kernel
    from src.security.encryption import resolve_master_key_file
    from src.security.jwt_handler import RevocationStore
    from src.tasks.broker import default_queue_db
    from src.tasks.journal import task_journal_path
    from src.tasks.reminder_notify import default_reminder_outbox
    from src.tasks.reminders import default_reminders_db

    identities = resolve_human_identity_store()
    assert _under(identities.location, data), identities.location

    assert _under(AuditStore()._db_path, data)
    assert _under(AlertStore().storage_path, data)
    assert _under(resolve_secret_store().path, data)
    assert _under(RevocationStore().path, data)
    assert _under(ModelRegistry().storage_path, data)
    assert _under(_settings_path(), data)
    assert _under(IntelStore()._db_path, data)
    assert default_database_url().startswith(f"sqlite:///{data}")

    # 第三批：审计高水位 / 主密钥 / 队列四类
    assert _under(get_audit_kernel()._hwm_path, data)
    assert _under(get_crypto_audit_logger()._hwm_path, data)
    assert _under(resolve_master_key_file(), data)
    for path in (
        default_queue_db(),
        task_journal_path(),
        default_reminders_db(),
        default_reminder_outbox(),
    ):
        assert _under(path, data), path

    # 第四批：自动化票据 / 指标库 / 工作流检查点 / 双签证据 / 密钥库
    assert _under(_ticket_db_path(), data), _ticket_db_path()
    assert _under(
        ProviderMetricsRepository._resolve_sqlite_path(default_metrics_database_url()),
        data,
    )
    # MetricsApplication 曾自己复制了一份 cwd 默认值，单独钉住，防止再次绕过。
    assert _under(
        ProviderMetricsRepository._resolve_sqlite_path(MetricsApplication().database_url),
        data,
    )
    assert _under(CheckpointManager._default_db_path(), data)
    assert _under(DualSignatureStore()._db_path, data)
    assert _under(resolve_secret_store_dir(), data)


def test_new_sqlite_stores_migrate_from_legacy(monkeypatch, tmp_path):
    """通用 SQLite 存储：旧位置有库时复制到数据根，校验通过且旧文件保留。"""
    import sqlite3

    from src.tasks._queue_paths import resolve_sqlite_store

    data = tmp_path / "data"
    legacy = tmp_path / "legacy" / "automation_tickets.db"
    legacy.parent.mkdir(parents=True)
    monkeypatch.setenv("LIUHAO_DATA_DIR", str(data))

    conn = sqlite3.connect(legacy)
    conn.execute("CREATE TABLE tickets (id INTEGER PRIMARY KEY)")
    conn.execute("INSERT INTO tickets DEFAULT VALUES")
    conn.commit()
    conn.close()

    target = Path(resolve_sqlite_store("automation_tickets.db", legacy, label="测试库"))
    assert target == data / "automation_tickets.db"
    assert target.exists()
    check = sqlite3.connect(target)
    try:
        assert check.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 1
    finally:
        check.close()
    # 旧库保留未删，也不留迁移临时文件 / sidecar。
    assert legacy.exists()
    assert not list(data.glob("automation_tickets.db.*.migrating"))


def test_metrics_legacy_db_is_found_after_cwd_changes(monkeypatch, tmp_path):
    """旧 ``./verify_metrics.db`` 换了启动目录也要能找到并迁移。

    历史默认是相对 cwd 的，用户完全可能"上次装在程序目录启动、这次从别处启动"。
    解析必须探测 cwd 与程序根，而不是只认一个固定位置。
    """
    import sqlite3

    from src.api.providers_metrics import (
        ProviderMetricsRepository,
        default_metrics_database_url,
    )

    install_root = tmp_path / "install"
    install_root.mkdir()
    legacy = install_root / "verify_metrics.db"
    conn = sqlite3.connect(legacy)
    conn.execute(
        "CREATE TABLE provider_metrics ("
        "id INTEGER PRIMARY KEY, provider TEXT, model TEXT, timestamp TEXT,"
        " latency_ms REAL, success_rate REAL)"
    )
    conn.execute(
        "INSERT INTO provider_metrics"
        " (provider, model, timestamp, latency_ms, success_rate)"
        " VALUES ('mock', 'm1', '2026-01-01T00:00:00Z', 12.5, 0.9)"
    )
    conn.commit()
    conn.close()

    launch_dir = tmp_path / "launch"
    launch_dir.mkdir()
    monkeypatch.chdir(launch_dir)
    monkeypatch.setenv("LIUHAO_APP_ROOT", str(install_root))
    monkeypatch.setenv("LIUHAO_DATA_DIR", str(tmp_path / "data"))

    target = ProviderMetricsRepository._resolve_sqlite_path(
        default_metrics_database_url()
    )
    assert target == tmp_path / "data" / "verify_metrics.db"
    assert target.is_file(), "程序根里的旧库必须被迁到数据根"
    assert legacy.is_file(), "旧库保留，不删不改"

    check = sqlite3.connect(target)
    try:
        assert check.execute("SELECT COUNT(*) FROM provider_metrics").fetchone()[0] == 1
    finally:
        check.close()


def test_full_flow_leaves_the_code_root_untouched(client, auth_headers):
    """真跑「发消息 / 建员工 / 建目标」，代码根不得新增或改写任何运行时文件。"""
    before_names, before_stamps, before_plugins = (
        _top_level(),
        _watched_stamps(),
        _plugin_files(),
    )

    created = client.post(
        "/v1/models",
        json={
            "name": "isolation-mock",
            "provider": "mock",
            "model": "isolation-mock",
            "timeout": 10,
        },
        headers=auth_headers,
    )
    assert created.status_code == 200, created.text
    model_id = created.json()["id"]

    chat = client.post(
        "/v1/chat",
        json={
            "message": "隔离检查：这条消息不该写进仓库根",
            "session_id": "isolation-probe-session",
            "model_id": model_id,
        },
        headers=auth_headers,
    )
    assert chat.status_code == 200, chat.text

    hired = client.post(
        "/v1/employees",
        json={"name": "isolation-employee", "agent_count": 1, "agent_types": ["general"]},
        headers=auth_headers,
    )
    assert hired.status_code == 201, hired.text

    goal = client.post(
        "/v1/goals",
        json={"natural_language": "python: result = 1+1", "scope": "L1"},
        headers=auth_headers,
    )
    assert goal.status_code == 200, goal.text

    after_names, after_stamps, after_plugins = (
        _top_level(),
        _watched_stamps(),
        _plugin_files(),
    )

    assert after_names - before_names == set(), (
        f"代码根新增了运行时项：{sorted(after_names - before_names)}"
    )
    rewritten = sorted(
        name
        for name, stamp in after_stamps.items()
        if before_stamps.get(name) != stamp
    )
    assert rewritten == [], f"代码根运行时文件被改写：{rewritten}"
    assert after_plugins - before_plugins == set(), (
        f"插件源码目录新增了运行时索引：{sorted(after_plugins - before_plugins)}"
    )
