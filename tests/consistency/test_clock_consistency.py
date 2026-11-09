"""系统时间回拨/前跳后的正确性实测。

覆盖三条与"时间"耦合的语义：
  * 记忆 TTL：回拨不能让已过期条目复活；
  * 审计保留：前跳不能把仍在保留期内的证据一次性删掉；
  * 会话排序：必须按插入序号（seq），不随时钟跳变而乱序。

真实改系统时钟的用例默认跳过，需 ``LIUHAO_ALLOW_CLOCK_CHANGE_TESTS=1``。
"""

from __future__ import annotations

import datetime
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CLOCK_CHILD = Path(__file__).resolve().parent / "_clock_child.py"


# ---------------------------------------------------------------------------
# 1. 记忆过期 vs 系统时钟回拨（真实 Set-Date）
# ---------------------------------------------------------------------------
@pytest.mark.timeout(180)
@pytest.mark.skipif(
    os.environ.get("LIUHAO_ALLOW_CLOCK_CHANGE_TESTS") != "1",
    reason="会临时修改系统时钟，需 LIUHAO_ALLOW_CLOCK_CHANGE_TESTS=1 显式开启",
)
def test_memory_expiry_survives_system_clock_rollback(tmp_path: Path) -> None:
    db = tmp_path / "memory.db"
    before = datetime.datetime.now()
    mono_start = time.monotonic()

    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT)
    env.pop("PYTEST_CURRENT_TEST", None)
    proc = subprocess.run(
        [sys.executable, "-u", str(CLOCK_CHILD), str(db)],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    # 兜底：子进程若没恢复成功，父进程按单调时钟还原。
    expected = before + datetime.timedelta(seconds=time.monotonic() - mono_start)
    drift = abs((datetime.datetime.now() - expected).total_seconds())
    if drift > 300:
        subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                f"Set-Date -Date '{expected:%Y-%m-%d %H:%M:%S}'",
            ],
            check=False,
            capture_output=True,
            timeout=30,
        )

    assert proc.returncode == 0, proc.stderr[-3000:]
    result = json.loads(proc.stdout.strip().splitlines()[-1])
    print(
        f"\n[clock:memory] before_visible={result['before_visible']} "
        f"after_rollback_visible={result['after_rollback_visible']} clock_drift_restored={drift < 300}"
    )
    assert result["before_visible"] is False
    assert result["after_rollback_visible"] is False, "系统时间回拨后已过期记忆被复活"


# ---------------------------------------------------------------------------
# 2. 审计保留 vs 系统时钟前跳（模块级时间代理，不动真实时钟）
# ---------------------------------------------------------------------------
@pytest.mark.timeout(120)
def test_audit_retention_ignores_forward_clock_jump(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LIUHAO_AUDIT_RETENTION_ENABLED", "1")
    monkeypatch.setenv("LIUHAO_AUDIT_RETENTION_DAYS", "30")
    monkeypatch.setenv("LIUHAO_AUDIT_RETENTION_MIN_KEEP", "1")
    monkeypatch.setenv("LIUHAO_AUDIT_RETENTION_MAX_ROWS", "1000000000")
    monkeypatch.setenv("LIUHAO_AUDIT_RETENTION_MAX_BYTES", "1000000000")
    monkeypatch.setenv("LIUHAO_AUDIT_RETENTION_INTERVAL_SEC", "1")
    monkeypatch.setenv("LIUHAO_AUDIT_RETENTION_MAX_DELETE", "100000")

    from src.kernels.audit import AuditEventType, AuditScope, AuditStore
    import src.kernels.audit as audit_module
    import time as time_module

    db = tmp_path / "audit.db"
    store = AuditStore(db_path=str(db))
    store.initialize()
    for index in range(20):
        store.log_event(
            AuditEventType.STATE_CHANGE,
            "p",
            AuditScope.L0,
            "ok",
            {"i": index},
            f"clock-{index}",
        )

    class ForwardJumpTime:
        """只替换审计模块看到的 time.time()，stable 时钟仍读真实时间。"""

        def __init__(self, real, delta_seconds: float) -> None:
            self._real = real
            self._delta = delta_seconds

        def time(self) -> float:
            return self._real.time() + self._delta

        def __getattr__(self, name: str):
            return getattr(self._real, name)

    monkeypatch.setattr(
        audit_module, "time", ForwardJumpTime(time_module, 40 * 86400)
    )

    cut, floor, tail = store._retention_plan(store._conn)
    print(f"\n[clock:audit] cut={cut} floor={floor} tail={tail}")
    assert tail == 20  # seq 从 1 开始，20 条事件的尾部序号是 20
    assert cut == floor, "系统时间前跳 40 天后，仍在 30 天保留期内的审计证据被判定过期"
    assert store.verify_integrity()[0] is True


# ---------------------------------------------------------------------------
# 3. 会话排序 vs 时间篡改（不依赖 created_at）
# ---------------------------------------------------------------------------
@pytest.mark.timeout(120)
def test_conversation_order_is_insertion_order_not_wall_clock(tmp_path: Path) -> None:
    from src.ai.conversation_store import ConversationStore

    db = tmp_path / "conversation.db"
    store = ConversationStore(db_path=str(db))
    expected = [f"m{index}" for index in range(8)]
    for index, content in enumerate(expected):
        store.append("principal", index, "user", content)

    # 模拟"系统时间被乱改"：把最早的写成未来、最晚的写成过去。
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "UPDATE conversation_turns SET created_at = ? "
            "WHERE seq = (SELECT MIN(seq) FROM conversation_turns)",
            (time.time() + 10 * 86400,),
        )
        conn.execute(
            "UPDATE conversation_turns SET created_at = ? "
            "WHERE seq = (SELECT MAX(seq) FROM conversation_turns)",
            (time.time() - 10 * 86400,),
        )
        conn.commit()
    finally:
        conn.close()

    store2 = ConversationStore(db_path=str(db))
    loaded = [row["content"] for row in store2.load("principal")]
    print(f"\n[clock:conversation] order={loaded}")
    assert loaded == expected, "会话恢复顺序被 created_at 墙钟影响（应为 seq 插入序）"
