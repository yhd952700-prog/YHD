"""
RBAC Store singleton for LiuHao AI OS.

Provides a shared RBACManager instance so Role objects can look up
parent roles during inheritance computation and cycle detection.

Uses lazy initialization to avoid circular imports: rbac.py imports
this module inside methods, never at module top-level.
"""

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .rbac import RBACManager

_rbac_store: Optional["RBACManager"] = None


def get_rbac_manager() -> "RBACManager":
    """Get the singleton RBACManager instance (lazy-initialized)."""
    global _rbac_store
    if _rbac_store is None:
        from .rbac import RBACManager
        _rbac_store = RBACManager()
    return _rbac_store


class _RBACStoreProxy:
    """Thin proxy that delegates to the singleton, enabling
    `rbac_store.get_role(...)` syntax without circular imports."""

    def __getattr__(self, name):
        return getattr(get_rbac_manager(), name)


# Module-level singleton proxy — rbac_store.get_role() works
# without triggering RBACManager import at module load time
rbac_store = _RBACStoreProxy()
