"""
Base Solution Template.

Defines the abstract base class for all LiuHao AI OS solution templates.
Solutions follow a standardized lifecycle and provide consistent
interfaces for configuration, deployment, monitoring, and metrics.
"""

from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List
from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime, timezone


class SolutionStatus(str, Enum):
    """Lifecycle status of a solution deployment."""
    UNINITIALIZED = "uninitialized"
    INITIALIZED = "initialized"
    CONFIGURED = "configured"
    DEPLOYED = "deployed"
    RUNNING = "running"
    PAUSED = "paused"
    ERROR = "error"
    TEARDOWN = "teardown"


class SolutionHealth(str, Enum):
    """Health status of a running solution."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


@dataclass
class SolutionMetrics:
    """Performance and usage metrics for a solution.

    Attributes:
        total_requests: Total number of requests processed.
        successful_requests: Number of successful requests.
        failed_requests: Number of failed requests.
        avg_response_time_ms: Average response time in milliseconds.
        error_rate: Error rate as a decimal (0.0 - 1.0).
        uptime_seconds: Total uptime in seconds.
        custom_metrics: Additional solution-specific metrics.
        last_updated: ISO 8601 timestamp of last metrics update.
    """
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    avg_response_time_ms: float = 0.0
    error_rate: float = 0.0
    uptime_seconds: float = 0.0
    custom_metrics: Dict[str, Any] = field(default_factory=dict)
    last_updated: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert metrics to a dictionary."""
        return {
            "total_requests": self.total_requests,
            "successful_requests": self.successful_requests,
            "failed_requests": self.failed_requests,
            "avg_response_time_ms": self.avg_response_time_ms,
            "error_rate": self.error_rate,
            "uptime_seconds": self.uptime_seconds,
            "custom_metrics": self.custom_metrics,
            "last_updated": self.last_updated,
        }


@dataclass
class SolutionMetadata:
    """Metadata describing a solution template.

    Attributes:
        name: Unique solution identifier/name.
        version: Semantic version string.
        industry: Target industry vertical.
        description: Human-readable solution description.
        author: Solution author/organization.
        tags: Categorization tags.
        min_liuhao_version: Minimum LiuHao OS version required.
    """
    name: str
    version: str = "1.0.0"
    industry: str = "general"
    description: str = ""
    author: str = "LiuHao AI OS"
    tags: List[str] = field(default_factory=list)
    min_liuhao_version: str = "1.0.0"


