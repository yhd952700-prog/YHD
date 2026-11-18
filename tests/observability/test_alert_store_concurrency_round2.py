"""AlertStore 并发回归（完善第二轮 A3 压测复现的真缺陷）。

复现证据：三轮压测叠加时网关日志出现 2 次
``dictionary changed size during iteration``，栈在
``AlertStore._save`` 锁外构建 ``self._alerts`` 快照处，告警落盘失败
（「告警未能保存 latency_p99_high」）。根因：只有文件写入被跨实例
_SAVE_LOCK 串行化，同一实例的字典改/迭代没有任何锁。

修复：实例级 RLock 覆盖「改字典 + _save」与全部读快照。
本测试多线程并发 emit/resolve/list/stats/verify，要求零异常、
告警零丢失、落盘可重载。
"""

from __future__ import annotations

import threading

from src.observability.alerts.models import Alert, AlertSeverity, AlertType
from src.observability.alerts.store import AlertStore


def _alert(i: int) -> Alert:
    return Alert(
        id=f"alert-{i}",
        alert_type=AlertType.METRIC_THRESHOLD,
        name=f"并发告警 {i}",
        severity=AlertSeverity.HIGH,
        message=f"stress alert {i}",
        source="a3-regression",
        metric_name="latency_p99_ms",
    )


def test_concurrent_emit_resolve_reads_no_errors_no_loss(tmp_path):
    store = AlertStore(storage_path=str(tmp_path / "alerts.json"))
    errors: list[BaseException] = []
    n_threads, per_thread = 12, 40

    def emitter(base: int) -> None:
        try:
            for i in range(per_thread):
                store.emit_alert(_alert(base + i), dispatch=False)
        except BaseException as exc:  # noqa: BLE001 - 收齐再断言
            errors.append(exc)

    def reader() -> None:
        try:
            for _ in range(per_thread):
                store.list_firing()
                store.list_alerts()
                store.get_stats()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    def resolver() -> None:
        try:
            for i in range(per_thread):
                store.resolve_alert(f"alert-{i}")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = (
        [threading.Thread(target=emitter, args=(t * per_thread,)) for t in range(n_threads)]
        + [threading.Thread(target=reader) for _ in range(3)]
        + [threading.Thread(target=resolver) for _ in range(2)]
    )
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"并发下不该有异常（修复前会抛 dictionary changed）: {errors[:3]}"
    total = n_threads * per_thread
    assert store.get_stats()["total_alerts"] == total
    # 落盘可重载且数量一致（告警零丢失）
    reloaded = AlertStore(storage_path=str(tmp_path / "alerts.json"))
    assert reloaded.get_stats()["total_alerts"] == total
