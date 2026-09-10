"""审计并发安全回归测试。

背景（真实生产缺陷）：审计 store 是**全局单例**，而 FastAPI 的同步端点
（``def`` 而非 ``async def``）跑在**线程池**里 —— 连接会被多线程复用。
此前 ``sqlite3.connect`` 未传 ``check_same_thread=False``，多线程下抛
``ProgrammingError: SQLite objects created in a thread can only be used
in that same thread``，且 hash-chain 的 seq/prev_hash 会并发错乱。

这两个测试锁定修复：① 跨线程复用不抛异常；② 并发写后 hash-chain 仍然完整。
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from src.kernels.audit import AuditEventType, AuditScope, AuditStore


def _store(tmp_path) -> AuditStore:
    return AuditStore(db_path=str(tmp_path / "audit_thread.db"))


def test_audit_store_reusable_across_threads(tmp_path):
    """同一个 store 在多个线程里写审计不抛异常（FastAPI 线程池真实场景）。"""
    store = _store(tmp_path)
    errors: list[BaseException] = []

    def worker(i: int) -> None:
        try:
            store.log_event(
                AuditEventType.ACCESS_ALLOWED,
                principal_id=f"thread-{i}",
                scope=AuditScope.L1,
                outcome="allow",
            )
        except BaseException as exc:  # noqa: BLE001 - 记录任何线程内异常
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"跨线程写审计失败：{errors!r}"
    events = store.query_events()
    assert len(events) == 8
    assert {e["principal_id"] for e in events} == {f"thread-{i}" for i in range(8)}


def test_concurrent_writes_keep_hash_chain_intact(tmp_path):
    """并发写后 hash-chain 仍然完整（seq 连续 + prev_hash 链接正确）。"""
    store = _store(tmp_path)
    total = 60

    def worker(i: int) -> None:
        store.log_event(
            AuditEventType.ACCESS_ALLOWED,
            principal_id="concurrent",
            scope=AuditScope.L1,
            outcome="allow",
            details={"i": i},
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(worker, range(total)))

    ok, count = store.verify_integrity()
    assert count == total
    assert ok, "并发写破坏了审计 hash-chain（seq 不连续或 prev_hash 链接错误）"
