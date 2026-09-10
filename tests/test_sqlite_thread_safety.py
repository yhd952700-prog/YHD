"""SQLite 即建即关存储的并发安全回归测试。

第 9 轮修复了 audit store（全局长连接单例）的跨线程缺陷。本轮排查其余 SQLite 使用点：
- ProviderMetricsRepository / CheckpointManager 都采用 ``with sqlite3.connect(...)``
  即建即关模式（每次操作独立连接），本身线程安全；但显式加 ``check_same_thread=False``
  防御（风格与已修复的 audit 一致），本测试验证多线程并发读写不抛异常。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from src.api.providers_metrics import ProviderMetric, ProviderMetricsRepository
from src.workflow.checkpoint import CheckpointManager


def test_metrics_repo_concurrent_record_and_query(tmp_path):
    db = str(tmp_path / "metrics.db")
    repo = ProviderMetricsRepository(database_url=f"sqlite:///{db}")
    errors: list[BaseException] = []

    def worker(i: int) -> None:
        try:
            repo.record(
                ProviderMetric(
                    provider="ollama",
                    model="qwen2.5:3b",
                    timestamp=f"2026-09-09T00:0{i}:00",
                    latency_ms=float(i),
                    success_rate=1.0,
                )
            )
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(worker, range(40)))

    assert not errors, f"并发写 metrics 失败：{errors!r}"
    assert len(repo.list_recent(limit=100)) == 40


def test_checkpoint_manager_concurrent_create(tmp_path):
    """CheckpointManager 采用 with sqlite3.connect(...) 即建即关模式，验证多线程
    并发写不抛异常（线程安全）。

    顺带修复了一个独立真实 bug：create_checkpoint 的 INSERT 原少写 1 个占位符
    （17 个 ? 对 18 列），单线程即报 "17 values for 18 columns"，功能完全不可用；
    已补占位符，本测试改用真正的并发写验证。
    """
    cm = CheckpointManager(db_path=str(tmp_path / "ckpt.db"))
    errors: list[BaseException] = []

    def worker(i: int) -> None:
        try:
            cm.create_checkpoint(
                workflow_id=f"wf-{i // 4}",
                workflow_state={"step": i},
                input_data={"x": i},
                output_data={"y": i * 2},
                node_id=f"node-{i}",
                checkpoint_type="NODE",
                description=f"step {i}",
            )
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(worker, range(40)))

    assert not errors, f"并发建 checkpoint 失败：{errors!r}"
    assert cm.get_checkpoint_count() == 40
