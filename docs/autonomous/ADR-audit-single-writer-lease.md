# ADR — Audit store writer lease: single-writer per database (U39)

**Status:** ACCEPTED (current behaviour) — with an OPEN remediation item below.
**Date:** 2026-09-26
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

- **Keep A (status quo) as the current, verified behaviour.** It is correct and it
  is what protects HC-01 from further forking. This ADR records the limitation
  rather than allowing it to be discovered again as a surprise.
- **Do not weaken the gate.** The denial is the fail-closed contract working; the
  defect is the *topology*, not the gate.
- **Remediation path: B now, C later.** Per-append leasing (B) removes the
  ceiling while keeping every write serialised under one transaction. The
  dedicated writer (C) is the end-state for multi-node scale.
- `tests/kernels/execution/test_execution_journal.py::TestCrashRecovery` now gives
  its child process a private `AUDIT_DB_PATH`. That is *test scoping*, not a fix:
  the test is about journal durability across a kill, not about cross-process
  audit contention. The production limitation above is unchanged by it.

## 6. Verification requirements before B ships

1. `tests/kernels/audit/*` fully green (fencing, durability, recovery, hash chain).
2. C-6 guard (`scripts/verify_armed_actions_are_inert.py`) ALL GREEN.
3. A new test proving N processes appending concurrently produce a chain with
   **zero** broken joins (the property HC-01 lost).
4. A test proving a crashed writer mid-append leaves no partial row and its lease
   is recoverable within TTL.

## 7. Human-sovereign input (isolated, non-blocking)

Not required to proceed with B. If the intended deployment topology is
*deliberately* single-process-per-audit-store, then A is final and this ADR
closes with no code change. That is a product/deployment decision; engineering
defaults to the safer path (A) until told otherwise, and B/C are prepared.