@dataclass
class SolutionConfigSchema:
    """Schema definition for solution configuration.

    Describes the configuration parameters that a solution accepts,
    enabling validation and UI generation.

    Attributes:
        fields: Dictionary mapping field names to field definitions.
            Each field definition contains: type, required, default,
            description, and optionally valid values.
    """
    fields: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def add_field(
        self,
        name: str,
        field_type: str = "string",
        required: bool = False,
        default: Any = None,
        description: str = "",
        valid_values: Optional[List[Any]] = None,
    ) -> None:
        """Add a configuration field definition.

        Args:
            name: Field name (key in config dict).
            field_type: Data type (string, int, float, bool, list, dict).
            required: Whether the field is required.
            default: Default value if not provided.
            description: Human-readable field description.
            valid_values: Optional list of allowed values.
        """
        self.fields[name] = {
            "type": field_type,
            "required": required,
            "default": default,
            "description": description,
            "valid_values": valid_values,
        }

    def validate(self, config: Dict[str, Any]) -> List[str]:
        """Validate a configuration dictionary against the schema.

        Args:
            config: Configuration dictionary to validate.

        Returns:
            List of validation error messages (empty if valid).
        """
        errors: List[str] = []

        for field_name, field_def in self.fields.items():
            # Check required fields
            if field_def["required"] and field_name not in config:
                errors.append(f"Required field '{field_name}' is missing")
                continue

            # Skip validation for missing optional fields
            if field_name not in config:
                continue

            value = config[field_name]
            expected_type = field_def["type"]

            # Type validation
            type_ok = True
            if expected_type == "string":
                type_ok = isinstance(value, str)
            elif expected_type == "int":
                type_ok = isinstance(value, int) and not isinstance(value, bool)
            elif expected_type == "float":
                type_ok = isinstance(value, (int, float)) and not isinstance(value, bool)
            elif expected_type == "bool":
                type_ok = isinstance(value, bool)
            elif expected_type == "list":
                type_ok = isinstance(value, list)
            elif expected_type == "dict":
                type_ok = isinstance(value, dict)

            if not type_ok:
                errors.append(
                    f"Field '{field_name}' expected type '{expected_type}', "
                    f"got '{type(value).__name__}'"
                )
                continue

            # Valid values check
            valid_values = field_def.get("valid_values")
            if valid_values is not None and value not in valid_values:
                errors.append(
                    f"Field '{field_name}' value '{value}' is not in "
                    f"allowed values: {valid_values}"
                )

        return errors

    def apply_defaults(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Apply default values to a configuration dictionary.

        Args:
            config: Configuration dictionary to populate with defaults.

        Returns:
            New config dictionary with defaults applied.
        """
        result = dict(config)
        for field_name, field_def in self.fields.items():
            if field_name not in result and field_def["default"] is not None:
                result[field_name] = field_def["default"]
        return result


class BaseSolution(ABC):
    """Abstract base class for all LiuHao AI OS solution templates.

    Solutions follow a standardized lifecycle:
        initialize -> configure -> deploy -> run -> monitor -> teardown

    Subclasses must implement the abstract methods to provide
    solution-specific behavior.

    Attributes:
        metadata: Solution metadata (name, version, industry, etc.).
        config: Current solution configuration.
        status: Current lifecycle status.
        health: Current health status.
        metrics: Current solution metrics.
        config_schema: Configuration schema for validation.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        """Initialize the solution.

        Args:
            config: Optional initial configuration dictionary.
        """
        self.metadata = self._build_metadata()
        self.config: Dict[str, Any] = config or {}
        self.status: SolutionStatus = SolutionStatus.UNINITIALIZED
        self.health: SolutionHealth = SolutionHealth.UNKNOWN
        self.metrics: SolutionMetrics = SolutionMetrics()
        self.config_schema: SolutionConfigSchema = SolutionConfigSchema()
        self._start_time: Optional[datetime] = None

        # Build the solution's config schema
        self._build_config_schema()

    # ------------------------------------------------------------------
    # Abstract methods (must be implemented by subclasses)
    # ------------------------------------------------------------------

    @abstractmethod
    def _build_metadata(self) -> SolutionMetadata:
        """Build and return the solution metadata.

        Returns:
            SolutionMetadata describing the solution.
        """
        ...

    @abstractmethod
    def _build_config_schema(self) -> None:
        """Populate the config_schema with solution-specific fields.

        Called during initialization to define what configuration
        parameters the solution accepts.
        """
        ...

    @abstractmethod
    def _on_initialize(self) -> None:
        """Solution-specific initialization logic.

        Called during :meth:`initialize`. Subclasses should set up
        data structures, connect to resources, etc.
        """
        ...

    @abstractmethod
    def _on_deploy(self) -> None:
        """Solution-specific deployment logic.

        Called during :meth:`deploy`. Subclasses should create agents,
        knowledge bases, workflows, and other platform resources.
        """
        ...

    @abstractmethod
    def _on_run(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """Solution-specific execution logic.

        Called during :meth:`run`. Subclasses implement the core
        solution workflow here.

        Args:
            input_data: Input data for the solution run.

        Returns:
            Result dictionary with solution output.
        """
        ...

    @abstractmethod
    def _on_teardown(self) -> None:
        """Solution-specific teardown logic.

        Called during :meth:`teardown`. Subclasses should clean up
        resources, delete agents, and perform final cleanup.
        """
        ...

    # ------------------------------------------------------------------
    # Lifecycle methods
    # ------------------------------------------------------------------

    def initialize(self) -> None:
        """Initialize the solution.

        Sets up internal state and calls solution-specific initialization.

        Raises:
            RuntimeError: If the solution is already initialized.
        """
        if self.status != SolutionStatus.UNINITIALIZED:
            raise RuntimeError(
                f"Cannot initialize: solution is in state '{self.status.value}'"
            )

        self._on_initialize()
        self.status = SolutionStatus.INITIALIZED
        self.health = SolutionHealth.UNKNOWN

    def configure(self, config: Dict[str, Any]) -> None:
        """Configure the solution with a configuration dictionary.

        Validates the config against the schema and applies defaults.

        Args:
            config: Configuration dictionary.

        Raises:
            ValueError: If configuration validation fails.
            RuntimeError: If the solution hasn't been initialized.
        """
        if self.status == SolutionStatus.UNINITIALIZED:
            raise RuntimeError("Solution must be initialized before configuring")

        # Validate config
        errors = self.config_schema.validate(config)
        if errors:
            raise ValueError(
                f"Configuration validation failed: {'; '.join(errors)}"
            )

        # Apply defaults and store
        self.config = self.config_schema.apply_defaults(config)
        self.status = SolutionStatus.CONFIGURED

    def deploy(self) -> None:
        """Deploy the solution onto the LiuHao platform.

        Creates required agents, knowledge bases, workflows, and
        other resources.

        Raises:
            RuntimeError: If the solution hasn't been configured.
        """
        if self.status not in (SolutionStatus.CONFIGURED, SolutionStatus.DEPLOYED):
            raise RuntimeError(
                f"Cannot deploy: solution must be configured first "
                f"(current state: {self.status.value})"
            )

        self._on_deploy()
        self.status = SolutionStatus.DEPLOYED
        self.health = SolutionHealth.HEALTHY

    def run(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the solution with the given input.

        Args:
            input_data: Input data for the solution run.

        Returns:
            Result dictionary with solution output.

        Raises:
            RuntimeError: If the solution hasn't been deployed.
        """
        if self.status not in (SolutionStatus.DEPLOYED, SolutionStatus.RUNNING):
            raise RuntimeError(
                f"Cannot run: solution must be deployed first "
                f"(current state: {self.status.value})"
            )

        start_time = datetime.now(timezone.utc)
        self.status = SolutionStatus.RUNNING

        if self._start_time is None:
            self._start_time = start_time

        try:
            result = self._on_run(input_data)
            self.metrics.successful_requests += 1
            self.health = SolutionHealth.HEALTHY
            return result
        except Exception as e:
            self.metrics.failed_requests += 1
            self.health = SolutionHealth.DEGRADED
            raise
        finally:
            end_time = datetime.now(timezone.utc)
            duration_ms = (end_time - start_time).total_seconds() * 1000
            self.metrics.total_requests += 1
            self._update_avg_response_time(duration_ms)
            self._update_error_rate()
            self._update_uptime()
            self.metrics.last_updated = end_time.isoformat()

            if self.status == SolutionStatus.RUNNING:
                self.status = SolutionStatus.DEPLOYED

    def monitor(self) -> Dict[str, Any]:
        """Check the solution's health and return current status.

        Returns:
            Dictionary with status, health, and metrics information.
        """
        # Re-check health
        self.health = self._check_health()

        return {
            "name": self.metadata.name,
            "version": self.metadata.version,
            "status": self.status.value,
            "health": self.health.value,
            "metrics": self.metrics.to_dict(),
        }

    def pause(self) -> None:
        """Pause the solution, temporarily stopping processing."""
        if self.status not in (SolutionStatus.DEPLOYED, SolutionStatus.RUNNING):
            raise RuntimeError(
                f"Cannot pause: solution must be deployed "
                f"(current state: {self.status.value})"
            )
        self.status = SolutionStatus.PAUSED

    def resume(self) -> None:
        """Resume a paused solution."""
        if self.status != SolutionStatus.PAUSED:
            raise RuntimeError(
                f"Cannot resume: solution is not paused "
                f"(current state: {self.status.value})"
            )
        self.status = SolutionStatus.DEPLOYED

    def teardown(self) -> None:
        """Tear down the solution, cleaning up all resources.

        Raises:
            RuntimeError: If the solution hasn't been initialized.
        """
        if self.status == SolutionStatus.UNINITIALIZED:
            raise RuntimeError("Solution was never initialized")

        self._on_teardown()
        self.status = SolutionStatus.TEARDOWN
        self.health = SolutionHealth.UNKNOWN

    # ------------------------------------------------------------------
    # Health check and metrics
    # ------------------------------------------------------------------

    def _check_health(self) -> SolutionHealth:
        """Perform a health check on the solution.

        Default implementation derives health from error rate.
        Subclasses can override for more sophisticated checks.

        Returns:
            Current solution health status.
        """
        if self.metrics.total_requests == 0:
            return SolutionHealth.UNKNOWN

        error_rate = self.metrics.error_rate
        if error_rate == 0:
            return SolutionHealth.HEALTHY
        elif error_rate < 0.1:
            return SolutionHealth.DEGRADED
        else:
            return SolutionHealth.UNHEALTHY

    def _update_avg_response_time(self, duration_ms: float) -> None:
        """Update the average response time with a new data point.

        Uses exponential moving average for efficiency.

        Args:
            duration_ms: Latest response time in milliseconds.
        """
        if self.metrics.total_requests <= 1:
            self.metrics.avg_response_time_ms = duration_ms
        else:
            alpha = 0.1  # EMA smoothing factor
            self.metrics.avg_response_time_ms = (
                alpha * duration_ms
                + (1 - alpha) * self.metrics.avg_response_time_ms
            )

    def _update_error_rate(self) -> None:
        """Update the error rate based on current counts."""
        if self.metrics.total_requests > 0:
            self.metrics.error_rate = (
                self.metrics.failed_requests / self.metrics.total_requests
            )

    def _update_uptime(self) -> None:
        """Update the uptime counter."""
        if self._start_time is not None:
            self.metrics.uptime_seconds = (
                datetime.now(timezone.utc) - self._start_time
            ).total_seconds()

    # ------------------------------------------------------------------
    # Utility methods
    # ------------------------------------------------------------------

    def get_info(self) -> Dict[str, Any]:
        """Get basic information about the solution.

        Returns:
            Dictionary with solution metadata and status.
        """
        return {
            "name": self.metadata.name,
            "version": self.metadata.version,
            "industry": self.metadata.industry,
            "description": self.metadata.description,
            "author": self.metadata.author,
            "tags": list(self.metadata.tags),
            "status": self.status.value,
            "health": self.health.value,
        }

    def __repr__(self) -> str:
        """Return a string representation of the solution."""
        return (
            f"{self.__class__.__name__}("
            f"name={self.metadata.name!r}, "
            f"version={self.metadata.version!r}, "
            f"status={self.status.value!r})"
        )
