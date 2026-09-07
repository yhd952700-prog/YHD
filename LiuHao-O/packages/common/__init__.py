"""common — 十源 packages 层 facade.

共享错误类型（复用根目录 error_types.py）+ 项目级常量（十源 / 版本）。

复用而非重写：错误类型来自已存在的 ``error_types.py``，十源常量是项目宪法。
"""

from error_types import (
    LiuHaoError,
    ProviderError,
    ProviderAPIError,
    ProviderRateLimitError,
    ConfigurationError,
    MemoryError,
    classify_error,
)

# 十源 DNA（项目宪法）：统一形成 LIUHAO X 的十个源。
TEN_SOURCES = [
    "ULTRON",
    "VISION",
    "ADA",
    "EDITH",
    "FRIDAY",
    "JARVIS",
    "JOCaSTA",
    "KAREN",
    "ENOCH",
    "ZOON",
]

__version__ = "3.0.0"

__all__ = [
    "LiuHaoError",
    "ProviderError",
    "ProviderAPIError",
    "ProviderRateLimitError",
    "ConfigurationError",
    "MemoryError",
    "classify_error",
    "TEN_SOURCES",
    "__version__",
]
