from src.api.app import MetricsApplication
from src.core.lifecycle import LifecycleManager


def test_health_and_ready_contracts(tmp_path) -> None:
    # 用 tmp_path 而不是相对 cwd 的 ``./verify_metrics.db``：否则每跑一次测试
    # 都会往代码根写一个库（历史遗留的 cwd 写盘习惯）。
    url = f"sqlite:///{(tmp_path / 'verify_metrics.db').as_posix()}"
    application = MetricsApplication(url)

    health = application.health()
    ready = application.ready()

    assert health["status"] == "ok"
    assert ready["status"] == "ready"
    assert "database_url" in ready
    assert "provider_samples" in ready


def test_lifecycle_startup_includes_ready_signal(tmp_path) -> None:
    url = f"sqlite:///{tmp_path.as_posix()}/verify_metrics.db"
    manager = LifecycleManager(url)
    startup = manager.startup()

    assert startup["status"] == "started"
    assert startup["ready"]["status"] == "ready"
