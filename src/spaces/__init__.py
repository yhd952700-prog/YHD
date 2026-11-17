"""协作空间存储（SQLite ``spaces.db``，随现有数据目录走）。"""

from __future__ import annotations

from .store import SpaceStore, get_space_store

__all__ = ["SpaceStore", "get_space_store"]
