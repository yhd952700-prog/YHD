"""
Integration Registry for LiuHao AI OS.

Manages registration, discovery, and lifecycle of integration adapters,
including connection pooling and health checks.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Type

from .base_adapter import (
    BaseIntegrationAdapter,
    ConnectionStatus,
    IntegrationConfig,
    IntegrationStatus,
)


logger = logging.getLogger(__name__)


@dataclass
class AdapterEntry:
    """Registry entry for a single adapter instance."""
    name: str
    adapter: BaseIntegrationAdapter
    adapter_class: Type[BaseIntegrationAdapter]
    config: IntegrationConfig
    created_at: float
    last_health_check: Optional[float] = None
    last_health_status: Optional[bool] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RegistryHealthReport:
    """Aggregated health report for all registered adapters."""
    total_adapters: int
    connected_count: int
    disconnected_count: int
    error_count: int
    rate_limited_count: int
    adapter_statuses: Dict[str, IntegrationStatus]
    checked_at: float


class IntegrationRegistry:
    """
    Central registry for integration adapters in LiuHao AI OS.

    Provides:
    - Registration and discovery of adapters by name
    - Connection pool management
    - Health checking for all integrations
    - Factory methods for creating adapter instances

    This class is thread-safe.
    """

    def __init__(self):
        """Initialize the integration registry."""
        self._adapters: Dict[str, AdapterEntry] = {}
        self._adapter_classes: Dict[str, Type[BaseIntegrationAdapter]] = {}
        self._lock = threading.RLock()
        self._health_check_interval: float = 60.0  # seconds
        logger.info("IntegrationRegistry initialized")

    # ------------------------------------------------------------------
    # Adapter class registration
    # ------------------------------------------------------------------

    def register_adapter_class(self, name: str,
                               adapter_class: Type[BaseIntegrationAdapter]) -> None:
        """
        Register an adapter class that can be instantiated later.

        Args:
            name: Unique name for this adapter type.
            adapter_class: The adapter class (not instance).
        """
        with self._lock:
            self._adapter_classes[name] = adapter_class
            logger.info("Registered adapter class: %s", name)

    def discover_adapter_classes(self) -> Dict[str, Type[BaseIntegrationAdapter]]:
        """
        Get all registered adapter classes.

        Returns:
            Dictionary mapping adapter names to their classes.
        """
        with self._lock:
            return dict(self._adapter_classes)

    # ------------------------------------------------------------------
    # Adapter instance registration
    # ------------------------------------------------------------------

    def register(self, adapter: BaseIntegrationAdapter,
                 metadata: Optional[Dict[str, Any]] = None) -> None:
        """
        Register an existing adapter instance.

        Args:
            adapter: The adapter instance to register.
            metadata: Optional metadata about the adapter.
        """
        with self._lock:
            name = adapter.config.name
            if name in self._adapters:
                logger.warning("Overwriting existing adapter: %s", name)

            entry = AdapterEntry(
                name=name,
                adapter=adapter,
                adapter_class=type(adapter),
                config=adapter.config,
                created_at=time.time(),
                metadata=metadata or {},
            )
            self._adapters[name] = entry
            logger.info("Registered adapter instance: %s", name)

    def create_and_register(self,
                            adapter_type: str,
                            config: IntegrationConfig,
                            auto_connect: bool = False,
                            metadata: Optional[Dict[str, Any]] = None
                            ) -> BaseIntegrationAdapter:
        """
        Create a new adapter instance from a registered class and register it.

        Args:
            adapter_type: Name of the registered adapter class.
            config: Configuration for the new adapter.
            auto_connect: Whether to connect immediately.
            metadata: Optional metadata.

        Returns:
            The newly created adapter instance.

        Raises:
            KeyError: If adapter_type is not a registered class.
        """
        with self._lock:
            if adapter_type not in self._adapter_classes:
                raise KeyError(
                    f"Unknown adapter type: {adapter_type}. "
                    f"Available: {list(self._adapter_classes.keys())}"
                )

            adapter_class = self._adapter_classes[adapter_type]
            adapter = adapter_class(config)

            if auto_connect:
                try:
                    adapter.connect()
                except Exception as e:
                    logger.error(
                        "Failed to auto-connect adapter %s: %s",
                        config.name, e,
                    )

            entry = AdapterEntry(
                name=config.name,
                adapter=adapter,
                adapter_class=adapter_class,
                config=config,
                created_at=time.time(),
                metadata=metadata or {},
            )
            self._adapters[config.name] = entry
            logger.info(
                "Created and registered adapter: %s (type=%s)",
                config.name, adapter_type,
            )
            return adapter

    def unregister(self, name: str, disconnect: bool = True) -> bool:
        """
        Unregister an adapter by name.

        Args:
            name: Name of the adapter to unregister.
            disconnect: Whether to disconnect before removing.

        Returns:
            True if the adapter was found and removed.
        """
        with self._lock:
            if name not in self._adapters:
                return False

            entry = self._adapters.pop(name)
            if disconnect:
                try:
                    entry.adapter.disconnect()
                except Exception as e:
                    logger.warning(
                        "Error disconnecting adapter %s: %s", name, e,
                    )

            logger.info("Unregistered adapter: %s", name)
            return True

    # ------------------------------------------------------------------
    # Adapter lookup and listing
    # ------------------------------------------------------------------

    def get(self, name: str) -> BaseIntegrationAdapter:
        """
        Get an adapter by name.

        Args:
            name: Adapter name.

        Returns:
            The adapter instance.

        Raises:
            KeyError: If no adapter with that name is registered.
        """
        with self._lock:
            if name not in self._adapters:
                raise KeyError(
                    f"Adapter not found: {name}. "
                    f"Available: {list(self._adapters.keys())}"
                )
            return self._adapters[name].adapter

    def get_or_none(self, name: str) -> Optional[BaseIntegrationAdapter]:
        """
        Get an adapter by name, returning None if not found.

        Args:
            name: Adapter name.

        Returns:
            The adapter instance, or None.
        """
        try:
            return self.get(name)
        except KeyError:
            return None

    def list_adapters(self) -> List[str]:
        """
        List all registered adapter names.

        Returns:
            List of adapter names.
        """
        with self._lock:
            return list(self._adapters.keys())

    def list_connected(self) -> List[str]:
        """
        List names of all currently connected adapters.

        Returns:
            List of connected adapter names.
        """
        with self._lock:
            return [
                name for name, entry in self._adapters.items()
                if entry.adapter.is_connected()
            ]

    # ------------------------------------------------------------------
    # Connection pool management
    # ------------------------------------------------------------------

    def connect_all(self) -> Dict[str, bool]:
        """
        Attempt to connect all registered adapters.

        Returns:
            Dictionary mapping adapter names to success status.
        """
        results: Dict[str, bool] = {}
        with self._lock:
            for name, entry in self._adapters.items():
                try:
                    results[name] = entry.adapter.connect()
                except Exception as e:
                    logger.error("Error connecting adapter %s: %s", name, e)
                    results[name] = False
        logger.info("Connect all complete: %d/%d succeeded",
                    sum(results.values()), len(results))
        return results

    def disconnect_all(self) -> Dict[str, bool]:
        """
        Disconnect all registered adapters.

        Returns:
            Dictionary mapping adapter names to success status.
        """
        results: Dict[str, bool] = {}
        with self._lock:
            for name, entry in self._adapters.items():
                try:
                    results[name] = entry.adapter.disconnect()
                except Exception as e:
                    logger.error("Error disconnecting adapter %s: %s", name, e)
                    results[name] = False
        return results

    # ------------------------------------------------------------------
    # Health checks
    # ------------------------------------------------------------------

    def health_check(self, name: str) -> bool:
        """
        Run a health check on a specific adapter.

        Args:
            name: Adapter name.

        Returns:
            True if the adapter is healthy.
        """
        with self._lock:
            if name not in self._adapters:
                return False

            entry = self._adapters[name]
            try:
                healthy = entry.adapter.test_connection()
            except Exception as e:
                logger.warning("Health check failed for %s: %s", name, e)
                healthy = False

            entry.last_health_check = time.time()
            entry.last_health_status = healthy
            return healthy

    def health_check_all(self) -> RegistryHealthReport:
        """
        Run health checks on all registered adapters.

        Returns:
            RegistryHealthReport with aggregated status.
        """
        statuses: Dict[str, IntegrationStatus] = {}
        connected = 0
        disconnected = 0
        error = 0
        rate_limited = 0

        with self._lock:
            for name, entry in self._adapters.items():
                try:
                    is_healthy = entry.adapter.test_connection()
                    entry.last_health_check = time.time()
                    entry.last_health_status = is_healthy
                except Exception as e:
                    logger.warning("Health check error for %s: %s", name, e)
                    entry.last_health_check = time.time()
                    entry.last_health_status = False

                try:
                    status = entry.adapter.get_status()
                except Exception as e:
                    logger.warning("Status error for %s: %s", name, e)
                    from .base_adapter import IntegrationStatus as IS
                    status = IS(
                        name=name,
                        status=ConnectionStatus.ERROR,
                        is_connected=False,
                        uptime_seconds=0.0,
                        requests_sent=0,
                        errors_count=0,
                        rate_limit_hit_count=0,
                        last_error=str(e),
                    )

                statuses[name] = status

                if status.status == ConnectionStatus.CONNECTED:
                    connected += 1
                elif status.status == ConnectionStatus.DISCONNECTED:
                    disconnected += 1
                elif status.status == ConnectionStatus.ERROR:
                    error += 1
                elif status.status == ConnectionStatus.RATE_LIMITED:
                    rate_limited += 1
                else:
                    disconnected += 1

        report = RegistryHealthReport(
            total_adapters=len(statuses),
            connected_count=connected,
            disconnected_count=disconnected,
            error_count=error,
            rate_limited_count=rate_limited,
            adapter_statuses=statuses,
            checked_at=time.time(),
        )
        logger.info(
            "Health check complete: %d total, %d connected, %d errors",
            report.total_adapters, report.connected_count, report.error_count,
        )
        return report

    def get_all_statuses(self) -> Dict[str, IntegrationStatus]:
        """
        Get status for all registered adapters.

        Returns:
            Dictionary mapping adapter names to their status.
        """
        statuses: Dict[str, IntegrationStatus] = {}
        with self._lock:
            for name, entry in self._adapters.items():
                try:
                    statuses[name] = entry.adapter.get_status()
                except Exception as e:
                    logger.warning("Status error for %s: %s", name, e)
        return statuses

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        with self._lock:
            return len(self._adapters)

    def __contains__(self, name: str) -> bool:
        with self._lock:
            return name in self._adapters

    def __repr__(self) -> str:
        with self._lock:
            return (
                f"<IntegrationRegistry adapters={len(self._adapters)} "
                f"classes={len(self._adapter_classes)}>"
            )


# Global singleton registry
_global_registry: Optional[IntegrationRegistry] = None


def get_global_registry() -> IntegrationRegistry:
    """
    Get or create the global integration registry singleton.

    Returns:
        The global IntegrationRegistry instance.
    """
    global _global_registry
    if _global_registry is None:
        _global_registry = IntegrationRegistry()
    return _global_registry
