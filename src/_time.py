"""UTC time helper — the single, deprecation-free source of "now".

Why **naive** (``tzinfo=None``) and not timezone-aware
-----------------------------------------------------
``datetime.utcnow()`` is deprecated from Python 3.12 onwards, but it returns a
*naive* datetime, and a naive UTC value is what this codebase stores,
serialises and compares:

* SQLAlchemy ``Column(DateTime, default=...)`` columns are naive. Several
  backends reject an aware value outright and others silently mangle it.
* ``.isoformat()`` on an aware datetime appends ``+00:00``, which would change
  the on-the-wire / on-disk format of audit records, events and API payloads.
* Persisted rows and existing fixtures are naive; comparing aware with naive
  raises ``TypeError``.
* ``.timestamp()`` on a naive datetime interprets it as *local* time, so
  silently switching to aware would also silently change epoch conversions.

Therefore ``utc_now()`` is a **drop-in, value-identical** replacement for
``datetime.utcnow()``: same instant, same naivety, no ``DeprecationWarning``.

Migrating the codebase to timezone-aware datetimes is a deliberate, separate
piece of work (schema + wire format + persisted rows + fixtures). When that is
decided, it is changed **here and nowhere else** — which is the entire point of
funnelling every "now" through this module.
"""

from __future__ import annotations

from datetime import datetime, timezone


def utc_now() -> datetime:
    """Return the current UTC time as a **naive** datetime.

    Exactly equivalent to the deprecated ``datetime.utcnow()`` (same value,
    same ``tzinfo is None``), so it is safe as a drop-in replacement.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


__all__ = ["utc_now"]
