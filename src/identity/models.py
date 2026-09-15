"""Lightweight user identity model used by the memory/identity layers.

This is a minimal, dependency-free ``User`` used by the Memory System refactor
to carry subject identity into RBAC/audit calls (``user.id``). It is intentionally
small and will be converged with the full identity manager
(``src/identity/__init__.py``) per the access-control design.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class User:
    """A knowledge/memory subject."""

    id: str
    is_admin: bool = False
    name: Optional[str] = None
    roles: Dict[str, Any] = field(default_factory=dict)
