"""pydantic-settings configuration for liuhao AI OS"""
from pydantic_settings import BaseSettings, SettingsConfigDict

class LiuHaoSettings(BaseSettings):
    """Configuration class using pydantic-settings."""
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
    
    # Database
    postgres_dsn: str = "postgresql://liuhao:liuhao_secure_pass_2024@localhost:5432/liuhao_ai_os"
    redis_url: str = "redis://:liuhao_redis_pass_2024@localhost:6379/0"
    qdrant_url: str = "http://localhost:6333"
    
    # Security
    secret_key: str = "change-this-in-production"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    
    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False
    
    # Monitoring
    otel_endpoint: str = "http://localhost:4317"
    prometheus_port: int = 8000

settings = LiuHaoSettings()
