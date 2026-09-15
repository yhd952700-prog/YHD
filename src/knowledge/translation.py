"""Supported languages for the S2 multi-platform / translation features.

``LANGUAGE_LIST`` is the ordered set of language codes the platform accepts;
``SUPPORTED_LANGUAGES`` maps each code to a human-readable name. Both are
re-exported from :mod:`src.knowledge` (see ``__all__`` in ``memory.py``).
"""

from __future__ import annotations

# Ordered, canonical list of supported language codes (ISO 639-1).
LANGUAGE_LIST: list[str] = [
    "en", "zh", "es", "fr", "de", "ja", "ko", "ru",
    "pt", "ar", "hi", "it",
]

# code -> display name
SUPPORTED_LANGUAGES: dict[str, str] = {
    "en": "English",
    "zh": "Chinese",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "ja": "Japanese",
    "ko": "Korean",
    "ru": "Russian",
    "pt": "Portuguese",
    "ar": "Arabic",
    "hi": "Hindi",
    "it": "Italian",
}

__all__ = ["LANGUAGE_LIST", "SUPPORTED_LANGUAGES"]
