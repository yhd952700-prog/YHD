"""
Production Configuration for LiuHao AI OS

Centralized production configuration with environment-specific overrides.
"""

import os
from pathlib import Path
from typing import Dict, Any, Optional
from dataclasses import dataclass, field, asdict
from enum import Enum


class Environment(Enum):
    """Deployment environment."""
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


@dataclass
class DatabaseConfig:
    """Database configuration."""
    url: str = "sqlite:///./production.db"
    pool_size: int = 10
    max_overflow: int = 20
    pool_timeout: int = 30
    pool_recycle: int = 3600
    echo: bool = False


@dataclass
class RedisConfig:
    """Redis configuration."""
    host: str = "localhost"
    port: int = 6379
    db: int = 0
    password: Optional[str] = None
    max_connections: int = 50
    socket_timeout: float = 5.0
    socket_connect_timeout: float = 5.0


@dataclass
class SecurityConfig:
    """Security configuration."""
    # API Keys
    api_key_storage_path: str = "data/security"
    api_key_master_key: Optional[str] = None
    default_key_expiry_days: int = 90
    key_rotation_grace_days: int = 7
    
    # JWT
    jwt_secret_key: str = ""  # Must be set in production
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 30
    jwt_refresh_token_expire_days: int = 7
    
    # Encryption
    encryption_master_key: Optional[str] = None
    encryption_algorithm: str = "AES-256-GCM"
    
    # RBAC
    rbac_storage_path: str = "data/security/rbac.json"
    
    # TLS/mTLS
    tls_enabled: bool = True
    tls_cert_path: str = "certs/server.crt"
    tls_key_path: str = "certs/server.key"
    tls_ca_path: str = "certs/ca.crt"
    mtls_enabled: bool = True


@dataclass
class ObservabilityConfig:
    """Observability configuration."""
    # Metrics
    metrics_enabled: bool = True
    metrics_port: int = 9090
    metrics_path: str = "/metrics"
    metrics_interval_seconds: int = 15
    
    # Tracing
    tracing_enabled: bool = True
    tracing_sample_rate: float = 0.1
    tracing_exporter: str = "otlp"  # otlp, jaeger, zipkin
    tracing_endpoint: str = "http://localhost:4317"
    
    # Logging
    log_level: str = "INFO"
    log_format: str = "json"
    log_output: str = "stdout"  # stdout, file, syslog
    log_file_path: str = "logs/app.log"
    log_max_size_mb: int = 100
    log_backup_count: int = 10
    
    # Alerting
    alerting_enabled: bool = True
    alert_rules_path: str = "config/alerts/rules.yaml"
    notification_channels: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PluginConfig:
    """Plugin system configuration."""
    plugins_dir: str = "src/plugins"
    marketplace_enabled: bool = True
    auto_start_plugins: bool = True
    sandbox_enabled: bool = True
    sandbox_memory_limit_mb: int = 512
    sandbox_cpu_limit: float = 1.0
    sandbox_timeout_seconds: int = 30
    plugin_signature_verification: bool = True


@dataclass
class PerformanceConfig:
    """Performance configuration."""
    # Caching
    cache_enabled: bool = True
    cache_backend: str = "redis"  # redis, memory
    cache_default_ttl: int = 300
    cache_max_size_mb: int = 256
    
    # Connection pooling
    db_pool_size: int = 20
    http_pool_connections: int = 100
    http_pool_maxsize: int = 200
    
    # Rate limiting
    rate_limit_enabled: bool = True
    default_rate_limit_rpm: int = 1000
    default_rate_limit_tpm: int = 50000
    
    # Timeouts
    request_timeout_seconds: int = 30
    upstream_timeout_seconds: int = 60


