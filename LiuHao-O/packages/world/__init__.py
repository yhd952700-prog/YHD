"""world — 十源 packages 层 facade.

世界接口（observe/validate/authorize/execute/verify）

复用 src/ 的真实实现（不重写，NO-FAKE）。
"""

from src.ai.world_interface import (
    WorldInterface,
    WorldRequest,
    WorldAdapter,
    FilesystemAdapter,
    ShellAdapter,
)

__all__ = [
    "WorldInterface",
    "WorldRequest",
    "WorldAdapter",
    "FilesystemAdapter",
    "ShellAdapter",
]
