"""队列类存储跟随数据根，且迁移不丢数据、不换空库。

覆盖四类落点（``tasks.db`` / ``tasks.journal`` / ``reminders.db`` /
``reminders_outbox.jsonl``）与四种组合：

* 只有旧路径 → 解析到新路径、数据被复制并校验、旧文件保留；
* 只有新路径 → 正常使用，不回头去建旧文件；
* 两边都有   → 以新位置为准，旧文件不删；
* 两边都没有 → 正常返回新路径（由各存储自己建库）。

另加两条：迁移失败必须报错并继续用旧位置（绝不静默空库）；CLI / 通知这类
**独立进程入口**在子进程里解析出的路径必须与运行时一致。
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from src.kernels.execution._journal import _SCHEMA as JOURNAL_SCHEMA
from src.kernels.execution._journal import ExecutionJournal
from src.tasks import _queue_paths
from src.tasks.broker import default_queue_db
from src.tasks.journal import task_journal_path
from src.tasks.reminder_notify import default_reminder_outbox
from src.tasks.reminders import ReminderStore, default_reminders_db
from src.tasks.sqlite_broker import _SCHEMA as TASK_QUEUE_SCHEMA
from src.tasks.sqlite_broker import SqliteTaskBroker

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def queue_env(monkeypatch, tmp_path):
    """隔离 ``~/.liuhao`` 与数据根，并清掉单项覆盖变量。"""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    data = tmp_path / "data"
    monkeypatch.setenv("LIUHAO_DATA_DIR", str(data))
    monkeypatch.delenv("LIUHAO_WORKSPACE_ROOT", raising=False)
    for var in (
        "LIUHAO_TASK_DB",
        "LIUHAO_TASK_JOURNAL",
        "LIUHAO_REMINDERS_DB",
        "LIUHAO_REMINDER_OUTBOX",
    ):
        monkeypatch.delenv(var, raising=False)
    return home, data


def _legacy_dir(home: Path) -> Path:
    return home / ".liuhao" / "queue"


def _make_legacy_tasks_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(TASK_QUEUE_SCHEMA)
        conn.execute(
            "INSERT INTO task_queue"
            " (task_id, task_name, queue, payload, status, created_at, claimed_at)"
            " VALUES ('pending-id', 'demo.pending', 'default', ?, 'pending', ?, NULL)",
            (b"pending-payload", "2026-11-08T00:00:00+00:00"),
        )
        conn.execute(
            "INSERT INTO task_queue"
            " (task_id, task_name, queue, payload, status, created_at, claimed_at)"
            " VALUES ('running-id', 'demo.running', 'default', ?, 'running', ?, ?)",
            (b"running-payload", "2026-11-08T00:00:01+00:00", "2026-11-08T00:00:02+00:00"),
        )
        conn.commit()
    finally:
        conn.close()


def _make_legacy_reminders_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    store = ReminderStore(str(path))
    try:
        store.create(
            reminder_id="rem-1",
            user_id="u1",
            spec=_once_spec(),
            now_iso="2026-11-08T00:00:00+00:00",
        )
    finally:
        store.close()


def _once_spec():
    from datetime import datetime, timedelta, timezone

    from src.tasks.reminder_time import ReminderSpec

    return ReminderSpec(
        title="迁移别丢这条",
        run_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        repeat_kind="once",
        source_text="五分钟后提醒我迁移别丢这条",
        time_text="5 分钟后",
    )


def _make_legacy_journal(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(JOURNAL_SCHEMA)
        conn.execute(
            "INSERT INTO journal_events (execution_id, event_type, task_name, ts, payload)"
            " VALUES ('exec-legacy', 'execution_started', NULL, ?, '{}')",
            ("2026-11-08T00:00:00+00:00",),
        )
        conn.commit()
    finally:
        conn.close()


def test_only_legacy_tasks_db_is_migrated_and_still_executable(queue_env):
    home, data = queue_env
    legacy = _legacy_dir(home) / "tasks.db"
    _make_legacy_tasks_db(legacy)

    resolved = Path(default_queue_db())
    assert resolved == data / "queue" / "tasks.db"
    assert resolved.is_file()
    assert legacy.is_file(), "旧文件必须保留（迁移是复制不是搬走）"
    assert not list(resolved.parent.glob("*.migrating"))

    broker = SqliteTaskBroker(str(resolved))
    try:
        assert broker.pending_count() == 1
        assert broker.running_count() == 1
        claimed = broker._claim_next()
        assert claimed is not None
        assert claimed[1] == b"pending-payload"
    finally:
        broker._conn.close()


def test_only_legacy_reminders_db_is_migrated_and_readable(queue_env):
    home, data = queue_env
    legacy = _legacy_dir(home) / "reminders.db"
    _make_legacy_reminders_db(legacy)

    resolved = Path(default_reminders_db())
    assert resolved == data / "queue" / "reminders.db"

    store = ReminderStore(str(resolved))
    try:
        records = store.list("u1")
    finally:
        store.close()
    assert [record["id"] for record in records] == ["rem-1"]
    assert legacy.is_file()


def test_only_legacy_journal_is_migrated(queue_env):
    home, data = queue_env
    legacy = _legacy_dir(home) / "tasks.journal"
    _make_legacy_journal(legacy)

    resolved = Path(task_journal_path())
    assert resolved == data / "queue" / "tasks.journal"

    journal = ExecutionJournal(str(resolved))
    try:
        assert journal.executions() == ["exec-legacy"]
    finally:
        journal.close()
    assert legacy.is_file()


def test_only_legacy_outbox_is_migrated_byte_for_byte(queue_env):
    home, data = queue_env
    legacy = _legacy_dir(home) / "reminders_outbox.jsonl"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    payload = '{"title":"旧提醒","body":"别丢"}\n'
    legacy.write_text(payload, encoding="utf-8")

    resolved = Path(default_reminder_outbox())
    assert resolved == data / "queue" / "reminders_outbox.jsonl"
    assert resolved.read_text(encoding="utf-8") == payload
    assert legacy.read_text(encoding="utf-8") == payload


def test_only_new_location_is_used_without_recreating_legacy(queue_env):
    home, data = queue_env
    new_dir = data / "queue"
    new_dir.mkdir(parents=True)
    target = new_dir / "tasks.db"
    _make_legacy_tasks_db(target)

    assert Path(default_queue_db()) == target
    assert not _legacy_dir(home).exists()


def test_both_locations_prefer_new_and_keep_legacy(queue_env):
    home, data = queue_env
    legacy = _legacy_dir(home) / "reminders.db"
    _make_legacy_reminders_db(legacy)
    new = data / "queue" / "reminders.db"
    _make_legacy_reminders_db(new)

    assert Path(default_reminders_db()) == new
    assert legacy.is_file(), "两边都有时旧文件也不能被静默删除"


def test_neither_location_returns_new_path_for_initialization(queue_env):
    _home, data = queue_env
    resolved = Path(default_reminders_db())
    assert resolved == data / "queue" / "reminders.db"
    assert not resolved.exists(), "不该抢在存储初始化之前建出空文件"

    store = ReminderStore(str(resolved))
    store.close()
    assert resolved.is_file()


def test_migration_failure_reports_and_keeps_old_location(queue_env, monkeypatch, caplog):
    home, data = queue_env
    legacy = _legacy_dir(home) / "tasks.db"
    _make_legacy_tasks_db(legacy)

    def _boom(*_args, **_kwargs):
        raise OSError("simulated copy failure")

    monkeypatch.setattr(_queue_paths, "_copy_sqlite", _boom)

    with caplog.at_level("ERROR", logger=_queue_paths.__name__):
        resolved = Path(default_queue_db())

    assert resolved == legacy, "迁移失败必须继续用旧库，不能换到空的新库"
    assert not (data / "queue" / "tasks.db").exists()
    assert not list((data / "queue").glob("*.migrating"))
    assert any("迁移失败" in record.getMessage() for record in caplog.records)


def test_explicit_variable_wins_and_skips_migration(queue_env, tmp_path):
    home, _data = queue_env
    legacy = _legacy_dir(home) / "tasks.db"
    _make_legacy_tasks_db(legacy)
    explicit = tmp_path / "explicit" / "tasks.db"
    os.environ["LIUHAO_TASK_DB"] = str(explicit)

    assert Path(default_queue_db()) == explicit
    assert not explicit.exists()
    assert legacy.is_file()


# ---------------------------------------------------------------------------
# 跨进程安全：真实子进程并发迁移 / 持锁被强杀 / 排程器库同锁
# ---------------------------------------------------------------------------

_CONCURRENT_CHILD = """
import json, logging, os, sys, time
from pathlib import Path
go, out = Path(sys.argv[1]), Path(sys.argv[2])
messages = []
class _Handler(logging.Handler):
    def emit(self, record):
        messages.append(record.getMessage())
