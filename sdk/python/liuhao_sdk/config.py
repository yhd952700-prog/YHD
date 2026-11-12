"""
LiuHao AI OS SDK Configuration.

Provides configuration management for the LiuHao SDK, supporting
environment variables, configuration files (JSON/YAML), and programmatic
configuration.
"""

import os
import json
from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, Any, List
from pathlib import Path

try:
    import yaml  # type: ignore
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False


# Environment variable prefix
ENV_PREFIX = "LIUHAO_"


@dataclass
class Config:
    """Configuration for the LiuHao SDK client.

    All settings can be configured programmatically, via environment
    variables (prefixed with LIUHAO_), or via a configuration file.

    Environment variable mapping:
        LIUHAO_API_KEY -> api_key
        LIUHAO_BASE_URL -> base_url
        LIUHAO_TIMEOUT -> timeout
        LIUHAO_MAX_RETRIES -> max_retries
        LIUHAO_DEBUG -> debug

    Attributes:
        api_key: API key for authentication with the LiuHao platform.
        base_url: Base URL of the LiuHao API endpoint.
        timeout: Request timeout in seconds.
        max_retries: Maximum number of retry attempts for failed requests.
        retry_backoff_factor: Backoff factor for exponential retry delay.
        retry_status_codes: HTTP status codes that trigger a retry.
        debug: Enable debug logging mode.
        verify_ssl: Whether to verify SSL certificates.
        proxy: Optional proxy URL for HTTP requests.
        custom_headers: Additional headers to send with every request.
        organization_id: Optional organization ID for multi-tenant setups.
        project_id: Optional project/tenant identifier.
    """

    api_key: Optional[str] = None
    base_url: str = "https://api.liuhao.ai/v1"
    timeout: float = 30.0
    max_retries: int = 3
    retry_backoff_factor: float = 0.5
    retry_status_codes: List[int] = field(
        default_factory=lambda: [429, 500, 502, 503, 504]
    )
    debug: bool = False
    verify_ssl: bool = True
    proxy: Optional[str] = None
    custom_headers: Dict[str, str] = field(default_factory=dict)
    organization_id: Optional[str] = None
    project_id: Optional[str] = None

    # ------------------------------------------------------------------
    # Factory methods
    # ------------------------------------------------------------------

    @classmethod
    def from_env(cls) -> "Config":
        """Create a Config instance from environment variables.

        Reads all configuration values from environment variables
        prefixed with ``LIUHAO_``.

        Returns:
            A Config instance populated from environment variables.
        """
        kwargs: Dict[str, Any] = {}

        # Simple string values
        env_map = {
            "API_KEY": "api_key",
            "BASE_URL": "base_url",
            "PROXY": "proxy",
            "ORGANIZATION_ID": "organization_id",
            "PROJECT_ID": "project_id",
        }
        for env_key, attr in env_map.items():
            value = os.environ.get(f"{ENV_PREFIX}{env_key}")
            if value is not None:
                kwargs[attr] = value

        # Numeric values
        numeric_map = {
            "TIMEOUT": ("timeout", float),
            "MAX_RETRIES": ("max_retries", int),
            "RETRY_BACKOFF_FACTOR": ("retry_backoff_factor", float),
        }
        for env_key, (attr, converter) in numeric_map.items():
            value = os.environ.get(f"{ENV_PREFIX}{env_key}")
            if value is not None:
                try:
                    kwargs[attr] = converter(value)
                except (ValueError, TypeError):
                    pass  # Keep default on parse error

        # Boolean values
        bool_map = {
            "DEBUG": "debug",
            "VERIFY_SSL": "verify_ssl",
        }
        for env_key, attr in bool_map.items():
            value = os.environ.get(f"{ENV_PREFIX}{env_key}")
            if value is not None:
                kwargs[attr] = value.lower() in ("1", "true", "yes", "on")

        # List of retry status codes
        retry_codes = os.environ.get(f"{ENV_PREFIX}RETRY_STATUS_CODES")
        if retry_codes:
            try:
                kwargs["retry_status_codes"] = [
                    int(c.strip()) for c in retry_codes.split(",")
                ]
            except (ValueError, TypeError):
                pass

        # Custom headers (JSON encoded)
        custom_headers = os.environ.get(f"{ENV_PREFIX}CUSTOM_HEADERS")
        if custom_headers:
            try:
                kwargs["custom_headers"] = json.loads(custom_headers)
            except (json.JSONDecodeError, TypeError):
                pass

        return cls(**kwargs)

    @classmethod
    def from_file(cls, file_path: str) -> "Config":
        """Load configuration from a JSON or YAML file.

        Args:
            file_path: Path to the configuration file.
                Supports .json, .yaml, and .yml extensions.

        Returns:
            A Config instance populated from the file.

        Raises:
            FileNotFoundError: If the configuration file does not exist.
            ValueError: If the file format is unsupported or parsing fails.
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Configuration file not found: {file_path}")

        suffix = path.suffix.lower()
        data: Dict[str, Any] = {}

        if suffix == ".json":
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except json.JSONDecodeError as e:
                raise ValueError(f"Failed to parse JSON config file: {e}") from e
        elif suffix in (".yaml", ".yml"):
            if not _HAS_YAML:
                raise ValueError(
                    "PyYAML is required to load YAML config files. "
                    "Install it with: pip install pyyaml"
                )
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
            except yaml.YAMLError as e:  # type: ignore
                raise ValueError(f"Failed to parse YAML config file: {e}") from e
        else:
            raise ValueError(
                f"Unsupported config file format: {suffix}. "
                "Supported formats: .json, .yaml, .yml"
            )

        if not isinstance(data, dict):
            raise ValueError("Configuration file must contain a top-level mapping/object.")

        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    @classmethod
    def load(cls, file_path: Optional[str] = None) -> "Config":
        """Load configuration with layered precedence.

        Configuration sources are merged in the following order
        (later sources override earlier ones):
            1. Default values (from dataclass defaults)
            2. Configuration file (if provided)
            3. Environment variables

        Args:
            file_path: Optional path to a configuration file.

        Returns:
            A fully merged Config instance.
        """
        config = cls()  # defaults

        if file_path:
            file_config = cls.from_file(file_path)
            config = config.merge(file_config)

        env_config = cls.from_env()
        config = config.merge(env_config)

        return config

    # ------------------------------------------------------------------
    # Instance methods
    # ------------------------------------------------------------------

    def merge(self, other: "Config") -> "Config":
        """Merge another Config into this one, keeping non-None values from other.

        Args:
            other: The configuration to merge in. Non-None values from
                ``other`` will override values in this config.

        Returns:
            A new Config instance with merged values.
        """
        result = asdict(self)
        other_dict = asdict(other)

        for key, value in other_dict.items():
            # Only override if the other value is not None / not empty for collections
            if value is None:
                continue
            if isinstance(value, (dict, list)) and not value:
                continue
            result[key] = value

        return Config(**result)

    def to_dict(self) -> Dict[str, Any]:
        """Convert the configuration to a dictionary.

        Returns:
            Dictionary representation of the configuration.
        """
        return asdict(self)

    def validate(self) -> List[str]:
        """Validate the configuration and return a list of issues.

        Returns:
            A list of validation error messages. Empty list means valid.
        """
        issues: List[str] = []

        if not self.api_key:
            issues.append("api_key is required for API access")

        if not self.base_url:
            issues.append("base_url must not be empty")
        elif not (self.base_url.startswith("http://") or self.base_url.startswith("https://")):
            issues.append("base_url must start with http:// or https://")

        if self.timeout <= 0:
            issues.append("timeout must be a positive number")

        if self.max_retries < 0:
            issues.append("max_retries must be non-negative")

        if self.retry_backoff_factor < 0:
            issues.append("retry_backoff_factor must be non-negative")

        return issues

    def is_valid(self) -> bool:
        """Check whether the configuration is valid.

        Returns:
            True if validation passes, False otherwise.
        """
        return len(self.validate()) == 0
