"""Normalizes provider-key aliases and returns the correct provider instance while preserving the old risk-assessment contract."""

from __future__ import annotations

from .mock import MockRiskAssessmentProvider
from .openai import OpenAIProvider
from .self_host import SelfHostProvider

# Alias mapping: all keys resolve to the same canonical provider class
_PROVIDER_ALIASES: dict[str, type] = {
    "mock": MockRiskAssessmentProvider,
    "openai": OpenAIProvider,
    "self_host": SelfHostProvider,
    "self-host": SelfHostProvider,
    "selfhost": SelfHostProvider,
}

# Canonical provider instances (singleton-ish for registry purposes)
_PROVIDER_INSTANCES: dict[str, object] = {}


def get_provider(key: str = "mock") -> object:
    """Get a provider instance by key.

    Args:
        key: Provider alias. Normalized from the full set of supported keys.

    Returns:
        An provider instance. Cached after first lookup.
    """
    # Normalize the key
    normalized = key.lower().strip()
    # Handle hyphenated/underscore variants
    aliases = {
        "mock": "mock",
        "openai": "openai",
        "self_host": "self_host",
        "self-host": "self_host",
        "selfhost": "self_host",
    }
    canonical = aliases.get(normalized, "mock")

    if canonical not in _PROVIDER_INSTANCES:
        _PROVIDER_INSTANCES[canonical] = {
            "mock": MockRiskAssessmentProvider(),
            "openai": OpenAIProvider(),
            "self_host": SelfHostProvider(),
        }[canonical]

    return _PROVIDER_INSTANCES[canonical]


def registry() -> dict[str, type]:
    """Return the alias-to-class mapping."""
    return dict(_PROVIDER_ALIASES)


__all__ = ["get_provider", "registry"]