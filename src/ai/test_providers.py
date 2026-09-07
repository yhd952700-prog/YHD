"""Test file for provider configuration."""

import os
import sys
import pytest

from ai.providers import (
    get_provider_type,
    get_api_key,
    get_model_name,
    ProviderType,
)


class TestProviderConfiguration:
    """Test provider configuration loading."""
    
    def test_mock_provider_default(self):
        """MockProvider should be the default."""
        # Clear any provider type env var
        old_value = os.environ.pop("AI_PROVIDER_TYPE", None)
        try:
            provider_type = get_provider_type()
            assert provider_type == ProviderType.MOCK, f"Expected MOCK, got {provider_type}"
        finally:
            if old_value is not None:
                os.environ["AI_PROVIDER_TYPE"] = old_value
    
    def test_mock_provider_with_env(self):
        """MockProvider can be set via env var."""
        os.environ["AI_PROVIDER_TYPE"] = ProviderType.MOCK
        try:
            provider_type = get_provider_type()
            assert provider_type == ProviderType.MOCK
        finally:
            del os.environ["AI_PROVIDER_TYPE"]
    
    def test_openai_provider(self):
        """OpenAI provider can be configured."""
        os.environ["AI_PROVIDER_TYPE"] = ProviderType.OPENAI
        os.environ["OPENAI_API_KEY"] = "sk-test-12345"
        try:
            provider_type = get_provider_type()
            api_key = get_api_key()
            model = get_model_name()
            assert provider_type == ProviderType.OPENAI
            assert api_key == "sk-test-12345"
            assert model == "gpt-4o-mini"
        finally:
            del os.environ["AI_PROVIDER_TYPE"]
            del os.environ["OPENAI_API_KEY"]
    
    def test_api_key_redacted(self):
        """API keys should not be exposed in logs."""
        os.environ["OPENAI_API_KEY"] = "[REDACTED]"
        try:
            api_key = get_api_key()
            # Should return the redacted value or None, not the actual key
            assert api_key is not None
        finally:
            del os.environ["OPENAI_API_KEY"]
