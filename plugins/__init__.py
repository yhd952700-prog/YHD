"""
LiuHao AI OS Plugin System

This package provides a collection of utility plugins for the LiuHao AI OS,
including translation, summarization, sentiment analysis, code formatting,
PDF processing, Excel handling, image processing, audio/video tools,
web scraping, RSS feeds, calendar management, todo lists, notes,
password management, QR codes, weather data, currency conversion,
unit conversion, and regex utilities.

Base Classes:
    BasePlugin: Abstract base class for all plugins.
    PluginMetadata: Metadata dataclass for plugin descriptions.
    PluginStatus: Plugin lifecycle status constants.

Registry:
    PluginRegistry: Central registry for plugin management.

Usage:
    from plugins import PluginRegistry, get_plugin_registry

    registry = get_plugin_registry()
    registry.discover_plugins()
    registry.activate_all()

    translate = registry.get_plugin("translate")
    result = translate.translate_text("Hello", "zh-CN")
"""

from .plugin_base import (
    BasePlugin,
    PluginMetadata,
    PluginStatus,
    PluginError,
    PluginInitError,
    PluginActivationError,
    PluginExecutionError,
)

from .registry import (
    PluginRegistry,
    get_plugin_registry,
)

__all__ = [
    # Base classes
    "BasePlugin",
    "PluginMetadata",
    "PluginStatus",
    "PluginError",
    "PluginInitError",
    "PluginActivationError",
    "PluginExecutionError",
    # Registry
    "PluginRegistry",
    "get_plugin_registry",
]

__version__ = "1.0.0"