@dataclass
class DeploymentConfig:
    """Deployment configuration."""
    # Docker
    docker_image: str = "liuhao-ai-os"
    docker_tag: str = "latest"
    docker_registry: Optional[str] = None
    
    # Kubernetes
    k8s_namespace: str = "liuhao-ai-os"
    k8s_replicas: int = 3
    k8s_cpu_request: str = "500m"
    k8s_memory_request: str = "1Gi"
    k8s_cpu_limit: str = "2000m"
    k8s_memory_limit: str = "4Gi"
    
    # Health checks
    health_check_path: str = "/health"
    readiness_check_path: str = "/ready"
    liveness_probe_initial_delay: int = 10
    liveness_probe_period: int = 30
    readiness_probe_initial_delay: int = 5
    readiness_probe_period: int = 10
    
    # Rolling update
    rolling_update_max_surge: str = "25%"
    rolling_update_max_unavailable: str = "25%"


@dataclass
class BackupConfig:
    """Backup configuration."""
    enabled: bool = True
    schedule: str = "0 2 * * *"  # Daily at 2 AM
    retention_days: int = 30
    storage_path: str = "backups/"
    compression: bool = True
    encryption: bool = True
    encryption_key: Optional[str] = None
    verify_after_backup: bool = True


@dataclass
class MonitoringConfig:
    """Monitoring configuration."""
    # Prometheus
    prometheus_enabled: bool = True
    prometheus_port: int = 9090
    prometheus_scrape_interval: int = 15
    
    # Grafana
    grafana_enabled: bool = True
    grafana_port: int = 3000
    grafana_dashboards_path: str = "config/grafana/dashboards"
    
    # Alerting
    alertmanager_enabled: bool = True
    alertmanager_port: int = 9093
    
    # Log aggregation
    loki_enabled: bool = True
    loki_port: int = 3100


@dataclass
class ProductionConfig:
    """Complete production configuration."""
    environment: Environment = Environment.PRODUCTION
    app_name: str = "LiuHao AI OS"
    app_version: str = "1.0.0"
    debug: bool = False
    
    # Sub-configurations
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    redis: RedisConfig = field(default_factory=RedisConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    observability: ObservabilityConfig = field(default_factory=ObservabilityConfig)
    plugins: PluginConfig = field(default_factory=PluginConfig)
    performance: PerformanceConfig = field(default_factory=PerformanceConfig)
    deployment: DeploymentConfig = field(default_factory=DeploymentConfig)
    backup: BackupConfig = field(default_factory=BackupConfig)
    monitoring: MonitoringConfig = field(default_factory=MonitoringConfig)
    
    # Override with environment variables
    def __post_init__(self):
        self._load_from_env()
    
    def _load_from_env(self):
        """Load configuration from environment variables."""
        # Database
        if os.getenv("DATABASE_URL"):
            self.database.url = os.getenv("DATABASE_URL")
        if os.getenv("DATABASE_POOL_SIZE"):
            self.database.pool_size = int(os.getenv("DATABASE_POOL_SIZE"))
        
        # Redis
        if os.getenv("REDIS_HOST"):
            self.redis.host = os.getenv("REDIS_HOST")
        if os.getenv("REDIS_PORT"):
            self.redis.port = int(os.getenv("REDIS_PORT"))
        if os.getenv("REDIS_PASSWORD"):
            self.redis.password = os.getenv("REDIS_PASSWORD")
        
        # Security
        if os.getenv("JWT_SECRET_KEY"):
            self.security.jwt_secret_key = os.getenv("JWT_SECRET_KEY")
        if os.getenv("ENCRYPTION_MASTER_KEY"):
            self.security.encryption_master_key = os.getenv("ENCRYPTION_MASTER_KEY")
        if os.getenv("API_KEY_MASTER_KEY"):
            self.security.api_key_master_key = os.getenv("API_KEY_MASTER_KEY")
        
        # Observability
        if os.getenv("LOG_LEVEL"):
            self.observability.log_level = os.getenv("LOG_LEVEL")
        if os.getenv("METRICS_ENABLED"):
            self.observability.metrics_enabled = os.getenv("METRICS_ENABLED").lower() == "true"
        
        # Deployment
        if os.getenv("DOCKER_IMAGE"):
            self.deployment.docker_image = os.getenv("DOCKER_IMAGE")
        if os.getenv("DOCKER_TAG"):
            self.deployment.docker_tag = os.getenv("DOCKER_TAG")
        if os.getenv("K8S_NAMESPACE"):
            self.deployment.k8s_namespace = os.getenv("K8S_NAMESPACE")
        if os.getenv("K8S_REPLICAS"):
            self.deployment.k8s_replicas = int(os.getenv("K8S_REPLICAS"))
        
        # Performance
        if os.getenv("CACHE_BACKEND"):
            self.performance.cache_backend = os.getenv("CACHE_BACKEND")
        if os.getenv("RATE_LIMIT_ENABLED"):
            self.performance.rate_limit_enabled = os.getenv("RATE_LIMIT_ENABLED").lower() == "true"
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)
    
    def validate(self) -> List[str]:
        """Validate configuration and return list of issues."""
        issues = []
        
        # Required security settings
        if self.environment == Environment.PRODUCTION:
            if not self.security.jwt_secret_key:
                issues.append("JWT_SECRET_KEY must be set in production")
            if not self.security.encryption_master_key:
                issues.append("ENCRYPTION_MASTER_KEY must be set in production")
            if not self.security.api_key_master_key:
                issues.append("API_KEY_MASTER_KEY must be set in production")
            if self.debug:
                issues.append("DEBUG must be False in production")
        
        # Database
        if not self.database.url:
            issues.append("DATABASE_URL must be set")
        
        # TLS certificates
        if self.security.tls_enabled:
            if not os.path.exists(self.security.tls_cert_path):
                issues.append(f"TLS certificate not found: {self.security.tls_cert_path}")
            if not os.path.exists(self.security.tls_key_path):
                issues.append(f"TLS key not found: {self.security.tls_key_path}")
        
        return issues
    
    def get_required_env_vars(self) -> List[str]:
        """Get list of required environment variables for production."""
        return [
            "DATABASE_URL",
            "JWT_SECRET_KEY",
            "ENCRYPTION_MASTER_KEY",
            "API_KEY_MASTER_KEY",
            "REDIS_HOST",
            "REDIS_PORT",
            "REDIS_PASSWORD",
        ]


