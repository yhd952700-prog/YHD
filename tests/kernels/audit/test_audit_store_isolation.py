"""审计 store 单例隔离的**双向证明**（F1 回归护栏）。

这个测试存在的理由
------------------
``tests/kernels/audit/test_audit.py::TestGlobalStore::
test_get_audit_store_returns_same_instance`` 在
``monkeypatch.setenv("AUDIT_DB_PATH", tmp_path/"global_audit.db")`` 之后只断言
``get_audit_store() is get_audit_store()``。那是**同一性**，不是**路径**。

实测证明：我把单例故意污染到另一个库之后，这个断言**依然通过**。它对
"AUDIT_DB_PATH 有没有被理会"完全无感 —— 是一个无法失败的断言。

而这不是纯理论问题。``src/kernels/audit/__init__.py:2196`` 的
``get_audit_store()`` 是模块级单例，``AuditStore.__init__``（:378-391）只在构造时
读 ``AUDIT_DB_PATH``。仓库里 24 个测试文件提到该变量，其中 19 个是 env-only
（只 monkeypatch、不复位单例），它们的重定向在会话中单例先被构造时一律为空。

这里锁定修复后的不变量：每个用例自己的 ``AUDIT_DB_PATH`` 必须真正绑定到它的
``tmp_path``，且 executor fence 不得继续持有上一轮的审计连接。

四段
----
1. ``test_per_test_redirection_binds_to_this_test``
2. ``test_redirection_survives_a_polluted_singleton``  —— 污染后仍能重定向
3. ``test_fence_does_not_hold_the_previous_audit_connection`` —— fence 与审计单例解耦
4. ``test_audit_writes_land_in_this_tests_own_database`` —— 真实写入落在本用例的库里
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import src.kernels.audit as audit_module
import src.kernels.execution.fence as fence_module
from src.kernels.audit import get_audit_store

PROD_AUDIT_DB = Path(__file__).resolve().parent.parent.parent / "audit_store.db"


def test_per_test_redirection_binds_to_this_test():
    """本用例的审计 store 必须落在 conftest 为它准备的**独有**目录里。

    修复前：拿到的是 session 级构造的单例，无论本用例 ``setenv`` 什么都无效。

    注意路径不比对 ``tmp_path``：conftest 刻意把审计库放在 ``tmp_path`` 之外
    （``tmp_path_factory`` 的独立目录），因为
    ``tests/kernels/network/test_network.py::TestHistoryPersistence::
    test_default_bus_writes_nothing`` 断言 ``list(tmp_path.iterdir()) == []``。
    所以这里断言的是「env 声明的路径」与「store 实际绑定的路径」一致。
    """
    import os

    store = get_audit_store()
    resolved = Path(str(store._db_path)).resolve()
    declared = Path(os.environ["AUDIT_DB_PATH"]).resolve()
    assert resolved == declared, (
        "the audit store is bound to %s but AUDIT_DB_PATH declares %s -- the "
        "per-test redirection is void" % (resolved, declared)
    )
    assert resolved != PROD_AUDIT_DB.resolve(), (
        "the audit store fell back to the shared project-root database; the "
        "frozen HC-01 evidence store must never be written by tests"
    )
    assert resolved.name == "audit_store.db", resolved


def test_redirection_survives_a_polluted_singleton():
    """单例被污染后，复位它即可让 ``AUDIT_DB_PATH`` 重新生效。

    复现缺陷机理：``get_audit_store()`` 只在第一次调用时解析 ``AUDIT_DB_PATH``，
    所以任何测试构造过单例之后，其余测试设的 env 就是空的。conftest 的
    ``_isolate_audit_store_per_test`` 靠每用例复位来挡住这种泄漏，本测试锁定该行为。

    注意与 ``tests/kernels/audit/test_audit.py`` 的区别：那个测试只断言
    ``get_audit_store() is get_audit_store()``（同一性），所以把单例污染到另一个库
    之后它依然通过 —— 它对"AUDIT_DB_PATH 有没有被理会"完全无感。
    """
    import os

    # 本用例 fixture 已经把 env 指向了它自己的 tmp_path/audit_store.db
    fixture_db = Path(os.environ["AUDIT_DB_PATH"]).resolve()
    assert fixture_db.name == "audit_store.db", fixture_db

    other = Path(tempfile.mkdtemp(prefix="f1_pollution_")) / "somewhere_else.db"
    previous = os.environ.get("AUDIT_DB_PATH")
    try:
        # 1) 模拟"别的测试"构造了单例并绑到它自己的库 —— 缺陷的触发条件
        os.environ["AUDIT_DB_PATH"] = str(other)
        audit_module._audit_store = None
        poisoned = get_audit_store()
        assert Path(str(poisoned._db_path)).resolve() == other.resolve()

        # 2) 只改 env 不复位 → 拿到同一个陈旧单例（这就是原缺陷）
        os.environ["AUDIT_DB_PATH"] = str(fixture_db)
        assert get_audit_store() is poisoned, (
            "precondition: without a reset the stale singleton is reused"
        )

        # 3) 复位（fixture 做的事）→ 必须重建到本用例的库
        audit_module._audit_store = None
        rebuilt = get_audit_store()
        assert rebuilt is not poisoned
        assert Path(str(rebuilt._db_path)).resolve() == fixture_db, (
            "after the reset the store must honour AUDIT_DB_PATH=%s, got %s"
            % (fixture_db, rebuilt._db_path)
        )
    finally:
        if previous is None:
            os.environ.pop("AUDIT_DB_PATH", None)
        else:
            os.environ["AUDIT_DB_PATH"] = previous


def test_fence_does_not_hold_the_previous_audit_connection():
    """executor fence 不得继续持有上一轮的审计 SQLite 连接。

    ``attach_default_executor_fence()``（fence.py:1166-1173）会把审计连接交给
    ``SqliteExecutorLease`` 并装进模块级全局 ``_DEFAULT_FENCE``。若审计单例被复位
    而 fence 未复位，fence 会握着旧连接 —— 那是坏连接，执行路径会炸。
    """
    fence = fence_module.get_executor_fence()
    lease = fence.lease
    conn = getattr(lease, "_conn", None)
    if conn is None:
        # InMemoryExecutorLease holds no sqlite connection -- that is exactly the
        # state the per-test reset deliberately restores, so there is nothing to
        # decouple. Assert the state rather than skipping, so a silently
        # different lease backend cannot hide behind a skip.
        assert type(lease).__name__ == "InMemoryExecutorLease", (
            "expected the in-memory lease after the per-test reset, got %s"
            % type(lease).__name__
        )
        return
    store_conn = getattr(get_audit_store(), "_conn", None)
    assert conn is not store_conn, (
        "the fence is holding the same sqlite connection object as the current "
        "audit store; after a per-test reset it must be rebuilt in-memory"
    )


def test_audit_writes_land_in_this_tests_own_database():
    """真实审计写入必须落在本用例的库里 —— 用真实 ``log_event`` 走产品路径。"""
    import os

    from src.kernels.audit import AuditEventType, AuditScope, log_event

    db = Path(str(get_audit_store()._db_path)).resolve()
    assert db == Path(os.environ["AUDIT_DB_PATH"]).resolve(), (
        "the audit write went to %s instead of this test's declared database" % db
    )

    log_event(
        AuditEventType.ACCESS_CHECK,
        "f1-regression-human",
        AuditScope.L7,
        "allow",
        details={"purpose": "prove per-test audit isolation"},
        correlation_id="f1-regression-corr",
    )

    assert db.exists(), "no audit database was created at %s" % db
    rows = get_audit_store().query_events(correlation_id="f1-regression-corr")
    assert rows, (
        "the event this test wrote is not readable from the same store -- "
        "the write and the read are looking at different databases"
    )
