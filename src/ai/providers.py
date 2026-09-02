"""Provider configuration for LiuHao AI OS.

This module provides configuration and instantiation of AI providers.
Supports both MockProvider for development and real providers
(OpenAI, Anthropic, etc.) for production use.

Usage:
    from ai.providers import get_provider
    provider = get_provider()  # Auto-detects from env config
"""

import os
from typing import Optional

# Provider type enumeration
class ProviderType:
    MOCK = "mock"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GOOGLE = "google"
    SELF_HOSTED = "self_hosted"

def get_provider_type() -> str:
    """Determine provider type from environment configuration."""
    return os.environ.get("AI_PROVIDER_TYPE", ProviderType.MOCK)

def get_api_key() -> Optional[str]:
    """Get the API key for the configured provider."""
    provider_type = get_provider_type()
    
    if provider_type == ProviderType.MOCK:
        return os.environ.get("MOCK_API_KEY", "[REDACTED]")
    elif provider_type == ProviderType.OPENAI:
        return os.environ.get("OPENAI_API_KEY", "[REDACTED]")
    elif provider_type == ProviderType.ANTHROPIC:
        return os.environ.get("ANTHROPIC_API_KEY", "[REDACTED]")
    elif provider_type == ProviderType.GOOGLE:
        return os.environ.get("GOOGLE_API_KEY", "[REDACTED]")
    elif provider_type == ProviderType.SELF_HOSTED:
        return os.environ.get("SELF_HOSTED_API_KEY", "[REDACTED]")
    else:
        return None

def get_base_url() -> Optional[str]:
    """Get the base URL for the provider API."""
    provider_type = get_provider_type()
    
    if provider_type == ProviderType.SELF_HOSTED:
        return os.environ.get("SELF_HOSTED_BASE_URL", "http://localhost:8000")
    return None

def get_model_name() -> str:
    """Get the default model name for the configured provider."""
    provider_type = get_provider_type()
    
    models = {
        ProviderType.MOCK: "mock-model",
        ProviderType.OPENAI: "gpt-4o-mini",
        ProviderType.ANTHROPIC: "claude-3-haiku-20240307",
        ProviderType.GOOGLE: "gemini-pro",
        ProviderType.SELF_HOSTED: "custom-model",
    }
    
    return models.get(provider_type, "mock-model")
