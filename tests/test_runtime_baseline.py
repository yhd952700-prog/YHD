from src.api.app import MetricsApplication
from src.core.lifecycle import LifecycleManager


def test_health_and_ready_contracts() -> None:
    application = MetricsApplication("sqlite:///./verify_metrics.db")

    health = application.health()
    ready = application.ready()

    assert health["status"] == "ok"
    assert ready["status"] == "ready"
    assert "database_url" in ready
    assert "provider_samples" in ready


def test_lifecycle_startup_includes_ready_signal() -> None:
    manager = LifecycleManager("sqlite:///./verify_metrics.db")
    startup = manager.startup()

    assert startup["status"] == "started"
    assert startup["ready"]["status"] == "ready"
