"""HD-06 — retention policy-neutral configuration.

The retention subsystem is *policy-neutral*: it ships with a hard SAFE DEFAULT of
**never delete, overwrite, or destroy original immutable history**, and every
final retention rule is configurable through a single reserved switch
(``LIUHAO_RETENTION_POLICY``) so the owner can set the real policy later
**without code changes**.

The legal retention period itself is a *sovereign* decision and is deliberately
NOT decided here -- the switch is a placeholder that the owner fills in. This
module only parses that switch and exposes a structured :class:`RetentionConfig`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional

#: Environment variable carrying the retention policy selection. Mirrors the
#: enforcement control-point pattern (``LIUHAO_KERNEL_POLICY_ENFORCE``): a single,
#: reversible, deployment-side switch.
RETENTION_POLICY_ENV = "LIUHAO_RETENTION_POLICY"


class RetentionMode(str, Enum):
    """Final retention modes.

    The default and only guaranteed-safe mode is :attr:`NO_DELETE`. The other
    modes are opt-in and still must honour the hard invariant that
    **original immutable history is never deleted**.
    """

    NO_DELETE = "NO_DELETE"  #: keep everything forever (default; never deletes)
    ARCHIVE_ONLY = "ARCHIVE_ONLY"  #: copy originals to cold storage, retain originals
    #: Reserved. Only ever deletes *derivative* copies after a horizon; originals
    #: are still protected. Requires an explicit ``DELETE_AFTER_DAYS:<n>`` spec.
    DELETE_AFTER_DAYS = "DELETE_AFTER_DAYS"


# Human-readable help surfaced when the spec is malformed.
_MODE_NAMES = {m.value for m in RetentionMode}


@dataclass(frozen=True)
class RetentionConfig:
    """Structured retention configuration (policy-neutral, switch-driven)."""

    mode: RetentionMode = RetentionMode.NO_DELETE
    #: Only meaningful in DELETE_AFTER_DAYS mode, and only ever applied to
    #: DERIVATIVE copies. ``None`` means "no horizon configured".
    delete_after_days: Optional[int] = None
    #: Root directory for the cold-storage / archival adapter.
    cold_storage_root: str = ".retention_cold"
    #: Hard invariant. When True (the default) the manager refuses to delete,
    #: overwrite, or destroy any record classified ``IMMUTABLE_ORIGINAL``.
    protect_immutable_originals: bool = True
    #: Optional human-readable note recorded for auditability of the chosen policy.
    note: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode.value,
            "delete_after_days": self.delete_after_days,
            "cold_storage_root": self.cold_storage_root,
            "protect_immutable_originals": self.protect_immutable_originals,
            "note": self.note,
        }


def _parse_days(value: str) -> int:
    try:
        days = int(value.strip())
    except ValueError as exc:
        raise ValueError(
            f"{RETENTION_POLICY_ENV}: DELETE_AFTER_DAYS requires an integer "
            f"horizon, e.g. 'DELETE_AFTER_DAYS:365' (got {value!r})"
        ) from exc
    if days <= 0:
        raise ValueError(
            f"{RETENTION_POLICY_ENV}: DELETE_AFTER_DAYS horizon must be positive "
            f"(got {days})"
        )
    return days


def parse_policy_spec(spec: Optional[str]) -> RetentionConfig:
    """Parse a retention policy spec string into a :class:`RetentionConfig`.

    Spec forms:
      * ``""`` (unset)            -> NO_DELETE (the safe default)
      * ``"NO_DELETE"``           -> NO_DELETE
      * ``"ARCHIVE_ONLY"``        -> ARCHIVE_ONLY (copies to cold storage)
      * ``"DELETE_AFTER_DAYS:365"`` -> DELETE_AFTER_DAYS, horizon 365 (derivatives only)

    Raises:
        ValueError: on an unknown mode or a malformed DELETE_AFTER_DAYS spec.
    """
    text = (spec or "").strip()
    if not text:
        return RetentionConfig(mode=RetentionMode.NO_DELETE)
    if ":" in text:
        head, _, tail = text.partition(":")
        head = head.strip().upper()
        if head != RetentionMode.DELETE_AFTER_DAYS.value:
            raise ValueError(
                f"{RETENTION_POLICY_ENV}: unknown mode {head!r} "
                f"(known: {sorted(_MODE_NAMES)})"
            )
        days = _parse_days(tail)
        return RetentionConfig(
            mode=RetentionMode.DELETE_AFTER_DAYS, delete_after_days=days,
            note="DELETE_AFTER_DAYS applies to DERIVATIVE copies only; "
                 "IMMUTABLE_ORIGINAL is never deleted.",
        )
    upper = text.upper()
    if upper not in _MODE_NAMES:
        raise ValueError(
            f"{RETENTION_POLICY_ENV}: unknown mode {text!r} "
            f"(known: {sorted(_MODE_NAMES)})"
        )
    return RetentionConfig(mode=RetentionMode(upper))


def load_retention_config() -> RetentionConfig:
    """Read the retention config from the environment (defaults to NO_DELETE)."""
    return parse_policy_spec(os.environ.get(RETENTION_POLICY_ENV))
