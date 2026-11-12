# ADR — Audit store writer lease: single-writer per database (U39)

**Status:** **Option B IMPLEMENTED (2026-09-26)** — verified; Option C remains the
long-term scale path.
**Date:** 2026-09-26 (updated same day: B implemented + verified)
**Author:** expert-org team lead (autonomous)
**Relates to:** U38 (self-fence, fixed), U39 (cross-process denial, this ADR),
HC-01 hash-chain fork (284 broken joins), CRIT-1C mandatory-evidence gate.

---

## 1. Context — why the lease exists at all

HC-01 (`src/kernels/audit`, the SQLite hash chain that is the authoritative
tamper-evident audit) is **forked** in the production store: 284 broken joins and
169 duplicate sequence numbers, with `verify_integrity()` returning False. The
root cause was concurrent writers appending to the chain without a fence.

To stop that class of corruption, `src/kernels/audit/fencing.py` implements a
single-writer lease: exactly one writer may append at a time, enforced by a
monotonic fencing token stored *in the audit database itself* (so the lease check
and the append share one transaction — no external dependency).

The mandatory-evidence gate (`src/kernels/_crosscutting.py:850-878`) then makes
audit availability a precondition: for any HIGH/CRITICAL action, if the evidence
write fails, the action is **hard denied** (`PolicyDeniedError`). That fail-closed
property is deliberate and correct (CRIT-1C, D17 Option C).

## 2. The behaviour, precisely

- Owner identity: `audit-store:<pid>` (`AuditStore._lease_owner`).
- A live lease is **refused** to any *different* owner, even if that owner's pid
  is demonstrably alive — split-brain prevention.
- A lease whose owner process is **dead** is taken over (Fix A).
- Since 2026-09-26 (U38), a lease whose owner string is **identical** is
  *renewed*, not refused — previously a store re-created inside one process
  fenced itself out and denied every HIGH/CRITICAL action for the full TTL.
- An active holder refreshes the lease as it writes (30s TTL), so a live,
  writing process holds it indefinitely.

## 3. Consequence — the limitation this ADR records

**Two live processes cannot share one audit database.** The second process cannot
obtain evidence, so the fail-closed gate denies **every** HIGH/CRITICAL action in
it. The failure is not graceful: it happens during bootstrap.

Measured (reproducible, `D:\cache\temp\liuhao_xproc_test.py`):

```
PARENT wrote OK. lease row = (1, 'audit-store:20952', ...)   # parent pid 20952
CHILD  rc = 1
  File "src/kernels/capability/__init__.py", line 317, in get_capability_registry
  File "src/kernels/_crosscutting.py", line 878, in wrapper
src.kernels._crosscutting.PolicyDeniedError: policy denied
    action='capability.register' verdict=error rule=default_deny
```

The child cannot even construct the capability registry, because
`capability.register` is itself a governed action. So:

> Any second process — an agent subprocess, a worker, a crash-recovery child —
> is denied all governed work for as long as the first process keeps writing.

For a system whose roadmap targets million-scale, multi-process operation, a
single-writer-per-database audit store is a hard scalability ceiling, not a
tuning problem.

## 4. Options considered

| Option | Preserves fork-safety? | Allows multi-process? | Cost / risk |
|---|---|---|---|
| **A. Status quo** (lease held by one writer for its lifetime) | Yes | **No** | Availability ceiling; every extra process is denied. |
| **B. Per-append leasing** — acquire, append, release inside one transaction | Yes (writes still serialised by lease + SQLite) | Yes | Changes fencing semantics; must prove no interleaving. Needs full re-verification of `tests/kernels/audit/*` and the C-6 guard. |
| **C. Dedicated audit writer** — all processes forward events to one append-only writer | Yes | Yes | New subsystem + IPC; a new failure domain, but the cleanest long-term shape. |
| **D. Per-process audit DB** | Yes | Yes | **Rejected:** fragments the single authoritative chain; forensics across processes becomes impossible. |
| **E. Bounded wait on contention, then fail closed** | Yes | Partially (helps bursts, not a persistent holder) | Turns an instant denial into a timeout; does not raise the ceiling. |

## 5. Decision

- ~~Keep A (status quo).~~ **SUPERSEDED — Option B is implemented and verified.**
- **Do not weaken the gate.** The denial was the fail-closed contract working; the
  defect was the *topology*, not the gate.

### 5b. What B actually changed (implemented)

The decisive discovery: the append was **never atomic**. `_log_event_locked` ran
`SELECT last_seq, last_hash FROM chain_state` *outside* any transaction, then
INSERTed and committed later. Two writers could read the same `last_seq` and both
append it — the fork. The process-lifetime lease was papering over a
non-atomic read-modify-write. (The comment claiming "the fence check and the
append are atomic on the same connection" was aspirational: the lease committed
its own transaction and the append then ran separately.)

B fixes the actual defect and rescales the lease:

1. **Atomic append** — `BEGIN IMMEDIATE` … read chain_state → compute →
   INSERT event → UPDATE chain_state → COMMIT. The read-modify-write is now one
   transaction, so SQLite serialises concurrent writers at the DB level.
2. **Lease scoped to one append** — `SqliteWriterLease.acquire_within()` /
   `release_within()` take and yield the lease *inside* that same transaction, so
   the fence check and the append really are atomic, and the yield rolls back if
   the append rolls back (they can never disagree).
