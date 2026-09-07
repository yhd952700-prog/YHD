from __future__ import annotations

from typing import Any

from src.api.app import MetricsApplication


class LifecycleManager:
    """Startup and shutdown container for the runtime."""

    def __init__(self, database_url: str | None = None) -> None:
        self.application = MetricsApplication(database_url=database_url)

    def startup(self) -> dict[str, Any]:
        return {
            "status": "started",
            "health": self.application.health(),
            "ready": self.application.ready(),
        }

    def ready(self) -> dict[str, Any]:
        return self.application.ready()

    def shutdown(self) -> dict[str, Any]:
        return {"status": "stopped"}
