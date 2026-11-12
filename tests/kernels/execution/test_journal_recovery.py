"""Execution Journal 的两个新增缺口 — 幂等键与恢复扫描。

这两项是 `_journal.py` 自述里最能救命的两项：

* **幂等键**：崩溃发生在 `task_started` 之后、`task_completed` 之前时，
  「副作用到底做没做」无法从事件序列判定。账本让副作用执行方有一个跨进程
  稳定的占坑点，重复投递不会再执行第二次。
* **恢复扫描**：进程重启后必须能问出「上一轮留下了哪些没有终态的执行、
  当时哪些任务正在跑」。没有它，恢复只能靠人记得。

本文件用真实 SQLite 文件（不是 mock）验证持久化语义：所有断言在关闭连接、
重新打开之后依然成立。
"""

from __future__ import annotations

import threading

import pytest

from src.kernels.execution._journal import (
    ExecutionJournal,
    make_idempotency_key,
)


@pytest.fixture()
def journal(tmp_path):
    j = ExecutionJournal(str(tmp_path / "journal.db"))
    try:
        yield j
    finally:
        j.close()


# ---------------------------------------------------------------------------
# 幂等键
# ---------------------------------------------------------------------------


def test_claim_is_exclusive_and_survives_reopen(journal, tmp_path):
    key = make_idempotency_key("exec-1", "write-report")
    assert journal.claim_idempotency(key, "exec-1", "write-report") is True
    assert journal.claim_idempotency(key, "exec-1", "write-report") is False

    # 真正的验收点：换一条连接（= 进程重启）后占坑仍在。
    journal.close()
    reopened = ExecutionJournal(str(tmp_path / "journal.db"))
    try:
        assert reopened.idempotency_claimed(key) is True
        assert reopened.claim_idempotency(key, "exec-1", "write-report") is False
    finally:
        reopened.close()


def test_key_is_stable_for_same_execution_and_task():
    assert make_idempotency_key("goal-1", "task-a") == make_idempotency_key("goal-1", "task-a")
    assert make_idempotency_key("goal-1", "task-a") != make_idempotency_key("goal-2", "task-a")
    assert make_idempotency_key("goal-1", "task-a") != make_idempotency_key("goal-1", "task-b")


def test_result_is_recorded_and_replay_returns_it(journal):
    key = make_idempotency_key("exec-2", "send-message")
    assert journal.idempotent_result(key) is None
    assert journal.claim_idempotency(key, "exec-2", "send-message") is True
    journal.remember_idempotent_result(key, message_id="m-42", delivered=True)

    assert journal.idempotent_result(key) == {"message_id": "m-42", "delivered": True}
    # 已完成的占坑不允许释放：副作用已经在外部世界发生过。
    assert journal.release_idempotency(key) is False
    assert journal.claim_idempotency(key, "exec-2", "send-message") is False


def test_release_allows_a_real_retry(journal):
    key = make_idempotency_key("exec-3", "upload")
    assert journal.claim_idempotency(key, "exec-3", "upload") is True
    # 副作用尚未发生（例如参数校验失败），释放后重试必须能再次占坑。
    assert journal.release_idempotency(key) is True
    assert journal.idempotency_claimed(key) is False
    assert journal.claim_idempotency(key, "exec-3", "upload") is True


def test_remember_without_claim_is_rejected(journal):
    with pytest.raises(ValueError):
        journal.remember_idempotent_result("never-claimed", ok=True)


def test_concurrent_claim_has_exactly_one_winner(journal):
    key = make_idempotency_key("exec-4", "pay")
    results: list[bool] = []
    lock = threading.Lock()

    def worker() -> None:
        won = journal.claim_idempotency(key, "exec-4", "pay")
        with lock:
            results.append(won)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count(True) == 1
    assert results.count(False) == 7


# ---------------------------------------------------------------------------
# 恢复扫描
# ---------------------------------------------------------------------------


def test_scan_reports_in_flight_tasks(journal):
    journal.record("exec-crash", "execution_started", goal="crash goal")
    journal.record("exec-crash", "task_started", task_name="fetch")
    journal.record("exec-crash", "task_completed", task_name="fetch")
    journal.record("exec-crash", "task_started", task_name="write")
    # 进程在这里被硬杀：没有 task_completed，也没有 execution_completed。

    scanned = journal.incomplete_executions()
    assert len(scanned) == 1
    found = scanned[0]
    assert found.execution_id == "exec-crash"
    assert found.in_flight_tasks == ("write",)
    assert found.completed_tasks == ("fetch",)
    assert found.failed_tasks == ()
    assert found.last_event_type == "task_started"
    assert found.age_seconds is not None and found.age_seconds >= 0


def test_scan_is_empty_after_terminal_event(journal):
    journal.record("exec-done", "execution_started", goal="done")
    journal.record("exec-done", "task_started", task_name="one")
    journal.record("exec-done", "task_completed", task_name="one")
    journal.record("exec-done", "execution_completed", status="completed")

    assert journal.incomplete_executions() == []


def test_scan_reports_failed_but_not_in_flight(journal):
    journal.record("exec-fail", "execution_started")
    journal.record("exec-fail", "task_started", task_name="bad")
    journal.record("exec-fail", "task_failed", task_name="bad", error="boom")

    found = journal.incomplete_executions()[0]
    assert found.in_flight_tasks == ()
    assert found.failed_tasks == ("bad",)


def test_scan_anchors_to_the_latest_start(journal):
    """同一个 goal 第二次提交后，旧 run 的完成事件不能把新 run 判成已终结。"""
    journal.record("exec-rerun", "execution_started")
    journal.record("exec-rerun", "execution_completed", status="completed")
    journal.record("exec-rerun", "execution_started")

    assert [e.execution_id for e in journal.incomplete_executions()] == ["exec-rerun"]


def test_cancelled_execution_is_not_incomplete(journal):
    journal.record("exec-cancel", "execution_started")
    journal.record("exec-cancel", "task_started", task_name="slow")
    journal.record("exec-cancel", "execution_cancelled", status="cancelled")

    assert journal.incomplete_executions() == []


def test_scan_ignores_rows_without_a_start_event(journal):
    # 只有任务事件、没有 execution_started 的行不是「半截执行」。
    journal.record("exec-orphan-events", "task_completed", task_name="mystery")
    assert journal.incomplete_executions() == []


def test_scan_supports_age_filter(journal):
    journal.record("exec-fresh", "execution_started")
    # 刚启动的执行不应被一个防抖窗口当成孤儿的。
    assert journal.incomplete_executions(older_than_seconds=3600) == []
    assert len(journal.incomplete_executions(older_than_seconds=0)) == 1
