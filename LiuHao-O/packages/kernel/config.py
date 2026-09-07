"""
LIUHAO X - Configuration Package

Settings using Pydantic Settings with environment variable support.
All configuration loaded from .env file and environment.
"""

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Root settings for LIUHAO X v3.0."""
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="LHX_",
        env_nested_delimiter="__",
        extra="ignore",
    )
    
    # Application
    app_name: str = Field(default="liuhao-x", alias="LHX_APP_NAME")
    app_version: str = Field(default="3.0.0", alias="LHX_APP_VERSION")
    debug: bool = Field(default=False, alias="LHX_DEBUG")
    
    # Database
    postgres_dsn: str = Field(alias="LHX_POSTGRES_DSN")
    redis_dsn: str = Field(alias="LHX_REDIS_DSN")
    vector_dsn: str = Field(alias="LHX_VECTOR_DSN", default="")
    
    # Security
    secret_key: str = Field(alias="LHX_SECRET_KEY")
    algorithm: str = Field(default="HS256", alias="LHX_ALGORITHM")
    access_token_expire_minutes: int = Field(default=30, alias="LHX_ACCESS_TOKEN_EXPIRE_MINUTES")
    
    # Model Gateway
    model_gateway_url: str = Field(alias="LHX_MODEL_GATEWAY_URL", default="http://localhost:8001")
    
    # Observability
    otel_endpoint: str = Field(alias="LHX_OTEL_ENDPOINT", default="http://localhost:4317")
    prometheus_port: int = Field(default=9090, alias="LHX_PROMETHEUS_PORT")
    grafana_url: str = Field(alias="LHX_GRAFANA_URL", default="http://localhost:3000")
    
    # Vault
    vault_address: str = Field(alias="LHX_VAULT_ADDRESS", default="http://localhost:8200")
    vault_token: str = Field(alias="LHX_VAULT_TOKEN", default="")
    
    # Feature flags
    enable_agent_spawning: bool = Field(default=True, alias="LHX_ENABLE_AGENT_SPAWNING")
    enable_multi_agent: bool = Field(default=True, alias="LHX_ENABLE_MULTI_AGENT")
    enable_long_horizon: bool = Field(default=True, alias="LHX_ENABLE_LONG_HORIZON")
    enable_perception: bool = Field(default=True, alias="LHX_ENABLE_PERCEPTION")
    
    # L10K
    vhl_baseline_threshold: float = Field(default=10000.0, alias="LHX_VHL_BASELINE_THRESHOLD")
    
    # Organization
    default_organization_name: str = Field(default="default", alias="LHX_DEFAULT_ORGANIZATION")
    
    # Paths
    data_dir: str = Field(default="/data", alias="LHX_DATA_DIR")
    logs_dir: str = Field(default="/logs", alias="LHX_LOGS_DIR")
    workspace_dir: str = Field(default=".", alias="LHX_WORKSPACE_DIR")
    
    @field_validator("postgres_dsn", "redis_dsn", "secret_key", mode="before")
    @classmethod
    def require_not_empty(cls, v, info):
        if not v:
            raise ValueError(f"{info.field_name} must be set")
        return v


settings = Settings()