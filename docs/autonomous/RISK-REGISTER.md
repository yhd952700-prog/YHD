# LIUHAO — Risk Register (LIVING)

> **Scope note (2026-09-26, c2-scaling).** This file did not exist; it is seeded
> here with the risks introduced by **Audit Storage Generation 2**
> (`ADR-audit-storage-generation-2.md`, C2 and C3). The pre-existing discovery
> register remains `UNKNOWN-TO-OWNER.md` (U1–U53) and the decision backlog
> remains `HUMAN-DECISION-BACKLOG.md`. Append new risk IDs at the end; never
> renumber. A risk is retired only by recording the evidence that retires it.
>
> Columns used below:
> **Risk** · **Why it appears** · **How it is detected** · **Fail-closed
> behaviour** (what the system does when it cannot prove things are fine) ·
> **Who decides** (the role that owns the call — engineering, or HUMAN).

---

## 1. C2 risks — "one file, one writer, group commit"

### R-G2-01 — Group-commit window converts a throughput problem into a latency problem
- **Why it appears:** C2-1 deliberately batches whatever arrives in a 5 ms
  window. Under sustained overload the queue grows and every caller waits
  longer; `log_event()` latency is no longer bounded by the append but by the
  backlog.
- **How it is detected:** queue depth, p50/p99 commit latency, and
  `audit_commit_window_ms` as first-class metrics; alarm at p99 > 50 ms.
- **Fail-closed behaviour:** the queue is **bounded and blocking** — backpressure,
  never drop, never "queued but not committed" success. A future that is not
  resolved within its timeout raises, and the mandatory-evidence gate denies the
  governed action.
- **Who decides:** engineering (window size, bound, timeout). A sustained breach
  is an SLO decision for the human.

### R-G2-02 — The append service becomes a single point of failure for the evidence channel
- **Why it appears:** concentrating all writes in one writer thread/process is
  exactly what buys the throughput, and it also concentrates the failure.
- **How it is detected:** writer liveness heartbeat + `audit_evidence_status`
  (already exists in `src/reliability/audit_metrics.py`) + `audit_failure_total`
  monotonic counter.
- **Fail-closed behaviour:** writer dead ⇒ every pending future raises ⇒ every
  HIGH/CRITICAL action is denied. The store itself is untouched and recovers by
  restart (deterministic: `chain_state` is the resume point).
- **Who decides:** engineering. Note this is *not* a new failure domain in kind —
  today the same outage is produced by a lease refusal — but it is now produced
  by one component's liveness, so the heartbeat is mandatory, not optional.

