"""断电/强杀一致性实测：真实子进程写入中被 TerminateProcess 强杀。

覆盖三类写路径：
  1. SQLite 事务中途（memory / conversation / audit 三个库）；
  2. JSON 原子替换中途（tmp + fsync + os.replace）；
  3. 日志滚动中途（多进程安全轮转）。

每个用例都记录「强杀前已确认成功的条数 acked」与「重启后实际条数」，
并断言：`PRAGMA integrity_check` 通过、已 ack 的写入一条不少、主文件不是
半截内容。
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CHILD = Path(__file__).resolve().parent / "_crash_child.py"


def _env(scratch: Path) -> dict:
    env = dict(os.environ)
    # 子进程模拟"另一台机器上的独立实例"：清掉 pytest conftest 注入的
    # LIUHAO_* / *_DB_PATH，避免与父测试进程共享日志锁/实例目录。
    for key in list(env):
        if key.startswith("LIUHAO_"):
            env.pop(key, None)
    for key in ("MEMORY_DB_PATH", "AUDIT_DB_PATH", "MEMORY_PERSIST_DIR",
                "CONVERSATION_DB_PATH", "PYTEST_CURRENT_TEST"):
        env.pop(key, None)
    env["PYTHONPATH"] = str(REPO_ROOT)
    env["PYTHONUNBUFFERED"] = "1"
    env["APP_ENV"] = "test"
    env["LIUHAO_DATA_DIR"] = str(scratch / "child_data")
    env["LIUHAO_LOG_DIR"] = str(scratch / "child_logs")
    # 交叉审计等旁路必须落到 tmp，否则每次写入都会因"拒绝写仓库审计库"而重试。
    env["AUDIT_DB_PATH"] = str(scratch / "child_audit.db")
    env["MEMORY_DB_PATH"] = str(scratch / "child_memory.db")
    env["CONVERSATION_DB_PATH"] = str(scratch / "child_conversation.db")
    return env


def _run_and_hard_kill(mode: str, target: Path, *, minimum: int = 20) -> int:
    ack_path = target.parent / f"{target.name}.ack"
    err_path = target.parent / f"{target.name}.stderr"
    acks: list[int] = []
    with open(err_path, "w", encoding="utf-8") as err_file:
        proc = subprocess.Popen(
            [sys.executable, "-u", str(CHILD), mode, str(target), str(ack_path)],
            cwd=str(REPO_ROOT),
            env=_env(target.parent),
            stdout=subprocess.PIPE,
            stderr=err_file,
            text=True,
            bufsize=1,
        )

        def _reader() -> None:
            assert proc.stdout is not None
            for line in proc.stdout:
                if line.startswith("ACK "):
                    acks.append(int(line[4:].strip()))

        thread = threading.Thread(target=_reader, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 60.0
            while len(acks) < minimum and time.monotonic() < deadline:
                if proc.poll() is not None:
                    break
                time.sleep(0.01)
            if len(acks) < minimum:
                raise AssertionError(
                    f"child produced only {len(acks)} acks (need {minimum}); "
                    f"stderr tail: {err_path.read_text(encoding='utf-8', errors='replace')[-2000:]}"
                )
            # 让子进程停在写入循环中的任意位置（大多落在一次事务/一次替换内部）。
            time.sleep(0.05)
            proc.kill()
            proc.wait(timeout=15)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=15)
            thread.join(timeout=3)

    return max(acks) + 1  # acked = 已收到"成功"的写入数


def _integrity_ok(db: Path) -> bool:
    """对强杀后的库跑 ``PRAGMA integrity_check(1)``，返回是否 ``ok``。

    Windows 特有时序：``TerminateProcess`` 返回后，被杀子进程对 ``*-shm`` 的
    内核引用（句柄/映射 section）仍在异步清理——实测此刻该文件连删除都会
    被拒绝（WinError 5，约几十毫秒后恢复）。首个新连接按 win32 VFS 的 DMS
    协议必须把 ``*-shm`` 截断重建（``SetEndOfFile``），恰与残留 section 撞车，
    得到瞬时 ``SQLITE_IOERR_TRUNCATE``（1546，即 audit kernel 注释里记录过的
    同一现象）。库文件与 WAL 的字节始终完好，重开即正常恢复。

    因此只对 I/O 错误家族做有界重试；不放宽断言：integrity_check 仍必须返回
    ``ok``，真实页损坏仍会以错误行形式失败，持续 I/O 故障在超时后原样上抛。
    """
    deadline = time.monotonic() + 5.0
    while True:
        conn = sqlite3.connect(str(db))
        try:
            rows = conn.execute("PRAGMA integrity_check(1)").fetchall()
            return rows == [("ok",)]
        except sqlite3.OperationalError as exc:
            code = getattr(exc, "sqlite_errorcode", 0) or 0
            # 10 = SQLITE_IOERR 主码；扩展码（如 1546 TRUNCATE）低位掩码后同族。
            transient = (code & 0xFF) == 10 and time.monotonic() < deadline
            if not transient:
                raise
        finally:
            conn.close()
        time.sleep(0.02)


def _count(db: Path, table: str) -> int:
    conn = sqlite3.connect(str(db))
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 1. SQLite 事务中途强杀
# ---------------------------------------------------------------------------
@pytest.mark.timeout(120)
def test_sqlite_durability_pragmas_are_power_loss_safe(tmp_path: Path) -> None:
    """进程强杀由 WAL 恢复兜底；"拔电源"还要靠 synchronous=FULL 才能不丢已确认事务。

    NORMAL 只保证崩溃一致性，已提交事务可能停留在 OS 页缓存里，直接断电
    （或蓝屏）会丢失。三个核心库默认都必须是 FULL/EXTRA。
    """
    from src.ai.conversation_store import ConversationStore
    from src.kernels.audit import AuditStore
    from src.kernels.memory import MemoryKernel

    paths = {
        "memory": tmp_path / "memory.db",
        "conversation": tmp_path / "conversation.db",
        "audit": tmp_path / "audit.db",
    }
    kernel = MemoryKernel(db_path=str(paths["memory"]))
    conversation = ConversationStore(db_path=str(paths["conversation"]))
    audit = AuditStore(db_path=str(paths["audit"]))
    audit.initialize()

    # 必须读各自"正在用于写入"的连接：synchronous 是连接级设置，新开一个
    # 连接读到的只是 SQLite 默认值，证明不了写入路径的耐久级别。
    observed = {
        "memory": int(kernel._store._conn.execute("PRAGMA synchronous").fetchone()[0]),
        "conversation": int(conversation._conn.execute("PRAGMA synchronous").fetchone()[0]),
        "audit": int(audit._conn.execute("PRAGMA synchronous").fetchone()[0]),
    }
    print(f"\n[durability] synchronous levels: {observed}")
    for name, level in observed.items():
        # 0=OFF 1=NORMAL 2=FULL 3=EXTRA
        assert level >= 2, f"{name} 的 synchronous={level}，断电可能丢失已确认写入"


@pytest.mark.timeout(120)
@pytest.mark.parametrize(
    "mode,filename,table",
    [
        ("memory", "memory.db", "memory_entries"),
        ("conversation", "conversation.db", "conversation_turns"),
        ("audit", "audit.db", "audit_events"),
    ],
)
def test_sqlite_hard_kill_keeps_acknowledged_writes(
    mode: str, filename: str, table: str, tmp_path: Path
) -> None:
    db = tmp_path / filename
    acked = _run_and_hard_kill(mode, db)

    assert db.exists(), "数据库文件必须存在（哪怕只创建了头部）"
    assert _integrity_ok(db), "PRAGMA integrity_check 必须通过"
    after = _count(db, table)

    # 崩溃瞬间可能有一次事务已经提交但 ack 还没落盘，因此允许 +1（最多 +2
    # 兼容内部批量刷新），但绝不允许少于已 ack 的数量。
    assert after >= acked, f"已确认的写入丢失：acked={acked}, after={after}"
    assert after <= acked + 2, f"出现异常膨胀：acked={acked}, after={after}"
    print(f"\n[crash:{mode}] acked={acked} after={after} integrity=ok")

    # 重启后仍能继续写入（不是"只读残骸"）。
    if mode == "memory":
        from src.kernels.memory import MemoryKernel, MemoryTier

        kernel = MemoryKernel(db_path=str(db))
        kernel.store("post-restart", "ok", tier=MemoryTier.PERSISTENT)
        assert _count(db, table) == after + 1
    elif mode == "conversation":
        from src.ai.conversation_store import ConversationStore

        store = ConversationStore(db_path=str(db))
        store.append("crash-user", 10_000, "user", "after-restart")
        assert _count(db, table) == after + 1
        assert store.load("crash-user")[-1]["content"] == "after-restart"
    else:
        from src.kernels.audit import AuditEventType, AuditScope, AuditStore

        store = AuditStore(db_path=str(db))
        store.initialize()
        ok, verified = store.verify_integrity()
        assert ok is True, "重启后审计哈希链必须仍然可验证"
        assert verified == after
        store.log_event(
            AuditEventType.STATE_CHANGE, "p", AuditScope.L0, "ok", {"post": 1}, "post"
        )
        assert _count(db, table) == after + 1


# ---------------------------------------------------------------------------
# 2. JSON 原子替换中途强杀
# ---------------------------------------------------------------------------
@pytest.mark.timeout(120)
def test_json_atomic_replace_never_leaves_half_written_file(tmp_path: Path) -> None:
    target = tmp_path / "entries.json"
    acked = _run_and_hard_kill("json", target)

    # 主文件必须始终是完整 JSON：要么是上一版，要么是新版。
    data = json.loads(target.read_text(encoding="utf-8"))
    entries = data.get("entries", {})
    after = len(entries)
    assert after >= acked, f"已确认的写入丢失：acked={acked}, after={after}"
    assert after <= acked + 1, f"出现异常膨胀：acked={acked}, after={after}"

    # 重新加载 + 哈希链校验，证明不是"能解析但链坏了"。
    from src.storage.backends import JSONFileBackend

    reloaded = JSONFileBackend(str(target))
    assert len(reloaded._entries) == after
    assert reloaded.verify_integrity() is True

    # 强杀会留下孤儿 .tmp（unlink 不会在 SIGKILL 时执行）——如实记录数量。
    leftovers = sorted(p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp"))
    print(f"\n[crash:json] acked={acked} after={after} orphan_tmp={len(leftovers)}")

    # 重启后仍能继续写。
    now = time.time()
    from src.storage.backends import StorageEntry

    reloaded.put(
        "post-restart",
        StorageEntry(key="post-restart", value="ok", created_at=now, updated_at=now),
    )
    assert len(JSONFileBackend(str(target))._entries) == after + 1

    # 主文件不是半截内容；孤儿 tmp 不属于"已提交数据"，下一次成功写入后应被
    # 顺手清理（见 atomic_write._cleanup_orphan_temps）。
    assert leftovers == [] or all(p.startswith("entries.json.") for p in leftovers), leftovers
    remaining = [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    if os.name == "nt":
        # Windows 上活跃写入的 tmp 无法 unlink，清理是安全的即时行为。
        assert remaining == [], f"成功写入后仍残留孤儿临时文件：{remaining}"
    else:
        # POSIX 上 unlink 对打开中的文件也成功，只清理 >5 分钟的旧孤儿。
        assert len(remaining) <= len(leftovers)


# ---------------------------------------------------------------------------
# 3. 日志滚动中途强杀
# ---------------------------------------------------------------------------
@pytest.mark.timeout(120)
def test_log_rotation_hard_kill_leaves_no_torn_lines(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    target = log_dir / "liuhao.log"
    acked = _run_and_hard_kill("log", target, minimum=50)

    rotated = sorted(
        p
        for p in log_dir.iterdir()
        if p.name == "liuhao.log"
        or (p.name.startswith("liuhao.log.") and p.name.rsplit(".", 1)[-1].isdigit())
    )
    assert rotated, "至少应存在当前日志文件"
    assert target.exists(), "当前日志文件必须存在"

    torn: list[str] = []
    seqs: list[int] = []
    for path in rotated:
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            torn.append(f"{path.name}: invalid utf-8")
            continue
        if text and not text.endswith("\n"):
            torn.append(f"{path.name}: no trailing newline (torn tail)")
        for line in text.splitlines():
            if line.startswith("seq="):
                seqs.append(int(line[4:].split(" ", 1)[0]))

    assert torn == [], f"强杀后出现半截日志行：{torn}"
    print(f"\n[crash:log] acked={acked} retained={len(seqs)} files={len(rotated)} torn=0")

    # 轮转本身会按 backupCount 丢弃最旧文件，因此 retained <= acked 是预期；
    # 关键断言是"没有任何一行是半截的"，且重启后还能继续写。
    assert len(seqs) <= acked + 1
    import logging
    from src.logging_config import MultiProcessRotatingFileHandler

    handler = MultiProcessRotatingFileHandler(
        str(target), maxBytes=8192, backupCount=3
    )
    logger = logging.getLogger("crashlog-after-restart")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.handlers = [handler]
    logger.info("seq=%d after-restart", acked)
    handler.close()
    assert "after-restart" in target.read_text(encoding="utf-8")

    assert len(seqs) >= 1
