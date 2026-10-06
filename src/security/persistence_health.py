"""D19-A+ persistence-health accounting for the VOLATILE audit loggers.

Why this module exists
----------------------
The D19 verdict (Option **A+**) was "volatile + bounded ring buffer +
persistent high-water-mark". Its stated purpose was to upgrade HC-09 / HC-10
from *"evidence lost and unattributable"* to *"evidence lost but ACCOUNTABLE"*.
The two loggers shipped the raw material for that promise -- a bounded buffer, a
``dropped_count`` (evictions) and a durable ``entries_ever_written`` (high-water
mark, HWM) -- but nothing ever *reconciled* the three numbers. So the system
could count how many events were lost and how many ever existed, yet never
answer the only question that matters after the fact:

    were those events lost by an EXPECTED eviction, or did they DISAPPEAR?

This module closes that gap with a pure, four-state verdict.

The accounting identity
-----------------------
For a ring buffer of capacity ``C``::

    high_water_mark  = number of entries ever written (survives restart)
    dropped_count    = number of entries evicted by the ring (in-memory)
    buffered         = number of entries currently held

    unaccounted = high_water_mark - dropped_count - buffered

``unaccounted`` is the number of entries that were written, are not in the
buffer, and were never counted as an eviction. A healthy chain keeps it at 0.

The epoch baseline (why a naive identity false-alarms on every restart)
-----------------------------------------------------------------------
The three counters above do NOT share a lifetime:

* ``high_water_mark`` is **durable** -- it is reloaded from the HWM file.
* ``dropped_count`` and ``buffered`` are **per-process** -- they reset to 0 on
  restart, because the ring buffer is volatile.

So the naive identity mixes a lifetime-spanning counter with two
process-scoped ones. Proven consequence (see the restart regression test): a
process that wrote 5 entries and is restarted, then simply rewrites the same 5,
shows ``hwm=10, dropped=0, buffered=5`` and therefore reports
``unaccounted=5`` -- a perfectly healthy chain labelled TRUNCATED with
``evidence_destroyed=True``. That is not a rare edge: it happens on **every
restart**, which is exactly the kind of false alarm that trains operators to
ignore the check, and it drowns a real TRUNCATED in noise.

The fix is to reconcile only within the epoch that actually observed the
losses::

    epoch_written = high_water_mark - high_water_mark_at_epoch_start
    unaccounted   = epoch_written - dropped_count - buffered

``high_water_mark_at_epoch_start`` is the HWM value read at construction time,
before this process wrote anything. Entries written by *earlier* processes are
therefore excluded: their evictions are unobservable by construction, and
pretending otherwise manufactures phantom loss. Loss that predates this epoch is
still reported -- as :attr:`~PersistenceHealth.DESTROYED` when the buffer is
empty, which is the truthful statement for a volatile chain -- but it is no
longer mislabelled as *unexplained*.

Detection is preserved: an entry removed from the buffer **without** the
eviction counter advancing still yields ``unaccounted > 0`` inside the epoch,
so a genuine TRUNCATED is still caught.

Honest reading of the states (do NOT over-read them)
----------------------------------------------------
Both chains are VOLATILE by design (see the module docstrings of
:mod:`src.security.audit_logger` and :mod:`src.security.audit_policy`). Losing
the buffer on restart is therefore EXPECTED volatility -- but it is still *loss*,
and A+ exists to make that loss measurable rather than silent. So:

* :attr:`PersistenceHealth.DESTROYED` means "the counter says entries existed and
  the buffer is now empty". After a normal restart of a volatile chain this is the
  truthful answer, not proof of an attack. Its value is that the loss is now
  *quantified and reported* instead of invisible.
* :attr:`PersistenceHealth.TRUNCATED` means entries vanished without being
  counted as evictions -- the one state that is NOT explained by documented
  behaviour and therefore the one that most warrants investigation.

Neither state is evidence of tampering. A local HWM file can be edited by anyone
with file permission; A+ raises the *cost* of silent loss, it does not provide
tamper-proofing. See the D19 decision packet for that boundary.

This module is telemetry only. It must never influence a trust, policy or
authorization decision, and every entry point here is fail-open by design.
"""

