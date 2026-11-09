"""执行 journal 跟随统一数据根，且 worker / 网关 / artifacts CLI 读写同一份。

三个读取方（`src/tasks/journal.py`、`src/gateway/ai_management.py`、
`src/artifacts/cli.py`）必须解析到同一个文件：任何一侧另拼路径就等于日志分裂，
崩溃恢复会漏掉另一侧写入的事件。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.kernels.execution._journal import ExecutionJournal
from src.tasks import _queue_paths, journal

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def journal_env(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    data = tmp_path / "data"
    monkeypatch.setenv("LIUHAO_DATA_DIR", str(data))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("LIUHAO_WORKSPACE_ROOT", str(workspace))
    monkeypatch.delenv(journal.EXECUTION_JOURNAL_ENV, raising=False)
    return home, data, workspace


def _discard_state_manager() -> None:
    """丢弃进程级 ``AIStateManager`` 单例，并尽力关闭它持有的 journal。"""
    from src.gateway.ai_management import AIStateManager

    manager = AIStateManager._instance
    AIStateManager._instance = None
    if manager is None:
        return
    try:
        runtime = getattr(manager, "_runtime", None)
        handle = getattr(runtime, "journal", None) if runtime is not None else None
        close = getattr(handle, "close", None)
        if callable(close):
            close()
    except Exception:  # noqa: BLE001 - 清理失败不影响用例
        pass


@pytest.fixture(autouse=True)
def _fresh_gateway_state_manager():
    """每个用例前重建网关运行态。

    ``tests/test_data_root_isolation.py::test_full_flow_leaves_the_code_root_untouched``
    会启动真实网关，``AIStateManager`` 是**进程级单例**，它的 ``_runtime`` 绑定了
    当时的旧数据根。本文件随后 ``AIStateManager()`` / ``_ensure_runtime_locked()``
    拿到的仍是那个旧单例，``_runtime`` 已存在于是直接返回、不再解析 journal，
    ``captured`` 永远为空，最终 ``KeyError: 'journal'``。这是跨文件的测试顺序
    污染，不是产品缺陷；只在本模块复位，避免动 ``tests/conftest.py`` 这个多线
    热点。
    """
    _discard_state_manager()
    yield
    _discard_state_manager()


def _make_journal(path: Path, execution_ids) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = ExecutionJournal(str(path))
    try:
        for index, execution_id in enumerate(execution_ids):
            handle.record(execution_id, "task_started", "demo.task", step=index)
    finally:
        handle.close()


def _event_types(path: Path, execution_id: str) -> list[str]:
    handle = ExecutionJournal(str(path))
    try:
        return [event.event_type for event in handle.events(execution_id)]
    finally:
        handle.close()


def test_legacy_workspace_journal_migrates_to_data_root(journal_env, monkeypatch):
    _home, data, workspace = journal_env
    legacy = workspace / "liuhao_execution.journal"
    _make_journal(legacy, ("exec-a", "exec-b"))
    target = data / "execution.journal"

    seen: list[str] = []
    real_lock = _queue_paths.exclusive_file_lock

    def spy_lock(path, *args, **kwargs):
        seen.append(str(path))
        return real_lock(path, *args, **kwargs)

    monkeypatch.setattr(_queue_paths, "exclusive_file_lock", spy_lock)

    resolved = journal.execution_journal_path()

    assert Path(resolved) == target
    assert seen == [str(target) + ".migrate.lock"], "迁移必须在目标锁内发生"
    assert legacy.is_file(), "旧文件必须保留"
    assert not list(target.parent.glob("*.migrating*")), "不能留临时文件"
    assert _queue_paths._sqlite_signature(target) == _queue_paths._sqlite_signature(legacy)
    assert _event_types(target, "exec-a") == ["task_started"]
    assert _event_types(target, "exec-b") == ["task_started"]


def test_repo_root_legacy_is_migrated_when_data_root_set(journal_env, monkeypatch, tmp_path):
    _home, data, _workspace = journal_env
    monkeypatch.delenv("LIUHAO_WORKSPACE_ROOT", raising=False)
    fake_repo = tmp_path / "fake-repo"
    monkeypatch.setattr(journal, "repo_root", lambda: fake_repo)
    legacy = fake_repo / ".liuhao_execution.journal"
    _make_journal(legacy, ("exec-repo",))

    resolved = journal.execution_journal_path()

    assert Path(resolved) == data / "execution.journal"
    assert legacy.is_file()
    assert _event_types(Path(resolved), "exec-repo") == ["task_started"]


def test_explicit_variable_wins_and_skips_migration(journal_env, tmp_path):
    _home, _data, workspace = journal_env
    legacy = workspace / "liuhao_execution.journal"
    _make_journal(legacy, ("exec-legacy",))
    explicit = tmp_path / "explicit" / "custom.journal"

    os.environ[journal.EXECUTION_JOURNAL_ENV] = str(explicit)
    try:
        assert Path(journal.execution_journal_path()) == explicit
    finally:
        os.environ.pop(journal.EXECUTION_JOURNAL_ENV, None)

    assert not explicit.exists(), "显式指定时不该抢跑迁移"
    assert legacy.is_file()


def test_source_mode_keeps_legacy_location(journal_env, monkeypatch):
    _home, _data, workspace = journal_env
    monkeypatch.delenv("LIUHAO_DATA_DIR", raising=False)
    legacy = workspace / "liuhao_execution.journal"

    assert Path(journal.execution_journal_path()) == legacy
    assert not legacy.exists(), "没有数据根也不该在这里建空日志"


def test_migration_failure_falls_back_to_legacy(journal_env, monkeypatch, caplog):
    _home, data, workspace = journal_env
    legacy = workspace / "liuhao_execution.journal"
    _make_journal(legacy, ("exec-legacy",))

    def _boom(*_args, **_kwargs):
        raise OSError("simulated copy failure")

    monkeypatch.setattr(_queue_paths, "_copy_sqlite", _boom)

    with caplog.at_level("ERROR", logger=_queue_paths.__name__):
        resolved = journal.execution_journal_path()

    assert Path(resolved) == legacy, "迁移失败必须继续用旧位置，不能新建空日志"
    assert not (data / "execution.journal").exists()
    assert any("迁移失败" in record.getMessage() for record in caplog.records)


def test_worker_writes_and_gateway_reads_the_same_journal(journal_env, monkeypatch):
    """worker 写入的事件，网关侧立刻读到；三个入口解析到同一路径。"""
    _home, data, workspace = journal_env
    legacy = workspace / "liuhao_execution.journal"
    _make_journal(legacy, ("exec-legacy",))

    worker_journal = journal.get_execution_journal()
    worker_path = worker_journal.db_path
    worker_journal.record("exec-live", "task_started", "demo.live", step=1)

    from src.gateway import ai_management

    captured: dict = {}

    class _StubLCore:
        def __init__(self, **_kwargs):
            pass

        def capability_executor(self):
            return object()

        def executable_capabilities(self):
            return set()

    class _StubRuntime:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    import src.ai.agent_runtime as runtime_module
    import src.ai.lcore as lcore_module
    import src.artifacts.index as artifacts_index

    monkeypatch.setattr(lcore_module, "LCore", _StubLCore)
    monkeypatch.setattr(runtime_module, "AgentRuntime", _StubRuntime)
    monkeypatch.setattr(artifacts_index, "ArtifactIndex", lambda _path: None)
    monkeypatch.setattr(ai_management, "_butler_model_planning_enabled", lambda: False)

    manager = ai_management.AIStateManager()
    monkeypatch.setattr(manager, "_ensure_employee", lambda: None)
    monkeypatch.setattr(manager, "_refresh_agent_pool", lambda: None)
    manager._ensure_runtime_locked()

    gateway_journal = captured["journal"]
    assert Path(worker_path) == data / "execution.journal"
    assert gateway_journal.db_path == worker_path, "网关必须读 worker 写的同一份文件"
    assert [event.event_type for event in gateway_journal.events("exec-live")] == [
        "task_started"
    ]
    assert [event.event_type for event in gateway_journal.events("exec-legacy")] == [
        "task_started"
    ], "迁移过来的旧事件网关也必须看得到"

    from src.artifacts.cli import _default_journal_path

    assert _default_journal_path() == worker_path