# ==================== Environment-Specific Configs ====================


def get_development_config() -> ProductionConfig:
    """Get development configuration."""
    config = ProductionConfig(environment=Environment.DEVELOPMENT)
    config.debug = True
    config.database.url = "sqlite:///./dev.db"
    config.security.tls_enabled = False
    config.security.jwt_secret_key = "dev-secret-key-change-in-production"
    config.security.encryption_master_key = "dev-encryption-key-32-bytes-long!!"
    config.security.api_key_master_key = "dev-api-key-master-32-bytes-long!"
    config.observability.log_level = "DEBUG"
    config.observability.tracing_sample_rate = 1.0
    config.performance.rate_limit_enabled = False
    return config


def get_staging_config() -> ProductionConfig:
    """Get staging configuration."""
    config = ProductionConfig(environment=Environment.STAGING)
    config.debug = False
    config.database.url = os.getenv("DATABASE_URL", "postgresql://user:pass@localhost/staging")
    config.security.tls_enabled = True
    config.observability.log_level = "INFO"
    config.observability.tracing_sample_rate = 0.5
    config.performance.rate_limit_enabled = True
    config.deployment.k8s_replicas = 2
    return config


def get_production_config() -> ProductionConfig:
    """Get production configuration."""
    config = ProductionConfig(environment=Environment.PRODUCTION)
    config.debug = False
    # All sensitive values must come from environment variables
    return config


def get_config(environment: Optional[str] = None) -> ProductionConfig:
    """Get configuration for specified environment."""
    env = environment or os.getenv("ENVIRONMENT", "development")
    
    if env == "production":
        return get_production_config()
    elif env == "staging":
        return get_staging_config()
    else:
        return get_development_config()


# ==================== Export ====================

__all__ = [
    "Environment",
    "DatabaseConfig",
    "RedisConfig",
    "SecurityConfig",
    "ObservabilityConfig",
    "PluginConfig",
    "PerformanceConfig",
    "DeploymentConfig",
    "BackupConfig",
    "MonitoringConfig",
    "ProductionConfig",
    "get_config",
    "get_development_config",
    "get_staging_config",
    "get_production_config",
]