### R-G2-03 — `link_hash` backfill diverges from what a replay would produce
- **Why it appears:** C2-3 adds a cumulative hash over the whole existing chain.
  A backfill bug, a non-deterministic ordering (`ORDER BY seq` is unstable on the
  forked DB's duplicate `seq` values), or a partial failure mid-backfill leaves a
  column that looks populated and is wrong.
- **How it is detected:** three independent gates (ADR §6 step 3): the legacy
  verifier still passes; a **second, separately-written implementation** replays
  and compares row by row; a determinism proof (re-running on a fresh copy yields
  byte-identical output). Row-ordering must be by `rowid` (stable), never by
  `seq` (unstable under duplicates) — the same trap `recovery.py` already
  documents.
- **Fail-closed behaviour:** any failing gate discards the migration; the column
  is nulled and the old verifier remains authoritative. The migration never
  "mostly succeeds".
- **Who decides:** engineering, with the independent- implementation review done
  by a *different* agent than the one who wrote the migration.

### R-G2-04 — `synchronous=FULL` throughput regression exceeds the measured 14%
- **Why it appears:** MEASURED 14% at batch=250 on *this* machine. On a
  deployment with a slower device, a network filesystem, or an antivirus filter
  in the write path, fsync is far more expensive and the same setting can cost
  much more.
- **How it is detected:** the batch × `synchronous` matrix (ADR §2.1) becomes
  part of `scripts/bench_audit_chain.py` and runs as a CI benchmark gate on the
  target platform, not only on developer machines.
- **Fail-closed behaviour:** throughput loss is **not** a safety event — the
  system slows, it does not lie. But if throughput falls below the point where
  the evidence channel can keep up, **R-G2-01** triggers (bounded queue ⇒
  denial), which is the correct escalation path.
- **Who decides:** engineering for the measurement; the durability-vs-throughput
  posture (never `OFF`, never back to `NORMAL` once C2-1 ships) is fixed by ADR
  §7.4 and is **not** re-openable as a tuning knob.

### R-G2-05 — Dropping the query indexes silently removes a query capability
- **Why it appears:** C2-4 removes `idx_principal` / `idx_scope` /
  `idx_timestamp` / `idx_correlation` from the authoritative store (+32%
  MEASURED). Any query path that quietly depended on one of them becomes a full
  scan — invisible in correctness tests, obvious only in production latency.
- **How it is detected:** query-plan assertion tests against the derived read
  projection (`EXPLAIN QUERY PLAN` must not contain `SCAN` for the indexed
  predicates), plus a latency SLO on the projection.
- **Fail-closed behaviour:** a missing index in the projection degrades queries,
  never evidence. If the projection is unavailable, queries fail loudly — they
  must never fall back to claiming completeness from a partial projection.
- **Who decides:** engineering. The *capability* (these queries must be
  answerable) is not negotiable — only its location is (ADR §7.2 ordering:
  capability > architecture).

### R-G2-06 — `UNIQUE(seq)` cannot be created on the forked production DB
- **Why it appears:** the live store has 169 duplicate `seq` values. The
  constraint that would make the HC-01 fork class unwritable is exactly the
  constraint the existing data violates.
- **How it is detected:** the migration attempts the constraint and **fails**;
  that failure is the intended signal.
- **Fail-closed behaviour:** migration aborts, nothing is rewritten, the store
  remains in its (declared, UNVERIFIED) prior state. The constraint is never
  worked around with `IGNORE`, a partial index, or a "from now on" trigger —
  any of those would produce a store that claims a guarantee it does not have.
- **Who decides:** the fork disposition is already an open **HUMAN DECISION**
  (EXECUTION-QUEUE §Next autonomous work). Engineering must not pre-empt it.

### R-G2-07 — Segmented verification records a green it did not earn
- **Why it appears:** C2-6 replaces the O(N) full scan with
  `verified_upto_seq` + `verified_link_hash`. The marker is itself now a claim
  about history, and a marker advanced past an unverified segment is a **false
  green** — the worst possible outcome, worse than not verifying.
- **How it is detected:** the marker may only advance **inside the same
  transaction** that verified the segment; a startup check re-derives the tail
  `link_hash` from `verified_upto_seq` and compares it to
  `verified_link_hash`; mismatch ⇒ the marker is discarded and the range is
  re-verified.
- **Fail-closed behaviour:** on any doubt, the marker resets to the last
  provably-verified position and `audit_integrity_ok` goes to 0
  (Evidence=unverified), never to 1.
- **Who decides:** engineering. "Report unverified as unverified" is a standing
  boundary (GOVERNANCE §2.2, no faking success).

### R-G2-07b — A **false red**: a healthy chain is reported broken (mirror risk of R-G2-07) — **CODE FIXED 2026-09-27; retained as a regression guard**
- **Why it appears:** verification reads `audit_events` and `chain_state` as two
  **separate autocommit SELECTs**, so SQLite gives each its own MVCC snapshot.
  Any append that commits between the two reads makes
  `chain_state.last_seq != last_row.seq`, and the verifier reports **broken**.
  The window is the whole first `fetchall()` — at the MEASURED 26.4 µs/row that
  is ~0.2 s at 32k rows, ~2 s at 317k rows, ESTIMATE ~67 s at 10M rows, i.e. the
  window *grows with the chain* precisely when verification matters most.
  *(Reported by `access-audit`, relayed via `os-impl`, against the in-flight C2
  code; re-anchor line numbers before acting — the file is under active edit.)*
- **Status — resolved in code (2026-09-27):** `_begin_read_transaction` /
  `_end_read_transaction` (`__init__.py` ~L902-927) now pin one MVCC snapshot
  for both reads; `_verify_integrity_on` (~L933-955) uses it. Reported and
  re-anchored by `access-audit`; the code's own comment (L934-939) restates the
  failure mode. **Retained below as a regression guard, not as an open defect.**
- **How it is detected (O(1) criterion — preferred, from `access-audit`):** in a
  single `BEGIN DEFERRED` transaction read `chain_state.last_seq` and
  `MAX(audit_events.seq)` and assert they are equal. On an append-only chain the
  two are equal at every instant, so: equal ⇒ the earlier inequality was
  cross-snapshot skew (false red); unequal ⇒ genuinely broken. This is the same
  primitive the fix uses, so the repro and the regression test share it.
  (Heavier alternative, still valid: re-run against a `VACUUM INTO` snapshot.)
  The regression test must **force** the window — inject a sleep or a committing
  append between the two SELECTs — and assert the pinned version still passes.
- **Fail-closed behaviour — and this is the trap:** fail-closed converts a red
  verdict into **denial of every HIGH/CRITICAL governed action**. A false red is
  therefore not merely a noisy alert, it is a **self-inflicted availability
  outage whose duration grows with the chain** — the exact failure mode of U50,
  reached by a different route. Mitigation is to make both reads share one
  snapshot (`BEGIN DEFERRED` … `ROLLBACK`) so the comparison is well-defined;
  and to treat "verified on the snapshot" as the authoritative verdict, with the
  live read used only as a cheap pre-check.
- **Who decides:** engineering (the fix is a transaction-scoping change). The
  rule that a red verdict must never be auto-repaired or auto-suppressed is a
  standing boundary.

### R-G2-07c — Long O(n) work holding the append lock (backfill) — the lock is NECESSARY; do not "optimise" it away
- **Why it appears:** `backfill_link_hashes`-style migrations take `self._lock`
  and then `BEGIN IMMEDIATE` + a bulk UPDATE over the whole chain. The lock is
  the same one `log_event()` needs, so the migration's duration is an append
  outage; at 10M rows that is minutes to hours of every HIGH/CRITICAL action
  being denied by the fail-closed gate.
- **⚠️ RETRACTED GUIDANCE — do not release the lock mid-backfill.** An earlier
  version of this entry recommended "chunk + release `self._lock` between
  chunks". **That advice is wrong and would corrupt the chain permanently.**
  `link_hash` is a *forward-cumulative* value: an appending writer derives its
  `link_hash` from `chain_state.last_link_hash`. If the lock is released after
  chunk *k*, `chain_state.last_link_hash` is only correct up to chunk *k* while
  rows *k+1..N* are still un-backfilled — so a concurrent append would compute
  and **persist** its `link_hash` from a stale cumulative value. That is an
  irreversible broken chain, strictly worse than the starvation being fixed.
  (Established by the code comment at `__init__.py` ~L1428-1432 and confirmed
  by `access-audit`, who retracted their own original recommendation.)
  **The correct reclassification: this is not a superfluous lock — it is a
  necessary lock whose hold time is unbounded, unprogressed and unobserved.**
- **How it is detected:** lock-hold-time instrumentation (acquisition → release)
  exported as a metric; alarm at any hold > 1 s; a migration progress counter
  (rows done / rows total + estimated finish) so an operator can distinguish
  "slow" from "hung" and can tell *how long writes are stopped*.
- **Fail-closed behaviour — the two admissible shapes:**
  1. **Preferred: run the backfill on an offline copy.** `snapshot_audit_db`
     (`src/kernels/audit/durability.py:57`, `VACUUM INTO`) already exists;
     backfill the copy, verify it, then cut over. The lock problem disappears
     entirely.
  2. **If it must run live: keep the lock for the whole run**, chunk only to
     bound individual transaction size (the code already uses `executemany`,
     ~L1455-1460 — that part is correct), and add progress + ETA logging so the
     write-stop is a *declared* window rather than an open-ended hang.
  Either way: an un-backfilled row's `link_hash` is NULL and **must be read as
  UNVERIFIED**, never as "no link, therefore fine" — otherwise a partially
  backfilled store reports success it has not earned (this is R-G2-07 applied
  to migration state).
- **Who decides:** engineering for the mechanism and the observability; running
  it against the production (forked) DB remains gated on the HC-01 fork
  disposition, which is a **HUMAN** decision. Whether a multi-hour write stop is
  acceptable at all is an operational judgment for the human, and it should be
  decided *before* the migration starts, not discovered during it.

### R-G2-07d — Snapshot pinning covers only 1 of 5 verification entry points
- **Why it appears:** `_begin_read_transaction` (added to fix R-G2-07b) is
  called from exactly one place, `_verify_integrity_on`. Four other entry points
  still issue multiple statements without a pinned snapshot:
  `verify_segment` (~L1084 → `verification.verify_segment`, which reads events,
  then `tail_seq` on `chain_state` at `verification.py:209-213`, then
  `coverage_frontier` on checkpoints at 216-219, then `rooted_at_genesis` at
  300+), `verify_incremental` (~L1123), `audit_checkpoints` (~L1365), and
  `recompute_checkpoint` (~L1381).
- **Why it matters:** same failure class as R-G2-07b (comparing values taken
  from two different MVCC snapshots), but landing on C2's *checkpoint* semantics
  — e.g. `_verify_incremental_on` reads `tail_seq` under snapshot A, verifies a
  segment under snapshot B, then writes a checkpoint from that result. A false
  red here is equally a fail-closed denial; a false *green* here would be worse
  (R-G2-07).
- **How it is detected:** the O(1) criterion from R-G2-07b applied inside each
  entry point; plus a structural check — assert that every function performing
  more than one read for a cross-comparison is wrapped in
  `_begin_read_transaction`. A simple lint/AST check can enforce this, because
  the pin is a single named primitive.
- **Fail-closed behaviour:** unchanged from R-G2-07b — an inconclusive read is
  UNVERIFIED/deny, never success.
- **Who decides:** engineering. Cost of the fix is ~zero (hoist the existing
  `_begin_read_transaction` to the outer level of those four entry points; same
  primitive already proven). *(Reported by `access-audit`, 2026-09-27.)*

### R-G2-08 — Widening `event_id` leaves a mixed-width population
- **Why it appears:** C2-5 changes *generation*, not stored values; the DB
  carries both 48-bit and 128-bit ids indefinitely.
- **How it is detected:** a one-off scan counting `LENGTH(event_id)`; expected
  to be non-zero for legacy rows and is informational, not an error. The real
  check is that **no new** 12-char ids appear after the change.
- **Fail-closed behaviour:** a collision after the change is treated as a real
  duplicate (correct) and, in the single-append path, raises → denial. It is
  never silently swallowed.
- **Who decides:** engineering.

---

## 2. C3 risks — "sharded chains under one epoch anchor"

### R-G3-01 — A down shard stalls the global epoch indefinitely
- **Why it appears:** the anchor writer **must not** close an epoch with a
  missing member (closing would let that shard add epoch-E events retroactively
  and undetectably). So one unavailable shard stops global anchoring for
  everyone.
- **How it is detected:** `audit_anchor_lag_epochs` (epochs behind) plus
  per-shard liveness and per-shard `last_sealed_epoch`. Lag > 1 epoch is an
  alert, not a warning.
- **Fail-closed behaviour:** the epoch stays open. Ordinary actions on *other*
  shards continue (their shard commits are still evidence); actions carrying
  `require_anchor_seal=True` are **denied** until the epoch closes. The lag is
  reported — never hidden behind a green "verified".
- **Who decides:** engineering for the thresholds. Whether `require_anchor_seal`
  is ever *mandatory* for a given action class is a governance decision.

### R-G3-02 — Two different anchor values for the same epoch (anchor fork)
- **Why it appears:** a torn seal, a restored-from-backup shard, or a second
  anchor writer that did not observe the first's lease. It is the C3 analogue of
  the HC-01 fork, and it is the one failure that silently invalidates the whole
  "one chain" claim.
- **How it is detected:** sealing is `INSERT … ON CONFLICT(epoch) DO NOTHING`
  followed by a read-back compare; a stored value differing from the computed
  value is a hard error. Plus the epoch chain check in `verify_global`
  (ADR §4.4 step 2).
- **Fail-closed behaviour:** **all writes halt**, the anchor writer refuses to
  start, and the condition escalates. It is never repaired automatically —
  automatic repair of a fork is how HC-01 got a "fixed" chain nobody could
  trust.
- **Who decides:** engineering detects and halts; the remedy is a **HUMAN**
  decision (it is the same class as the existing HC-01 fork disposition).

### R-G3-03 — Routing drift puts the same event in two shards
- **Why it appears:** a retry re-routed after a failed first attempt, or a shard
  key / `S` change applied mid-epoch. The same `event_id` then exists in two
  chains, which is a duplicate the per-shard dedup cannot see.
- **How it is detected:** routing is **carried in the event**, never recomputed;
  the router validates `event.shard_id` against `shard_map[shard_epoch]` and
  refuses a mismatch. Plus a global `(event_id → shard_id)` uniqueness audit.
- **Fail-closed behaviour:** the router **refuses the event** (deny), it does not
  "helpfully" pick another shard.
- **Who decides:** engineering (construction, ADR §7.9 — re-routing is refused
  outright).

### R-G3-04 — Correlated load skew (a hot shard)
- **Why it appears:** with `shard = hash(correlation_id) mod S`, a very chatty
  correlation (a long-running agent) concentrates on one shard and one write
  lock, reproducing C1's contention there while other shards idle.
- **How it is detected:** per-shard queue depth and per-shard eps; skew ratio
  (max/min) as a metric.
- **Fail-closed behaviour:** backpressure on the hot shard ⇒ latency ⇒ denial,
  exactly as R-G2-01. Never drop, never re-route (re-routing breaks R-G3-03).
- **Who decides:** engineering for the alerting; a persistent skew that a
  rebalance cannot fix is a scale/design escalation.

### R-G3-05 — Loss of one shard file invalidates the whole log set
- **Why it appears:** `verify_global` is a conjunction over all shards; it
  cannot be "mostly true". Losing one shard's history therefore downgrades the
  *entire* set from VERIFIED to BROKEN, even though S−1 shards are intact.
- **How it is detected:** per-shard verification in `verify_global` step 1;
  a shard that cannot be opened fails the whole run.
- **Fail-closed behaviour:** global integrity reports broken; `audit_integrity_ok
  = 0`. The system does **not** report the surviving shards as "the verified
  part" unless and until a human declares a scope limitation.
- **Who decides:** engineering reports; the *legal* consequence of a partial
  chain (what it can still prove, to whom) is a **HUMAN / governance-legal**
  decision.

### R-G3-06 — No atomic snapshot across S shard files + the anchor DB
- **Why it appears:** `VACUUM INTO` is per-file. A backup of S+1 files taken at
  different instants is not a consistent set: it can contain epoch-E events from
  shard A with an anchor that sealed epoch E−1. Restoring it produces a set that
  fails completeness (ADR §4.4 step 3) — or, worse, that somebody "fixes" by
  re-sealing.
- **How it is detected:** the restored set must pass `verify_global` including
  completeness; a snapshot carries `epoch_set` metadata naming the
  `(shard, last_sealed_epoch)` vector, and a restore refuses a mixed vector.
- **Fail-closed behaviour:** refuse to restore a non-epoch-consistent snapshot.
  Recovery is to the last epoch-consistent snapshot, and the gap is reported as
  lost evidence — never reconstructed.
- **Who decides:** engineering for the mechanism; **R8/R11** (recovery mandates
  `VACUUM INTO`) already binds, and U31's "refuse to unpack an archive that does
  not verify" is the precedent to follow.

### R-G3-07 — Re-sharding (`S` change) is a correctness-critical operation
- **Why it appears:** changing `S` changes every event's home shard. Done
  mid-epoch it splits the causal history of a `correlation_id` across shards and
  silently loses intra-correlation ordering — the one ordering C3 promises.
- **How it is detected:** rebalance only at a sealed epoch boundary; a
  `shard_epoch` bump invalidates stale routes (R-G3-03); the derived global view
  is compared before/after.
- **Fail-closed behaviour:** the router refuses events whose `shard_epoch` does
  not match the live map; the rebalance itself is a sealed, manifest-recorded
  operation with a rollback (merge back to S=1 by deterministic replay).
- **Who decides:** engineering for the mechanism; changing `S` in production is
  an operational change with a **human-signed** runbook (it is irreversible in
  the same sense the HC-01 migration is).

### R-G3-08 — The unanchored tail is mistaken for verified evidence
- **Why it appears:** events after the last sealed epoch are durable and
  per-shard-verified but not globally anchored. Anyone reading "verified" without
  reading `sealed_upto_epoch` will over-claim.
- **How it is detected:** `verify_global` always returns
  `(integrity_ok, sealed_upto_epoch, unanchored_tail_events)`; the object handed
  to an external timestamp authority (HD-05) is **only** `global_anchor[E]`,
  never a tail.
- **Fail-closed behaviour:** an unanchored range is reported as UNANCHORED, not
  as verified. `require_anchor_seal` actions wait or are denied.
- **Who decides:** engineering for the reporting; the admissibility of
  unanchored evidence is a **HUMAN / governance-legal** question.

### R-G3-09 — Anchor-writer fencing across nodes (only when C3 federates)
- **Why it appears:** the anchor writer is a single writer by construction. The
  moment C3 spans nodes, "single writer" needs a cross-node lease, and this repo
  has no such primitive (`src/distribution/lock.py` is a Redis-less stub, U12).
- **How it is detected:** fencing-token monotonicity assertions; a writer whose
  token is not current is refused immediately.
- **Fail-closed behaviour:** refusal to write, not a best-effort write. A
  cross-node anchor without a proven fence must not ship.
- **Who decides:** engineering, gated on U12 being closed. **Not** part of the
  single-node C3 scope.

### R-G3-10 — Verification cost grows with the shard count × the chain length
- **Why it appears:** MEASURED 26.4 µs/row. Sharding parallelises verification
  across shards (good) but the anchor chain itself is a single sequential chain
  and the completeness check sums over all shards (grows with S per epoch).
- **How it is detected:** `verify_global` duration and rows/sec as metrics;
  scheduled verification off a snapshot, never on the live path.
- **Fail-closed behaviour:** if a scheduled verification cannot complete within
  its budget, the result is **UNVERIFIED** (not "assumed fine from last
  week"). This is R-G2-07 applied globally.
- **Who decides:** engineering.

---

## 3. Open actions (detection gaps with no owner yet)

These are not risks; they are **missing detectors** that a risk above depends
on. Each is listed because "the risk is registered" is worthless if nothing
observes it. An action is closed only when the detector exists and is proven to
fire.

### A-01 — UNASSIGNED: `AuditStore` lock-hold-time metric (root cause for R-G2-07c and U50)
- **What is missing:** no signal records *how long* `AuditStore._lock` is held.
  Two registered risks are invisible without it: R-G2-07c (a backfill can hold
  the append lock for minutes to hours) and U50 (a full-chain
  `verify_integrity()` holds the same lock — 26.4 µs/row MEASURED ⇒ ~264 s at
  10⁷ rows). Both surface only as "HIGH/CRITICAL actions are being denied",
  which is one layer too late to diagnose.
- **Spec (small enough to be uncontroversial):** record `t0` at lock
  acquisition and `t1` at release in every `with self._lock:` site in
  `src/kernels/audit/__init__.py`; export a gauge
  `audit_lock_hold_seconds_last` plus a monotonic counter
  `audit_lock_hold_seconds_total`; log a WARNING on any single hold > 1 s
  including the calling function name. Deliberately **not** a histogram — one
  scalar "last hold" plus one counter is enough to answer "is the lock stuck,
  and who is holding it".
- **Why it is unassigned:** `src/kernels/audit/**` is under the standing
  **D23/D24 prohibition** for `os-impl`, who reported the gap and cannot take
  it; `c2-scaling` is under a **docs-only** constraint for this task and cannot
  take it either. **Team-lead must name an owner.**
- **Interim coverage (not a substitute):** `os-impl` added a *consumer-side
  symptom* signal in `HostCommandBroker` (commit `9ecc62e4`) —
  `audit_failures`, `last_audit_error`, `last_audit_write_ms`, timed in a
  `finally` so it records on both the success and the fail-closed refusal path.
  That tells you **who felt it**; A-01 tells you **why**. Correlating the two is
  the intended reading: broker write latency rising *together with* a long lock
  hold confirms an append-lock stall rather than a broker-local problem.
- **Explicit non-goal:** this metric must never be used to justify weakening the
  gate. A long hold during a legitimate backfill or a legitimate full
  verification is a reason to **refuse** governed actions, not a reason to let
  them run unaudited.

---

## 4. Standing rule for every risk above

When detection is inconclusive, the system's answer is **UNVERIFIED**, never
VERIFIED. Every risk in this register resolves to one of three fail-closed
actions: **deny the governed action**, **refuse the write**, or **report
UNVERIFIED**. There is no fourth option where the system carries on and reports
success.
