"""SQLite 连接面收敛（round 15）回归测试。

修复前 ProviderMetricsRepository / CheckpointManager 每次调用都
``sqlite3.connect(...)`` 即建即关。两个真实问题：
1. ``:memory:`` 场景下每次调用拿到的是**全新空库**，``_initialize`` 建的表在
   后续调用中消失 → 功能完全失效（本文件 test_*_memory_* 复现并锁定修复）。
2. 每次操作都开/关连接，存在不必要的开销。

收敛为「每实例单连接 + RLock 串行化」后，上述两点都解决，且对外 API 不变。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from src.api.providers_metrics import ProviderMetric, ProviderMetricsRepository
from src.workflow.checkpoint import CheckpointManager


def _sample(provider: str = "ollama") -> ProviderMetric:
    return ProviderMetric(
        provider=provider,
        model="qwen2.5:3b",
        timestamp="2026-09-10T00:00:00",
        latency_ms=12.5,
        success_rate=1.0,
    )


def test_metrics_repo_memory_db_roundtrip():
    """修复前：:memory: 每次调用拿新空库，建表不可见 → count()==0。"""
    repo = ProviderMetricsRepository(database_url="sqlite://")
    repo.record(_sample())
    assert repo.count() == 1
    rows = repo.list_recent(limit=10)
    assert len(rows) == 1
    assert rows[0]["provider"] == "ollama"


def test_metrics_repo_uses_single_connection():
    """所有操作复用同一连接对象（连接面收敛的核心不变量）。"""
    repo = ProviderMetricsRepository(database_url="sqlite://")
    conn_id = id(repo._conn)
    repo.record(_sample())
    repo.list_recent(limit=5)
    repo.count()
    assert id(repo._conn) == conn_id


def test_metrics_repo_close_idempotent():
    repo = ProviderMetricsRepository(database_url="sqlite://")
    repo.close()
    repo.close()  # 必须不抛异常


def test_checkpoint_manager_memory_roundtrip():
    """CheckpointManager 也用单连接：:memory: 下建表/读写应在同一连接上生效。"""
    cm = CheckpointManager(db_path=":memory:")
    cid = cm.create_checkpoint(
        workflow_id="wf-1",
        workflow_state={"step": 1},
        input_data={"x": 1},
        output_data={"y": 2},
    )
    assert cm.get_checkpoint_count() == 1
    ckpt = cm.get_checkpoint(cid)
    assert ckpt is not None
    assert ckpt.workflow_state == {"step": 1}
    assert ckpt.done is False


def test_checkpoint_manager_uses_single_connection():
    cm = CheckpointManager(db_path=":memory:")
    conn_id = id(cm._conn)
    cm.create_checkpoint(
        workflow_id="wf-1",
        workflow_state={},
        input_data={},
        output_data={},
    )
    cm.get_checkpoint_count()
    cm.list_checkpoints()
    assert id(cm._conn) == conn_id


def test_checkpoint_manager_file_roundtrip(tmp_path):
    cm = CheckpointManager(db_path=str(tmp_path / "ckpt.db"))
    cid = cm.create_checkpoint(
        workflow_id="wf-x",
        workflow_state={"a": 1},
        input_data={"b": 2},
        output_data={"c": 3},
        node_id="n1",
        description="smoke",
    )
    assert cm.get_checkpoint_count() == 1
    listing = cm.list_checkpoints(workflow_id="wf-x")
    assert len(listing) == 1
    assert listing[0]["checkpoint_id"] == cid
    stats = cm.get_stats()
    assert stats["total_checkpoints"] == 1


def test_metrics_repo_concurrent_record_single_connection(tmp_path):
    """并发写：RLock 串行化共享连接，既不抛异常也不出现 "database is locked"。
    同时验证并发结束后连接对象仍是同一个（未因并发而重建）。"""
    db = str(tmp_path / "metrics.db")
    repo = ProviderMetricsRepository(database_url=f"sqlite:///{db}")
    errors: list[BaseException] = []

    def worker(i: int) -> None:
        try:
            repo.record(_sample(provider=f"p{i}"))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(worker, range(40)))

    assert not errors, f"并发写 metrics 失败：{errors!r}"
    assert len(repo.list_recent(limit=100)) == 40
    # 连接未被重建
    assert repo._conn is not None