_logger = logging.getLogger("src.tasks._queue_paths")
_logger.setLevel(logging.INFO)
_logger.addHandler(_Handler())
deadline = time.time() + 30
while not go.exists():
    if time.time() > deadline:
        raise SystemExit(3)
    time.sleep(0.005)
from src.tasks.broker import default_queue_db
path = default_queue_db()
out.write_text(json.dumps({
    "pid": os.getpid(),
    "path": path,
    "migrated": any("已迁移" in m for m in messages),
}), encoding="utf-8")
"""

_KILLED_LOCK_CHILD = """
import os, sys
from src.common.atomic_write import exclusive_file_lock
with exclusive_file_lock(sys.argv[1]):
    open(sys.argv[2], "w", encoding="utf-8").write("locked")
    os._exit(0)   # 持锁期间被强杀：不走 with 收尾，也不做任何 unlink
"""


def _spawn(script_path: Path, args: list[str], env: dict) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, str(script_path), *args],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def test_two_processes_migrate_exactly_once(queue_env, tmp_path):
    """两个真实进程同时解析：只迁一次，路径一致，库完整且无临时残留。"""
    home, data = queue_env
    legacy = _legacy_dir(home) / "tasks.db"
    _make_legacy_tasks_db(legacy)
    expected_counts = {"pending": 1, "running": 1}

    script = tmp_path / "concurrent_child.py"
    script.write_text(_CONCURRENT_CHILD, encoding="utf-8")
    go = tmp_path / "go"
    outs = [tmp_path / f"out-{index}.json" for index in range(2)]
    env = _child_env(home, data)
    children = [_spawn(script, [str(go), str(out)], env) for out in outs]
    go.write_text("go", encoding="utf-8")
    for child in children:
        _, stderr = child.communicate(timeout=120)
        assert child.returncode == 0, stderr

    results = [json.loads(out.read_text(encoding="utf-8")) for out in outs]
    assert len({item["path"] for item in results}) == 1, results
    assert Path(results[0]["path"]) == data / "queue" / "tasks.db"
    assert sum(1 for item in results if item["migrated"]) == 1, (
        f"应当只有一个进程执行迁移，实际：{results}"
    )

    assert counts_of(data / "queue" / "tasks.db") == expected_counts
    assert counts_of(legacy) == expected_counts, "旧库也必须仍然完整"
    assert not list((data / "queue").glob("*.migrating*")), "并发下不能有临时残留"


def test_kill_while_holding_lock_is_recoverable(queue_env, tmp_path):
    """持锁进程被强杀后，锁由系统释放，下一次解析能正常完成迁移。"""
    home, data = queue_env
    legacy = _legacy_dir(home) / "tasks.db"
    _make_legacy_tasks_db(legacy)
    target = data / "queue" / "tasks.db"
    lock_path = str(target) + ".migrate.lock"

    script = tmp_path / "killed_lock_child.py"
    script.write_text(_KILLED_LOCK_CHILD, encoding="utf-8")
    signal = tmp_path / "locked.signal"
    child = _spawn(script, [lock_path, str(signal)], _child_env(home, data))

    deadline = time.time() + 30
    while not signal.exists():
        assert time.time() < deadline, "子进程没能拿到锁"
        time.sleep(0.01)
    child.communicate(timeout=60)
    assert child.returncode == 0
    assert _has_lock_file(lock_path), "锁文件本身会保留（内容无意义，不靠它判断占用）"

    started = time.monotonic()
    resolved = Path(default_queue_db())
    elapsed = time.monotonic() - started

    assert resolved == target
    assert elapsed < 10, f"锁没有被系统释放，等了 {elapsed:.1f}s"
    assert counts_of(target) == {"pending": 1, "running": 1}
    assert legacy.is_file()


def test_default_scheduler_db_goes_through_the_same_lock(queue_env, monkeypatch, tmp_path):
    """排程器库复用同一个解析入口，因此也必须被同一把锁包住（不是假设）。"""
    home, data = queue_env
    legacy = _legacy_dir(home) / "local_scheduler.db"
    _make_legacy_scheduler_db(legacy)

    seen: list[str] = []
    real_lock = _queue_paths.exclusive_file_lock

    def _spy(lock_path, *args, **kwargs):
        seen.append(str(lock_path))
        return real_lock(lock_path, *args, **kwargs)

    monkeypatch.setattr(_queue_paths, "exclusive_file_lock", _spy)

    from src.tasks.local_scheduler import default_scheduler_db

    resolved = Path(default_scheduler_db())
    assert resolved == data / "queue" / "local_scheduler.db"
    assert seen == [str(resolved) + ".migrate.lock"], (
        f"排程器库迁移没有走同一把锁：{seen}"
    )
    assert counts_of(resolved, table="scheduled_jobs") == {
        "pending": 1,
        "running": 0,
    }


def test_migration_lock_timeout_does_not_fall_back_to_old_location(
    queue_env, monkeypatch, caplog
):
    """持锁进程一直活着时：明确报错，绝不退回旧库造成两套并存。"""
    home, data = queue_env
    legacy = _legacy_dir(home) / "tasks.db"
    _make_legacy_tasks_db(legacy)

    monkeypatch.setattr(_queue_paths, "MIGRATION_LOCK_TIMEOUT_SECONDS", 0.5)
    lock_path = str(data / "queue" / "tasks.db") + ".migrate.lock"
    with _queue_paths.exclusive_file_lock(lock_path, timeout=5.0):
        with caplog.at_level("ERROR", logger=_queue_paths.__name__):
            with pytest.raises(TimeoutError):
                default_queue_db()

    assert not (data / "queue" / "tasks.db").exists()
    assert any("迁移锁等待超时" in record.getMessage() for record in caplog.records)


def test_orphan_staging_cleanup_only_removes_dead_and_old(queue_env):
    """持锁清理：只删「pid 已退出 + mtime 够旧」的孤儿，活的与新的都留。"""
    _home, data = queue_env
    queue_dir = data / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)
    target = queue_dir / "tasks.db"
    target.write_bytes(b"")  # 目标已存在：本次解析只做清理，不再迁移
    target_wal = queue_dir / "tasks.db-wal"
    target_shm = queue_dir / "tasks.db-shm"
    target_wal.write_bytes(b"")
    target_shm.write_bytes(b"")

    dead_pid = _exited_pid()
    assert not _queue_paths._pid_is_alive(dead_pid), "前置条件：该 pid 必须已退出"
    assert _queue_paths._pid_is_alive(os.getpid())

    old = time.time() - 7200  # 默认阈值 1 小时，2 小时前足够旧
    orphan = queue_dir / f"tasks.db.{dead_pid}.deadbeef.migrating"
    orphan.write_bytes(b"half-copied")
    orphan_wal = queue_dir / f"tasks.db.{dead_pid}.deadbeef.migrating-wal"
    orphan_wal.write_bytes(b"wal")
    for path in (orphan, orphan_wal):
        os.utime(path, (old, old))

    live = queue_dir / f"tasks.db.{os.getpid()}.cafe0001.migrating"
    live.write_bytes(b"in-flight")

    assert Path(default_queue_db()) == target
    assert not orphan.exists(), "死 pid + 旧 mtime 的孤儿必须被清理"
    assert not orphan_wal.exists(), "孤儿 sidecar 也要清理"
    assert live.exists(), "活 pid 的临时文件绝不能删"
    assert target.exists() and target_wal.exists() and target_shm.exists(), (
        "目标库及其 -wal/-shm 不在清理范围内"
    )


def counts_of(path: Path, table: str = "task_queue") -> dict:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return {
            status: conn.execute(
                f'SELECT COUNT(*) FROM "{table}" WHERE status = ?', (status,)
            ).fetchone()[0]
            for status in ("pending", "running")
        }
    finally:
        conn.close()


def _has_lock_file(lock_path: str) -> bool:
    return Path(lock_path).exists()


def _exited_pid() -> int:
    """跑一个立刻退出的真实子进程，拿一个「确实已不存在」的 pid。"""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def _make_legacy_scheduler_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            "CREATE TABLE scheduled_jobs ("
            " job_id TEXT PRIMARY KEY, name TEXT, payload TEXT,"
            " run_at TEXT, status TEXT, created_at TEXT, claimed_at TEXT)"
        )
        conn.execute(
            "INSERT INTO scheduled_jobs VALUES ('job-1', 'demo', '{}',"
            " '2026-11-08T00:00:00+00:00', 'pending', '2026-11-08T00:00:00+00:00', NULL)"
        )
        conn.commit()
    finally:
        conn.close()


def _child_env(home: Path, data: Path) -> dict:
    env = os.environ.copy()
    env.update(
        {
            "USERPROFILE": str(home),
            "HOME": str(home),
            "LIUHAO_DATA_DIR": str(data),
            "PYTHONIOENCODING": "utf-8",
        }
    )
    for var in (
        "LIUHAO_WORKSPACE_ROOT",
        "LIUHAO_TASK_DB",
        "LIUHAO_TASK_JOURNAL",
        "LIUHAO_REMINDERS_DB",
        "LIUHAO_REMINDER_OUTBOX",
        "LIUHAO_LOCAL_SCHEDULER_DB",
    ):
        env.pop(var, None)
    # 子进程脚本放在 tmp 里，sys.path[0] 是脚本目录，必须显式给出仓库根。
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        f"{REPO_ROOT}{os.pathsep}{existing}" if existing else str(REPO_ROOT)
    )
    return env


def test_cli_and_notifier_entrypoints_resolve_in_a_child_process(tmp_path):
    """``reminders_cli`` / ``reminder_notify`` 是独立进程入口，不能只在网关里改。"""
    home = tmp_path / "home"
    home.mkdir()
    data = tmp_path / "data"
    script = (
        "import argparse, json\n"
        "from src.tasks.broker import default_queue_db\n"
        "from src.tasks.journal import task_journal_path\n"
        "from src.tasks.reminders import default_reminders_db\n"
        "from src.tasks.reminder_notify import default_reminder_outbox\n"
        "from src.tasks.reminders_cli import _reminders_db, _scheduler_db\n"
        "from src.tasks.local_scheduler import default_scheduler_db\n"
        "ns = argparse.Namespace()\n"
        "print(json.dumps({\n"
        "    'queue': default_queue_db(),\n"
        "    'journal': task_journal_path(),\n"
        "    'reminders': default_reminders_db(),\n"
        "    'outbox': default_reminder_outbox(),\n"
        "    'cli_reminders': _reminders_db(ns),\n"
        "    'cli_scheduler': _scheduler_db(ns),\n"
        "    'runtime_scheduler': default_scheduler_db(),\n"
        "}))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(REPO_ROOT),
        env=_child_env(home, data),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout.strip().splitlines()[-1])

    queue_dir = str(data / "queue")
    assert Path(payload["queue"]).parent == Path(queue_dir)
    assert Path(payload["journal"]).parent == Path(queue_dir)
    assert Path(payload["reminders"]).parent == Path(queue_dir)
    assert Path(payload["outbox"]).parent == Path(queue_dir)
    assert payload["cli_reminders"] == payload["reminders"], (
        "CLI 必须与提醒运行时解析到同一份库"
    )
    assert payload["cli_scheduler"] == payload["runtime_scheduler"], (
        "排程器库由 local_scheduler 定义，CLI 不能另搬一份"
    )


# ---------------------------------------------------------------------------
# 逐个入口证明：都走 resolve_queue_file 的同一把锁，不是"结构上应该"
# ---------------------------------------------------------------------------

def _invoke_cli_reminders() -> str:
    import argparse

    from src.tasks.reminders_cli import _reminders_db

    return _reminders_db(argparse.Namespace())


def _invoke_scheduler() -> str:
    from src.tasks.local_scheduler import default_scheduler_db

    return default_scheduler_db()


def _make_legacy_outbox(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes('{"title":"旧提醒","body":"别丢"}\n'.encode("utf-8"))


_ENTRYPOINT_CASES = [
    ("default_queue_db", "tasks.db", "sqlite", _make_legacy_tasks_db, default_queue_db, "task_queue"),
    ("task_journal_path", "tasks.journal", "sqlite", _make_legacy_journal, task_journal_path, "journal_events"),
    ("default_reminders_db", "reminders.db", "sqlite", _make_legacy_reminders_db, default_reminders_db, "reminders"),
    ("reminders_cli._reminders_db", "reminders.db", "sqlite", _make_legacy_reminders_db, _invoke_cli_reminders, "reminders"),
    ("default_reminder_outbox", "reminders_outbox.jsonl", "text", _make_legacy_outbox, default_reminder_outbox, None),
    ("default_scheduler_db", "local_scheduler.db", "sqlite", _make_legacy_scheduler_db, _invoke_scheduler, "scheduled_jobs"),
]


@pytest.mark.parametrize(
    "name,filename,kind,maker,invoke,table",
    _ENTRYPOINT_CASES,
    ids=[case[0] for case in _ENTRYPOINT_CASES],
)
def test_every_entrypoint_migrates_under_the_same_lock(
    queue_env, monkeypatch, name, filename, kind, maker, invoke, table
):
    """每个入口都实测：只迁一次、迁的那次持有 <target>.migrate.lock、内容一致。"""
    home, data = queue_env
    legacy = _legacy_dir(home) / filename
    maker(legacy)
    assert legacy.is_file()

    seen: list[str] = []
    real_lock = _queue_paths.exclusive_file_lock

    def spy_lock(path, *args, **kwargs):
        seen.append(str(path))
        return real_lock(path, *args, **kwargs)

    monkeypatch.setattr(_queue_paths, "exclusive_file_lock", spy_lock)

    resolved = Path(invoke())
    target = data / "queue" / filename

    assert resolved == target, name
    assert seen == [str(target) + ".migrate.lock"], name
    assert target.is_file(), name
    assert legacy.is_file(), f"{name}: 旧文件必须保留"
    assert not list(target.parent.glob("*.migrating*")), name

    if kind == "text":
        assert target.read_bytes() == legacy.read_bytes(), f"{name}: 文本必须逐字节一致"
    else:
        legacy_sig = _queue_paths._sqlite_signature(legacy)
        target_sig = _queue_paths._sqlite_signature(target)
        assert target_sig[0] == "ok", name
        assert target_sig[1] == legacy_sig[1], f"{name}: 逐表行数必须一致"


def test_outbox_staging_pattern_is_covered_and_scoped(queue_env):
    """文本型目标的 staging 也能被孤儿清理识别，且不会跨目标误删。"""
    _home, data = queue_env
    queue_dir = data / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)
    outbox = queue_dir / "reminders_outbox.jsonl"
    outbox.write_bytes(b'{"keep":1}\n')

    outbox_pattern = _queue_paths._staging_name_pattern(outbox)
    db_pattern = _queue_paths._staging_name_pattern(queue_dir / "tasks.db")

    staging_name = "reminders_outbox.jsonl.1234.deadbeef.migrating"
    assert outbox_pattern.match(staging_name), "outbox 自己的 staging 必须能匹配"
    assert not db_pattern.match(staging_name), "数据库目标的清理不能扫到 outbox 的临时文件"
    assert outbox_pattern.match(staging_name + "-wal")
    assert not outbox_pattern.match("reminders_outbox.jsonl-wal"), "目标文件与 sidecar 不匹配"
    assert not outbox_pattern.match("tasks.db.1234.deadbeef.migrating")

    dead_pid = _exited_pid()
    old = time.time() - 7200
    orphan = queue_dir / f"reminders_outbox.jsonl.{dead_pid}.deadbeef.migrating"
    orphan.write_bytes(b"half")
    orphan_wal = queue_dir / f"reminders_outbox.jsonl.{dead_pid}.deadbeef.migrating-wal"
    orphan_wal.write_bytes(b"wal")
    for path in (orphan, orphan_wal):
        os.utime(path, (old, old))
    live = queue_dir / f"reminders_outbox.jsonl.{os.getpid()}.cafe0001.migrating"
    live.write_bytes(b"in-flight")
    db_orphan = queue_dir / f"tasks.db.{dead_pid}.deadbeef.migrating"
    db_orphan.write_bytes(b"db-half")
    os.utime(db_orphan, (old, old))

    assert Path(default_reminder_outbox()) == outbox
    assert not orphan.exists(), "outbox 的孤儿 staging 必须被清理，不能留成永久垃圾"
    assert not orphan_wal.exists()
    assert live.exists(), "活 pid 的必须保留"
    assert db_orphan.exists(), "解析 outbox 不能误删数据库目标的临时文件"

