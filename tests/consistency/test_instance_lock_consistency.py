"""数据目录被另一个进程占用（文件锁）时的行为实测。

期望：第二个进程不是"随机损坏"，而是被操作系统级文件锁明确拒绝，并给出
可读提示；持锁进程被强杀后，内核自动释放锁，第二个进程可以正常接管，
数据库仍然完整。
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

_HOLDER = r"""
import os, sys, time
sys.path.insert(0, sys.argv[1])
from src.common.instance_lock import InstanceLock
lock = InstanceLock(sys.argv[2])
lock.acquire()
lock.publish(pid=os.getpid(), url="http://127.0.0.1:8080", started_at="consistency-test")
print("READY", flush=True)
while True:
    time.sleep(0.5)
"""


def _env() -> dict:
    env = dict(os.environ)
    for key in list(env):
        if key.startswith("LIUHAO_"):
            env.pop(key, None)
    for key in ("MEMORY_DB_PATH", "AUDIT_DB_PATH", "PYTEST_CURRENT_TEST"):
        env.pop(key, None)
    env["PYTHONPATH"] = str(REPO_ROOT)
    env["PYTHONUNBUFFERED"] = "1"
    return env


@pytest.mark.timeout(120)
def test_second_process_is_refused_then_can_take_over_after_kill(
    tmp_path: Path,
) -> None:
    from src.common.instance_lock import InstanceLock, InstanceLockBusy

    root = tmp_path / "data"
    root.mkdir()
    db = root / "audit.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, note TEXT)")
    conn.execute("INSERT INTO events(note) VALUES ('before-lock')")
    conn.commit()
    conn.close()

    holder = subprocess.Popen(
        [sys.executable, "-u", "-c", _HOLDER, str(REPO_ROOT), str(root)],
        env=_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        line = holder.stdout.readline().strip() if holder.stdout else ""
        assert line == "READY", f"holder failed: {holder.stderr.read() if holder.stderr else ''}"

        second = InstanceLock(root)
        with pytest.raises(InstanceLockBusy) as excinfo:
            second.acquire()
        message = excinfo.value.describe()
        print(f"\n[instance-lock] refused message: {message}")
        assert "已经在运行" in message
        assert "本次启动已退出" in message
        holder_info = second.read_holder()
        assert holder_info.get("pid")
        assert f"pid={holder_info['pid']}" in message

        # 被拒绝的进程没有碰数据库：完整性 + 数据条数都不变。
        check = sqlite3.connect(str(db))
        try:
            assert check.execute("PRAGMA integrity_check(1)").fetchall() == [("ok",)]
            assert check.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
        finally:
            check.close()
    finally:
        holder.kill()
        holder.wait(timeout=15)

    # 持锁进程被强杀后，内核释放锁；第二个进程可以无歧义地接管。
    third = InstanceLock(root)
    deadline = time.monotonic() + 15
    while True:
        try:
            third.acquire()
            break
        except InstanceLockBusy:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.05)
    try:
        third.publish(pid=os.getpid(), url="http://127.0.0.1:8081")
        print("[instance-lock] takeover after kill=ok")
        conn = sqlite3.connect(str(db))
        try:
            conn.execute("INSERT INTO events(note) VALUES ('after-takeover')")
            conn.commit()
            assert conn.execute("PRAGMA integrity_check(1)").fetchall() == [("ok",)]
            assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 2
        finally:
            conn.close()
    finally:
        third.release()