from __future__ import annotations

from enum import Enum
from typing import FrozenSet


class PersistenceHealth(str, Enum):
    """D19-A+ accountability verdict for a volatile ring-buffer chain.

    The values are strings so the verdict serialises straight into JSON
    (``/v1/ready``-style payloads, ``stats()`` output) without a converter.
    """

    #: Nothing was ever written and nothing is buffered. Healthy.
    IDLE = "idle"
    #: Everything written is either buffered or accounted as an eviction.
    #: This is the healthy steady state (including when no eviction occurred).
    EXPECTED_EVICTION = "expected_eviction"
    #: The buffer is empty but the HWM says entries existed -> they are gone.
    DESTROYED = "destroyed"
    #: Entries are missing and were never counted as evictions -> unexplained.
    TRUNCATED = "truncated"


#: States that mean "evidence is gone and the loss is NOT explained by a
#: documented eviction". Surfaced for alerting; never used to gate a decision.
ALERTING_STATES: FrozenSet[PersistenceHealth] = frozenset(
    {PersistenceHealth.DESTROYED, PersistenceHealth.TRUNCATED}
)


def resolve_persistence_health(
    high_water_mark: int,
    dropped_count: int,
    buffered: int,
    *,
    high_water_mark_at_epoch_start: int = 0,
) -> PersistenceHealth:
    """Reconcile the three counters into one accountability verdict.

    :param high_water_mark: entries ever written (durable across restarts).
    :param dropped_count: entries evicted by the ring buffer (in-memory).
    :param buffered: entries currently held in the buffer.
    :param high_water_mark_at_epoch_start: the HWM value as read at construction,
        before this process wrote anything. Losses from earlier processes are
        then excluded from the unexplained-loss arithmetic -- see the module
        docstring. Defaults to 0, which preserves the naive identity for callers
        that have no epoch baseline.
    :returns: the :class:`PersistenceHealth` verdict.

    The check order matters: an empty buffer with a non-zero HWM is
    :attr:`~PersistenceHealth.DESTROYED` regardless of the arithmetic, because
    "everything is gone" is the more specific (and more actionable) statement
    than "some number is unaccounted for".

    Pure and total: never raises, never reads the environment, never blocks.
    Negative inputs are clamped so a corrupt/rolled-back counter cannot crash
    a telemetry call.
    """
    hwm = max(int(high_water_mark), 0)
    dropped = max(int(dropped_count), 0)
    held = max(int(buffered), 0)
    # A baseline above the current HWM means the counter was rolled back or the
    # HWM file was replaced. Clamp to 0 so epoch_written never goes negative and
    # a downgraded counter cannot fabricate (or erase) a TRUNCATED verdict.
    baseline = min(max(int(high_water_mark_at_epoch_start), 0), hwm)

    if held == 0:
        return PersistenceHealth.DESTROYED if hwm > 0 else PersistenceHealth.IDLE

    epoch_written = hwm - baseline
    if epoch_written - dropped - held > 0:
        return PersistenceHealth.TRUNCATED
    return PersistenceHealth.EXPECTED_EVICTION


def unaccounted_loss(
    high_water_mark: int,
    dropped_count: int,
    buffered: int,
    *,
    high_water_mark_at_epoch_start: int = 0,
) -> int:
    """How many entries written *in this epoch* are neither buffered nor evicted.

    Zero means the ledger balances. Reported alongside the verdict so operators
    can see the magnitude, not just the category. Never negative.
    """
    hwm = max(int(high_water_mark), 0)
    baseline = min(max(int(high_water_mark_at_epoch_start), 0), hwm)
    return max((hwm - baseline) - int(dropped_count) - int(buffered), 0)