3. **No retry loop needed** — contention is absorbed by SQLite's write lock (a
   competing process waits for the current append) instead of surfacing
   `StaleWriterError`, which the fail-closed gate read as "evidence unavailable".
   A genuinely *fenced* writer (a newer live owner) is still refused immediately.

Properties preserved: exactly one writer appends at a time; a different live
owner is still refused; a dead owner is still taken over (Fix A); fail-closed is
untouched (if the append cannot commit, the error still propagates and the
mandatory-evidence gate denies the action).

**One existing test was retargeted, deliberately and transparently:**
`test_second_writer_cannot_acquire_while_lease_held` asserted that after a write
the lease was *still held* — an implementation detail of topology A. It now
asserts the real invariant (while the lease IS held, a different live owner is
refused) and a new test `test_lease_is_yielded_after_append` pins the per-append
contract. The safety assertion was not weakened, only re-anchored.

## 6. Verification (done, not planned)

1. `tests/kernels/audit/*` green — 83 passed.
2. C-6 guard re-run: ALL GREEN (7 checks).
3. **New `tests/kernels/audit/test_multiprocess_append.py`** — 5 tests proving
   N processes (2/4/6) appending concurrently to one database produce:
   exact row count (no lost append), seq exactly 1..N (no duplicates, no gaps),
   0 broken joins, chain_state anchor == tail, and `verify_integrity() is True`.
4. **Discrimination proof**: all 5 tests FAIL when the fix is reverted
   (reproducing `StaleWriterError: lease held by 'audit-store:<pid>' (alive=True)`)
   and PASS with it. A test that passes both ways proves nothing — the
   capability-registry test was strengthened for exactly this reason.

### Still open (C, the scale path)

Per-append leasing means each append takes and yields the lease, so throughput is
bounded by SQLite's single-writer serialisation. That is correct but is not a
million-events-per-second shape. Option C (**dedicated append-only audit writer**
that all processes forward to) remains the end-state; B removes the correctness
ceiling, C removes the throughput ceiling. Not needed until throughput is the
binding constraint — measure before building it.

## 7. Option C1 — batched atomic append (implemented, measured)

B made multi-process correctness work. It did not change the throughput shape,
so before designing a separate writer process the cost was measured rather than
assumed (`scripts/bench_audit_append.py`, baseline written to
`docs/autonomous/performance-baseline.json`):

| what | result |
|---|---|
| hashing alone, no database | **~67,000/sec** — hashing is *not* the bottleneck |
| single append (one transaction per event) | **~2,250/sec**, p95 0.47 ms, p99 2.06 ms |
| batch of 10 | ~9,950/sec |
| batch of 100 | ~14,200/sec |
| batch of 250 | **~16,500/sec** |
| 4 processes contending for one DB | ~1,500/sec incl. start-up (see baseline for the start-up-corrected figure) |
| re-verify 15,000 events | 404 ms |
| re-verify 23,000 events | 590 ms |
| disk per event | ~478 bytes |

The cost is **per transaction**, not per event. That makes C's real lever
batching, not a separate writer process — so C1 was built first, and the
dedicated-writer option is deferred to C2 with a measurement to justify it.

### Semantics C1 guarantees (each is pinned by a test)

* **Order** — events append in the caller's list order; `seq` increases by
  exactly one per appended event; `prev_event_hash` chains through the batch and
  onto the previously committed tail. List order is the only defensible
  definition for a scheduler-independent pipeline.
* **Atomicity** — all or nothing. "Committed" means every non-duplicate event in
  the batch is durable. A crash mid-batch rolls the whole batch back: never a
  partial batch, never a gap in `seq`.
* **Idempotency** — a caller-supplied `event_id` already committed is skipped; it
  consumes no `seq` and creates no duplicate. This is what makes
  retry-after-uncertain-outcome safe.
* **Fencing** — the lease is taken once per batch, inside the same transaction,
  so a fenced writer is still refused.
* **Fail-closed** — if the batch cannot commit, the error propagates and the
  mandatory-evidence gate denies the governed action.

### Two intermittent defects found while measuring (both fixed)

Neither was visible in inspection or in a single test run; both were found by
running the experiment repeatedly and counting failures.

1. **`SQLITE_READONLY` ("attempt to write a readonly database")** — with several
   processes opening one database, SQLite intermittently fails while the WAL
   shared-memory file is created or torn down by a peer. Measured: 6 processes
   × 12 rounds failed in 2–4 rounds; with the process starts staggered, 0/12.
   Retrying on the *same* connection never recovers — the handle is poisoned —
   so `_write_with_retry()` rolls back and **re-opens**, bounded, and only for
   BUSY / LOCKED / READONLY. After: **0/20**.
2. **`UNIQUE constraint failed: chain_state.id`** — two processes both observe
   "no anchor yet" and both insert the seed row. The seed is now
   `INSERT OR IGNORE`, and the whole schema migration runs inside
   `BEGIN IMMEDIATE` so initialisation serialises on SQLite's write lock.

Both have deterministic regression tests (`tests/kernels/audit/test_write_retry.py`);
each was proven to fail with the exact production symptom when the fix is reverted.

### Verified

`tests/kernels/audit/*` 97 passed; full suite 2927 passed / 20 skipped / 0 failed;
C-6 ALL GREEN (7 checks).

## 8. Human-sovereign input (isolated, non-blocking)

None required. If the intended deployment topology is *deliberately*
single-process-per-audit-store, B is harmless (it is strictly more permissive but
no less safe). Building C2 is a scale decision; engineering will measure first —
and the measurement so far says the next real constraint is **verification cost
growing linearly with the chain**, not append throughput.
