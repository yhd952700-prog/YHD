# ADR — Audit Storage Generation 2: the 10× / 100× / 1000× scaling path (C1 → C2 → C3)

**Status:** **DESIGN (not implemented).** No code in `src/` or `tests/` was changed to
produce this document; it is research + design only.
**Date:** 2026-09-26
**Author:** c2-scaling (autonomous, team `p08-d19-d20-d21`), branch `p36`
**Supersedes / extends:** `ADR-audit-single-writer-lease.md` (Option B/C). This ADR
does **not** contradict it — §6 of that ADR named Option C ("dedicated append-only
audit writer") as the end-state and said "measure before building it". This is the
measurement and the design.
**Relates to:** HC-01 fork (284 broken joins / 169 duplicate seq), CRIT-1C
mandatory-evidence gate (`src/kernels/_crosscutting.py:850-878`), U9/U11/U14,
F1–F6, HD-05 (timestamp authority), HD-06 (retention), D22 (derived view),
ROADMAP §Architecture ("10× / 100× / 1000×").

---

### Reviewer addendum (team lead, 2026-09-26)

This ADR was produced by an autonomous worker. Its findings were **independently
re-checked against the code before being accepted**, not taken on trust:

* Finding 1 (48-bit `event_id`) — **confirmed** (`uuid.uuid4()[:12]`, two sites).
* Finding 2 (`verify_integrity()` holds the append lock for a full scan) —
  **confirmed** (`verify_integrity` → `with self._lock`).
* Finding 5 (`get_event` / `get_stats` inconsistent locking) — **confirmed**.
* Finding 3 (`synchronous=NORMAL` hardcoded; `configure_audit_durability` has no
  production caller) — **confirmed by grep**; only `test_durability.py` calls it.
* Finding 8 (`ALTER TABLE` migrations unguarded) — **now stale**: the whole
  schema migration was moved inside `BEGIN IMMEDIATE` after this ADR was written,
  so it is serialised on SQLite's write lock. Kept here as the historical reason
  that change was made.

Findings 1 and 2 have since been fixed with deterministic regression tests
(`tests/kernels/audit/test_scale_defects.py`); each was proven to fail with the
original behaviour. Findings 3, 4, 5, 7, 9 and 10 remain open.

---

## 0. How to read the numbers in this document

Every quantitative claim is labelled:

- **MEASURED** — produced on this machine (Windows 10 Pro, Python 3.13.14,
  SQLite 3.53.1, 24 logical cores) by a named command, against a throwaway
  database in a private temp directory. Nothing in the repo was read or written.
- **ESTIMATE** — derived from MEASURED numbers by stated arithmetic. No
  unverified constants, no vendor numbers, no "typical" figures.
- **CITED** — taken from another agent's measurement in this wave (the
  ~2,000 / ~23,000 / ~67,000 eps figures).

The three probes used are self-contained (they replicate the append SQL exactly
rather than importing `src.kernels.audit`, because that module was being edited
concurrently by another teammate — see §9):

```
# probe 1 — cost attribution + durability sensitivity
python D:\cache\temp\c2_audit_ceil_probe.py
# probe 2 — does one file scale with cores? (shared vs sharded) + verify cost
python D:\cache\temp\c2_audit_shard_probe.py
# probe 3 — per-row cost attribution at batch=250
python D:\cache\temp\c2_audit_index_probe.py
```

All three measure the *same* write shape as `AuditStore._log_event_batch_locked`:
`BEGIN IMMEDIATE` → lease upsert → read `chain_state` → N × (canonical JSON +
SHA-256 + `INSERT INTO audit_events`) → `chain_state` upsert → lease yield →
`COMMIT`. Event payload: a `details` dict with a 200-byte blob (541.7 B/row on
disk — **MEASURED**, probe 2).

---

## 1. Baseline: what the system actually delivers today

| path | throughput | label |
|---|---|---|
| `log_event()` — the ordinary caller path, one transaction per event | ~2,000 eps | CITED (team-lead, this wave); **MEASURED 2,916 eps** (probe 1, batch=1, WAL, `synchronous=NORMAL`) |
| `log_event_batch(250)` — Option C1 | ~23,000 eps | CITED (team-lead); **MEASURED 21,117 eps** (probe 1, batch=250) |
| SHA-256, standalone | ~67,000 eps | CITED (team-lead, through `AuditEvent.compute_hash`); **MEASURED 315,033 eps** (probe 1, raw `hashlib` loop over the same canonical payload) |

The two independent measurements agree within 15%, and both show the same
conclusion the team already reached: **hashing is not the bottleneck**. Hash
capacity exceeds the batched append rate by 3× (through the dataclass path) to
15× (raw primitive).

The headline "23,000/sec" is therefore *already the optimistic number*: it is
what you get only if the caller batches 250 events. An ordinary `log_event()`
caller — which is what essentially all of `src/` does today — sees ~2,000 eps.

**This ADR takes ~2,000 eps as the roadmap baseline**, because that is the
capability the system actually delivers to a governed action. The 23,000 figure
is recorded as the *batched* ceiling.

---

## 2. Why ~23k/sec is the ceiling for one SQLite file on one machine

### 2.1 The measurement

**MEASURED** (probe 1, batch=250 unless stated, WAL, isolated temp DB):

| variant | batch=1 | batch=10 | batch=50 | batch=250 | batch=1000 | batch=5000 |
|---|---|---|---|---|---|---|
| full append, `synchronous=NORMAL` | 2,916 | 10,652 | 15,190 | **21,117** | 21,088 | 20,909 |
| full append, `synchronous=FULL` | 811 | 5,555 | 12,347 | **18,123** | 19,924 | 19,608 |
| full append, `synchronous=OFF` | 4,964 | 15,936 | 19,259 | **23,124** | 15,959 | 19,889 |
| bare txn (1 INSERT + 1 upsert, no index/hash/JSON), `NORMAL` | 8,611 | — | — | **144,253** | — | — |
| bare txn, `FULL` | 999 | — | — | **100,682** | — | — |
| full append, `journal_mode=DELETE` (no WAL), `NORMAL` | **27** | — | — | 5,055 | — | — |

### 2.2 Which cost dominates

**The ceiling is per-event CPU inside one Python thread, not fsync.**

The proof is the `synchronous` column. At **batch=1**, `OFF` vs `FULL` is
4,964 vs 811 — a **6.1×** spread, i.e. one fsync-equivalent per event dominates
everything. At **batch=250**, `OFF` vs `FULL` is 23,124 vs 18,123 — only
**1.28×**. The fsync cost is per *transaction*; batching amortises it away, and
what remains at batch=250 is not disk at all.

**MEASURED** attribution at batch=250 (probe 3 — each row removes one more cost
from the previous row; µs/event derived as 1e6/eps):

| variant | eps | µs/event | cost removed |
|---|---|---|---|
| A. everything (4 secondary indexes + lease + canonical JSON + SHA-256) | 22,689 | 44.1 | — |
| B. minus the 4 secondary indexes | 29,931 | 33.4 | indexes ≈ **10.7 µs (24%)** |
| C. minus the lease upsert | 31,154 | 32.1 | lease ≈ **1.3 µs (3%)** |
| D. minus canonical `json.dumps` | 66,420 | 15.1 | canonical JSON ≈ **17.0 µs (39%)** |
| E. minus SHA-256 | 78,712 | 12.7 | hash ≈ **2.4 µs (5%)** |
| F. A but `executemany` instead of an execute loop | 23,675 | — | **+4%** only |

So the ~44 µs/event that produces the 23k ceiling decomposes as:
**canonical JSON ≈ 39%, four secondary indexes ≈ 24%, base INSERT+commit ≈ 29%,
SHA-256 ≈ 5%, writer lease ≈ 3%.**

Two consequences that drive the whole design:

1. **The largest single win is free.** Canonical JSON is 39% of the cost and it
   is currently computed *inside* the write transaction, even though the hashed
   payload depends only on the event's own content — never on the chain tail.
   (Confirmed by reading `AuditEvent.compute_hash`,
   `src/kernels/audit/__init__.py:180-199`: the hashed dict contains
   `event_id, event_type, principal_id, scope, timestamp, correlation_id,
   outcome, details` — **not** `prev_event_hash` or `seq`.) It can be computed
   before the transaction opens.
2. **`synchronous=FULL` is affordable once you batch.** 14% at batch=250
   (MEASURED). That closes the WAL/NORMAL power-loss gap (§8, U14) for a
   throughput price we can actually pay. This is the single most important
   *correctness-for-throughput* trade in this ADR, and it is a trade we win.

### 2.3 Why adding processes does not help (the actual ceiling)

**MEASURED** (probe 2, batch=250, 4,000 events per process; "parallel phase"
excludes interpreter startup):

| processes | ONE shared db — aggregate eps | ONE db per process — aggregate eps |
|---|---|---|
| 1 | 21,978 | 21,390 |
| 2 | **19,802** (per-proc 22,477 / 9,894) | **35,874** |
| 4 | **16,512** (per-proc 23,558 / 4,127 / 12,475 / 6,246) | **81,218** |

Aggregate throughput on one file **falls** as you add writers: 4 processes
deliver *less* than 1. This is SQLite's single-writer lock plus lock-convoy
unfairness — one writer gets 23.5k eps while another starves at 4.1k. Adding
cores to a single audit file is not merely unhelpful, it is negative.

With one file per process, scaling is near-linear: 3.80× at 4 shards.

**Therefore:** 23k eps is the ceiling for *one chain in one file*, and the
binding constraint is not SQLite's write lock per se — it is that all writers
must serialise on one lock, and what they do while holding it costs ~44 µs per
event in single-threaded Python. Two independent escape routes follow:
**do less work per event while holding the lock (C2)** and **stop sharing the
lock (C3)**.

### 2.4 WAL is load-bearing

**MEASURED**: `journal_mode=DELETE` (rollback journal) at batch=1 gives **27
eps** — 108× slower than WAL's 2,916, because every transaction creates, fsyncs
and deletes a journal file. Any regression that drops WAL (a "simpler" config, a
`VACUUM` that resets it, an embedded deployment with WAL unavailable) turns the
audit store into a 27-events-per-second system. This must be asserted at
startup, not assumed.

---

## 3. C2 — the 10× generation: one file, one writer, group commit

**Target: ≥20,000 eps on the ordinary single-event `log_event()` path (10× the
~2,000 baseline), ESTIMATE 45,000–55,000 eps sustained, with `synchronous=FULL`
on. One logical chain, no weakened property.**

C2 is five independent changes. Each is separately shippable and separately
revertable.

### 3.1 C2-1 — Group-commit append service (this is Option C from the old ADR)

Every `log_event()` enqueues onto a bounded queue owned by a single writer
(dedicated thread in-process, or a sidecar process). The writer drains the queue
and commits a transaction every `min(batch_size=250, window=5 ms)`, whichever
comes first. The caller blocks on a per-event future and returns only once its
event's transaction has **committed**.

Semantics, stated precisely:

* **Durability** — unchanged. "returned" still means "committed and durable".
  The difference is *which* transaction it committed in, not *whether*.
* **Ordering** — unchanged and still the caller-visible arrival order: events
  are drained in enqueue order and `seq` follows that order.
* **Atomicity** — unchanged: the batch is one transaction, all-or-nothing. A
  crash mid-batch rolls the whole batch back; the future raises; the
  mandatory-evidence gate denies. Already proven by
  `tests/kernels/audit/test_batch_append.py::test_hard_kill_mid_batch_commits_nothing`.
* **Idempotency** — strengthened, not weakened: a retry after an uncertain
  outcome carries the same `event_id` and is de-duplicated by the existing rule.
* **Fencing** — unchanged: the lease is taken once per group, inside the
  transaction.
* **Fail-closed** — unchanged: if the group cannot commit, every future in that
  group raises, and `_crosscutting.py:850-878` denies the governed action. There
  is no "queued but not committed" success state. A bounded queue that is full
  **blocks** (backpressure), never drops and never returns success.
* **New latency** — up to +5 ms per `log_event()` (the group window). This is
  the price and it is declared.

What it buys: today's 23k batched ceiling becomes the *ordinary* path. That
alone is 10× over the caller-visible baseline.

### 3.2 C2-2 — Compute the canonical payload and the content hash *outside* the transaction

`AuditEvent.compute_hash()` does not depend on the chain tail (§2.2), so both
the canonical bytes and `event_hash` can be produced at `log_event()` time,
before the writer touches the database. The writer then only binds parameters
and INSERTs.

**MEASURED** value: removing canonical JSON from the critical section is
+113% at batch=250 (31,154 → 66,420 eps, probe 3 C→D). This is the largest
single win in the whole roadmap and it costs nothing.

### 3.3 C2-3 — Add a cumulative `link_hash` (the cryptographic upgrade that makes C3 possible)

**This is the most important non-throughput change in C2.** Today
`event_hash = H(content)` and `prev_event_hash` is a *separate pointer column*
that is not covered by any hash. The chain is therefore "a set of independent
content hashes plus pointers". Consequences:

* no single value commits to the ordered prefix, so there is nothing cheap to
  **notarize / externally timestamp** (HD-05) — you would have to timestamp
  every event;
* no inclusion proofs — you cannot prove "event X is at position k" without
  replaying;
* truncation defence rests entirely on the `chain_state` row, which lives in
  the same file as the data it is defending.

Fix: add `link_hash`, computed as `link_hash_i = H(link_hash_{i-1} ‖
event_hash_i)`, with `link_hash_0 = H("genesis" ‖ event_hash_1)`. Now the tail's
`link_hash` commits to the entire ordered prefix, and:

* a **per-epoch** head hash can be externally timestamped — 1 TSA call per epoch
  instead of per event (this turns HD-05 from a theoretical capability into an
  affordable one);
* C3's cross-chain anchor (§4) becomes expressible at all.

Cost: **MEASURED** one extra SHA-256 per event ≈ 2.4 µs (5%). Additive column,
nullable, backfilled deterministically (§6). `prev_event_hash` is **kept** — the
existing verifier is not replaced, it is supplemented, and both must pass.

### 3.4 C2-4 — Move the four query indexes off the hot append path

**MEASURED**: dropping `idx_principal`, `idx_scope`, `idx_timestamp`,
`idx_correlation` (keeping `idx_seq`, which verification and ordered queries
need) is +32% at batch=250 (22,689 → 29,931 eps).

**This is not a capability reduction.** Per the owner's ordering (capability >
architecture > correctness > security > reliability > maintainability > scale),
query capability must survive. Compensation: those four indexes are moved to a
**derived read projection** — a separate database rebuilt from the chain, in the
same spirit as D22's `scripts/derive_audit_view.py`. The projection is
non-authoritative and rebuildable; losing it costs query latency, never
evidence. The authoritative store keeps only what integrity requires.

### 3.5 C2-5 — `synchronous=FULL`, `UNIQUE(seq)`, and a 128-bit `event_id`

* **`synchronous=FULL`** — closes the WAL/NORMAL power-loss window. **MEASURED**
  cost at batch=250: 14% (21,117 → 18,123). We take it. Note this *requires*
  C2-1: at batch=1 the same change costs 3.6×. **C2-1 and C2-5 ship together or
  not at all.**
* **`UNIQUE(seq)`** — the DB-level backstop that makes the HC-01 fork class
  *unwritable regardless of any future code regression*. Absent today
  (schema at `src/kernels/audit/__init__.py:250-345`). Cannot be added to the
  currently-forked production DB — it will fail, and that failure is the proof
  the migration is not yet safe (fail-closed, §6).
* **`event_id` 48-bit → 128-bit** — today `str(uuid.uuid4())[:12]` is 48 bits
  of randomness (`__init__.py:530` and `:675`). At 10M events the birthday
  collision probability is ≈18%; at 100M, ≈18 expected collisions. In the batch
  path a collision is **silently swallowed as a "duplicate"** — real evidence
  lost with no error. Fix: full `uuid4().hex` (128 bits).

### 3.6 C2-6 — Segmented, off-lock verification

Today `_verify_integrity_locked` (`__init__.py:758-763`) does a full
`fetchall()` of the chain, builds an `AuditEvent` per row, and holds the **same
lock as appends** for the whole scan. **MEASURED** 26.4 µs/row ⇒ 10M rows =
**264 seconds during which every append — and therefore every HIGH/CRITICAL
governed action — is denied** (ESTIMATE; reachable via
`refresh_audit_metrics(with_integrity_check=True)`). It also materialises the
whole chain in RAM (541.7 B/row ⇒ ~5.4 GB at 10M rows).

C2 replaces this with: (a) **segmented verification** — persist
`verified_upto_seq` + `verified_link_hash` and verify only the new tail, so cost
is O(new events), not O(chain); (b) **continuous verification off a
`VACUUM INTO` snapshot**, so the live store is never locked for more than one
segment; (c) a `verify_range(a, b)` primitive so C3's per-shard verification is
parallelisable.

### 3.7 C2 — what breaks if it is wrong

| change | failure mode | blast radius | detection |
|---|---|---|---|
| group commit | group window starves under load → `log_event()` latency unbounded | every governed action slows; nothing is lost | queue depth + p99 commit latency metrics, alarm at >50 ms |
| group commit | an event's future is never resolved (writer dies mid-group) | callers hang | bounded future timeout → raise → fail-closed deny |
| pre-hash | pre-computed hash disagrees with what is stored | chain break, caught by the next verify | content-hash recheck on commit (cheap, one hash) |
| `link_hash` | backfill diverges from replay | verification reports a break at the first divergent row | independent second implementation recompute (§6) |
| index removal | a query path was silently depending on a dropped index | query slow / full scan | query-plan assertion test on the projection |
| `synchronous=FULL` | throughput regresses more than 14% | slower only, never unsafe | benchmark gate in CI |
| `UNIQUE(seq)` | cannot be created on a forked DB | migration aborts (correct) | migration fail-closed (§6) |
| segmented verify | `verified_upto_seq` advances past a segment that was never checked | **a false green** | never advance the marker except inside the transaction that verified the segment |

**Rollback for C2:** every component is a flag or an additive schema change.
`C2-1` off ⇒ `log_event()` returns to one-transaction-per-event (slower,
identical semantics). `C2-2/C2-4` off ⇒ slower. `C2-3/C2-5` are tightenings and
are not rolled back independently — rolling back `synchronous=FULL` would be
*weakening* durability, which §7 forbids. Schema changes are additive columns;
a downgrade ignores them. There is no data rewrite in C2, so rollback is a
config change, not a restore.

---

## 4. C3 — the 100× generation: sharded chains under one epoch anchor

**Target: 100× over the 2,000 baseline ≈ 200,000 eps. MEASURED 81,218 eps at 4
shards (3.80×); ESTIMATE 160k–320k at 8–16 shards on this 24-core box (linear
extrapolation of the measured 3.80× at 4); beyond ~16 shards the next increment
is machines, not cores.**

### 4.1 Why naive sharding destroys the evidence chain — and what replaces it

"Shard per writer" as usually implemented (each writer gets a file, each file
has its own `chain_state`) produces **S independent genesis points and no global
order**. `verify_integrity()` on any one file passes while the log as a whole
has no defined sequence, no completeness guarantee, and no defence against a
shard being edited retroactively. That is Option D from the old ADR, already
rejected — correctly.

C3 keeps **one** verifiable evidence chain by splitting the notion of a chain
into two levels:

```
Level 1 (per shard, parallel):     event → event → event → …
   each shard owns its own seq space, its own prev_event_hash links,
   its own link_hash, its own chain_state, its own writer_lease.

Level 2 (global, one writer, ~1/s):
   epoch E:  for each shard s: checkpoint_s(E) = H(prev_cp_s ‖ s ‖ E ‖ seq_hi ‖ link_hash ‖ count)
   global_anchor(E) = H(global_anchor(E-1) ‖ canon(sorted([(s, checkpoint_s(E)) for all s])))
```

The shard chains give parallelism and per-shard tamper-evidence. The anchor
chain binds **all** shards, in **every** epoch, into a single ordered hash
chain whose head is one 32-byte value.

### 4.2 The epoch protocol (the correctness-critical part)

1. **Shard key.** Default: `shard = hash(correlation_id) mod S`. This is chosen
   over tenant/scope sharding for one decisive reason — **every event that
   shares a `correlation_id` lands in the same shard, so causal order inside a
   unit of work is preserved exactly**. It also makes idempotency exact: the
   same `event_id` always maps to the same shard, so the existing
   "already-committed `event_id` ⇒ skip" rule works unchanged and cannot be
   defeated by re-routing.
   *(A per-tenant mode is available for legal-hold locality, at the cost of
   cross-correlation ordering — see §4.6.)*
2. **Routing is carried, never recomputed.** A retry must carry the shard
   assignment decided on the first attempt. Re-routing a retry to a different
   shard would make the same `event_id` land in two chains — a silent duplicate.
   This is forbidden outright (§7.9).
3. **Epoch advance.** An epoch closes on `max(epoch_events=10,000,
   epoch_seconds=1)`. Whichever comes first.
4. **Sealing is per-shard and atomic.** Inside the shard's write transaction,
   closing epoch E writes `shard_checkpoint` and bumps `shard_epoch`. Because
   both happen in one transaction, a shard can never emit two checkpoints for
   one epoch, and can never write an event into an already-sealed epoch.
5. **The anchor writer never closes an epoch with a missing member.**
   `global_anchor(E)` requires a checkpoint from **all S** shards. An idle shard
   seals an *empty* checkpoint, so no shard can block the epoch by being quiet.
   **A shard that is down blocks the epoch — deliberately.** Closing an epoch
   with a missing member would allow that shard to add epoch-E events
   retroactively and undetectably. This is the single most important invariant
   in C3.
6. **Sealing is idempotent and fork-detecting.** `INSERT INTO global_anchor …
   ON CONFLICT(epoch) DO NOTHING`, then read back and compare. A different
   stored value for the same epoch is *by definition* a rewritten history: halt
   all writes, refuse, escalate. Never repair silently.
7. **Tail honesty.** Events written after the last sealed epoch are durable and
   per-shard-verified but **not globally anchored**. The verifier reports
   `sealed_upto_epoch` and `unanchored_tail_events` explicitly. It never reports
   "verified" for an unanchored range.

### 4.3 Fail-closed under C3

The mandatory-evidence gate (`_crosscutting.py:850-878`) is untouched in
meaning: an event is evidence when its **shard transaction commits**. The
caller's `log_event()` still blocks on that commit. If the shard is down, the
write fails, the action is denied — exactly as today.

The anchor is a **bounded-lag secondary commitment**, not part of the
acknowledge path. Therefore:

* default: acknowledge on shard commit (latency unchanged, fail-closed
  unchanged);
* optional `require_anchor_seal=True` for sovereignty-critical actions: the
  action waits until its epoch is sealed (adds up to `epoch_seconds`) and is
  denied if the epoch cannot close. This is a *tightening*, offered, not
  imposed;
* `audit_anchor_lag_epochs` is a first-class metric. A stalled epoch is an
  **observable degradation**, never a silent one.

### 4.4 Verification of a sharded chain — one boolean, one hash

```
verify_global(shards, upto_epoch):
  1. for each shard s (parallel):
       link chain from genesis to tail; seq contiguous; content hashes recompute;
       every checkpoint_s(E) equals the shard's own state at the E boundary
  2. for each epoch 0..upto:
       anchor_hash recomputes from (prev_anchor_hash, members)
       prev_anchor_hash == global_anchor[E-1].anchor_hash
       members == exactly the S shards (no extra, none missing)
       epochs increase by exactly 1 (no gap, no reuse)
  3. completeness:  Σ_shards count(events with epoch==E)  ==  Σ event_count in anchor E
  4. tail: report sealed_upto_epoch and unanchored_tail_events
  ⇒ integrity_ok  ∧  head = global_anchor[upto].anchor_hash
```

`head` is a single 32-byte commitment to the entire ordered content of every
shard up to `upto_epoch`. **That is the object you hand to an external
timestamp authority (HD-05) or a notary** — one signature per epoch instead of
one per event. C3 does not just preserve verifiability, it makes a capability
possible that C1/C2 cannot afford.

### 4.5 Fencing, crash recovery, and failover in C3

* **Single-writer property** — preserved *per shard*, by the *same*
  `SqliteWriterLease` primitive, unmodified. C3 does not invent a new fencing
  scheme; it runs N instances of the one that is already proven. Since a shard
  is a file, "one writer per shard" is exactly today's guarantee, minus the
  contention. The anchor DB gets its own lease and its own single writer.
* **Crash recovery** — a shard recovers exactly as today: read `chain_state`,
  continue from `last_seq + 1`. Deterministic, no replay, no ambiguity.
* **Deterministic recovery of the anchor** — read `MAX(epoch)` from
  `global_anchor`; resume at `epoch + 1`. Refuse to start if `MAX(epoch)` is
  greater than the highest fully-sealed shard epoch (a torn seal) — that is a
  halt-and-escalate condition, not a "skip it" condition.
* **Failover** — a shard has no hot standby (one writer, by construction).
  Failover = restart on the same file, or promote a replica **only after the
  dead writer's lease is provably expired or its process provably dead** — the
  existing Fix-A logic (`fencing.py:296-302`, `:349-354`), unmodified.
* **Zero duplicate seq / zero broken joins / zero lost committed events** —
  within a shard these are today's properties, on today's code, with today's
  tests. Globally: duplicate `seq` is impossible across shards because shards
  are distinct files (identity is `(shard_id, seq)`); a broken join is detected
  per shard; a lost committed event is detected by the completeness check
  (§4.4 step 3).

### 4.6 The one property C3 weakens — stated explicitly

**Weakened:** *global total order*. Under C1/C2 every event has a unique global
`seq` and the log has one total order. Under C3 the log has **per-shard total
order plus a global order at epoch granularity**. Events in different shards
within the same epoch have no defined relative position.

**Compensation (all four, required together):**

1. **The window is bounded and declared.** `epoch_seconds ≤ 1 s` by default.
   The ambiguity never exceeds one epoch and is reported as such.
2. **Causal order is preserved where it matters.** Shard key =
   `hash(correlation_id)`, so every event in one unit of work is in one shard
   and keeps an exact total order (§4.2.1).
3. **A deterministic derived order always exists.** Materialise
   `(timestamp, shard_id, seq)` into the derived read projection; every auditor
   replaying it gets byte-identical output. The order is *derived*, never
   authoritative.
4. **What is actually lost is smaller than it sounds.** Today's "global order"
   is *arrival order at one write lock* — an arbitrary serialisation that
   already carries no causal information between unrelated events. C3 loses an
   arbitrary tie-break, not causality.

If the owner's legal/governance judgment is that a single global total order is
a hard evidentiary requirement, **C3 must not be deployed** and the ceiling
stays at C2 (~5×10⁴ eps). That is a genuine human decision (§7 / HUMAN
DECISIONS), not an engineering detail, and this ADR does not decide it.

### 4.7 C3 — what breaks if it is wrong

| failure | consequence | detection | fail-closed behaviour |
|---|---|---|---|
| a shard goes down | its epoch never seals; `audit_anchor_lag_epochs` grows | lag metric + per-shard liveness | epoch stays open; `require_anchor_seal` actions denied; normal actions to *other* shards unaffected (blast radius 1/S, better than today's whole-store outage) |
| anchor writer crashes mid-seal | epoch half-written | torn-seal check on restart (§4.5) | refuse to start; escalate |
| two different anchor values for one epoch | history rewritten | read-back compare on seal | halt all writes; escalate; never repair |
| an event is re-routed after a failed first attempt | duplicate in two shards | `(event_id, shard_id)` uniqueness + routing carried in the event | forbidden by construction (§4.2.2) |
| shard key changed without a rebalance epoch | the same event maps to two shards | `shard_epoch` mismatch on route | router refuses the event (fail-closed) |
| one shard's file is corrupted/lost | that shard's history is unavailable | per-shard verify fails | **global verify fails** — the log set as a whole is reported broken, never partially green |
| correlated load skew (a hot `correlation_id`) | one shard hot, others idle | per-shard queue depth | backpressure, never drop |

**Rollback for C3:** run C3 at **S=1**. With one shard, C3 reduces exactly to
C2 plus an anchor chain. Rollout is therefore `S: 1 → 2 → 4 → …`, each step an
epoch boundary, and rollback is `S → 1` by (a) sealing a final epoch, (b)
replaying all shards into one file in the deterministic derived order
`(epoch, shard_id, seq)`, (c) recomputing `link_hash` over the merged chain and
comparing its tail against `global_anchor[final].anchor_hash`. A mismatch means
the merge is wrong and the merge is refused — it never silently produces a
chain that merely looks fine.

---

## 5. Decision table

Legend: ✅ preserved / good, ⚠️ weakened but compensated and declared, ❌ lost.

| Candidate | Correctness under concurrency | Ordering guarantee | Crash recovery | Failover / fencing | Operational complexity | Migration cost | Rollback cost | NEW failure modes introduced |
|---|---|---|---|---|---|---|---|---|
| **C0** status quo, 1 txn/event | ✅ (Option B: atomic RMW + lease) | ✅ global total order | ✅ deterministic (`chain_state`) | ✅ lease, but 30 s TTL + pid-liveness hazard | low | — | — | none (it is today) |
| **C1** batched append (current) | ✅ (proven by `test_batch_append.py`) | ✅ global total order | ✅ no partial batch | ✅ lease per batch | low | none (already shipped) | trivial | batched latency; `event_id` 48-bit collision silently drops evidence |
| **C2** group commit + pre-hash + `link_hash` + index move + FULL + UNIQUE | ✅ unchanged — one chain, one writer | ✅ global total order | ✅ unchanged + O(new) verify | ✅ unchanged | **medium** (a writer thread/service, a queue, a derived projection) | **low** — additive columns + config; no row rewrite except `link_hash` backfill | **low** — flags + additive schema | queue backpressure; group-window latency; a stale `verified_upto_seq` would be a false green (guarded) |
| **C2′** WAL-backed queue + append service, no sharding | ✅ | ✅ | ✅ | ✅ | medium | low | low | same as C2, but **caps at C2 throughput** — no 100× path |
| **C3** sharded chains + epoch anchor | ✅ per-shard; globally complete via anchor | ⚠️ **epoch-granular global + per-shard total + correlation-affinity** (§4.6) | ✅ per-shard deterministic; anchor resumes at MAX(epoch)+1 | ✅ same lease, per shard; proven primitive reused | **high** — S files + anchor writer + router + rebalance | **high** — new topology, shadow-run required | **medium** — S→1 by deterministic merge | epoch stall; torn seal; anchor fork; routing drift; shard loss ⇒ whole-set verify fails; no atomic multi-file snapshot |
| **D** shard-per-writer, no anchor | ❌ | ❌ no global order, S genesis points | ❌ per-shard only | ⚠️ per shard | medium | medium | medium | **inadmissible** — fragments the single evidence chain (already rejected in the prior ADR, Option D) |
| **E** move the chain to Postgres / an external log | ⚠️ depends entirely on the new store's isolation semantics | ⚠️ must be re-derived | ⚠️ new semantics | ⚠️ new mechanism (advisory locks / leader election) | **very high** | **very high** | very high | new failure domain, new durability semantics, a second source of truth during migration. **Not now** — but it is the honest answer past ~16 shards on one box, and §10 names it as the 1000× direction |

**Recommendation:** ship **C2** now (10×, no weakened property, cheap rollback);
design **C3** and hold its deployment behind the §4.6 human decision; keep
**E** on the roadmap as the 1000× tier, not as a C3 substitute.

---

## 6. Migration path: C0/C1 → C2 → C3 (and how you prove it)

Every step is non-destructive in the I1–I10 sense already established by
`src/kernels/audit/recovery.py`: **no DELETE, no UPDATE of existing
`audit_events` rows, no second genesis.** Every step starts from a
transaction-consistent copy (`VACUUM INTO` — never `cp` of a WAL-mode file,
U14/R11) that is hashed and recorded in a manifest before anything is touched.

### Step 0 — Freeze the starting state
`VACUUM INTO` the live store to `pre_c2_<ts>.db`; record `sha256(file)`,
`COUNT(*)`, `MAX(seq)`, `SUM(LENGTH(event_hash))`, and the schema version in a
manifest. The copy is immutable for the duration.

### Step 1 — Pre-flight (mandatory, and it can fail)
Run `recovery.detect()` on the copy. The production DB is **forked** (169
duplicate seq). The fork's disposition is an already-open **HUMAN DECISION**
(EXECUTION-QUEUE §Next autonomous work) and this ADR does not pre-empt it. No
C2/C3 migration runs against a forked DB.

### Step 2 — C2 schema migration (additive)
```
ALTER TABLE audit_events ADD COLUMN link_hash TEXT;      -- nullable
ALTER TABLE audit_events ADD COLUMN canonical_version INTEGER;  -- optional, for §8
-- backfill, deterministic, in seq order:
--   link_hash_1 = H("genesis" || event_hash_1)
--   link_hash_i = H(link_hash_{i-1} || event_hash_i)
-- widen event_id generation to uuid4().hex (128 bits) — write-path only,
--   existing ids untouched
```

### Step 3 — Proof that the converted chain still verifies
Five independent checks; **all five must pass or the migration is discarded**:

1. **Old verifier still passes** — `verify_integrity()` (prev-hash joins,
   contiguous `seq`, recomputed content hashes, tail anchor) returns `True`
   with the same `total`. Nothing about the old chain changed.
2. **New verifier passes** — the `link_hash` chain recomputes from genesis to
   tail and the final `link_hash` equals `chain_state.last_link_hash`.
3. **Independent recompute** — a second, separately-written implementation (not
   a copy of the migration code) replays the chain and produces an identical
   `link_hash` column, compared row by row. Two implementations agreeing is
   evidence; one implementation agreeing with itself is not.
4. **No-row-changed proof** — `COUNT(*)`, `MAX(seq)`, `SUM(LENGTH(...))` over
   every pre-existing column, and `sha256` of a canonical dump of every
   pre-existing column, are identical before and after.
5. **Determinism proof** — re-run the migration on a fresh copy of the same
   input; the `link_hash` column must be byte-identical. A non-deterministic
   migration cannot be trusted and cannot be rolled back.

Only after all five: `CREATE UNIQUE INDEX ... ON audit_events(seq)`. On a forked
DB this statement **fails**, and that failure is the intended fail-closed signal
— the DB is not ready. It is never worked around.

### Step 4 — C2 rollout
Ship behind flags: `group_commit` (off ⇒ old path), `prehash_outside_txn`,
`synchronous=FULL`, `derived_projection`. Canary on a fresh DB; compare
throughput against the §2 table; compare `verify_integrity()` before and after
on identical event streams.

### Step 5 — C3 rollout (`S: 1 → N`)
1. Deploy C3 with **S=1** and run it in **shadow** against C2 for one full
   epoch: every event is appended to both; the two chains and the derived
   global view must agree exactly.
2. Raise S at an epoch boundary. Each raise is a rebalance epoch: the router
   switches the mapping at a sealed epoch, never mid-epoch.
3. After each raise, re-run §4.4 `verify_global` and assert
   `sealed_upto_epoch` advances and `unanchored_tail_events` returns to ~0.

### Step 6 — Continuous gate
`verify_global` (or `verify_integrity` on C2) runs on a schedule off a snapshot,
persists `verified_upto_*`, and feeds `audit_integrity_ok`. It never reports a
cached green for a range it did not actually verify.

---

## 7. What we refuse to do to gain throughput

Per the owner's ordering — **capability > architecture > correctness > security
> reliability > maintainability > scale** — the following are not available as
trade goods. Each names what it would buy and why it is refused.

1. **Refuse: per-process / per-agent audit databases without a cross-chain
   anchor.** Buys linear scaling trivially. Refused: it destroys the single
   evidence chain, which is the capability the whole subsystem exists to
   provide. (Prior ADR Option D.)
2. **Refuse: relaxing the mandatory-evidence gate for throughput.** No
   "audit best-effort" mode, no "skip evidence under load" for HIGH/CRITICAL.
   `_crosscutting.py:850-878` stays a hard deny.
3. **Refuse: dropping `prev_event_hash`, or any "eventually consistent" chain.**
   Ordering is the product.
4. **Refuse: `synchronous=OFF` (or `NORMAL` once C2-1 ships) on the
   authoritative store.** Power-loss silent loss of committed evidence is not a
   throughput trade, it is a lie about durability. Measured cost of refusing:
   14% (§2.2).
5. **Refuse: a visible partial batch.** All-or-nothing or nothing.
6. **Refuse: weaken fail-closed `hash_alg` verification.** An event declaring an
   algorithm this build cannot perform is *unverifiable*, never "probably
   sha256". (PHASE 3.6 / A5.)
7. **Refuse: delete or compact the authoritative chain to save storage.**
   Retention is a derived-projection + legal-hold concern (HD-06), never a
   DELETE against `audit_events`.
8. **Refuse: a second genesis.** History is frozen; new epochs anchor *above*
   it (`chain_anchor_v2` pattern, `recovery.py`).
9. **Refuse: silently re-routing an event to a different shard after a failed
   first attempt.** It defeats idempotency and duplicates evidence across
   chains.
10. **Refuse: reporting "verified" for a range that was not verified.** No
    cached green, no "verified up to yesterday therefore probably fine".
11. **Refuse: closing an epoch with a missing shard** (§4.2.5), even though
    doing so would keep the anchor pipeline flowing.
12. **Refuse: dropping WAL** — MEASURED 108× throughput loss (§2.4) and it is a
    durability property besides.

**Explicit weakening register (the complete list — there are exactly two):**

| # | property weakened | where | compensation |
|---|---|---|---|
| W1 | global total order → epoch-granular + per-shard + correlation affinity | C3 §4.6 | bounded ≤1 s window; correlation-affinity sharding; deterministic derived order; today's "global order" is itself only arrival order at one lock |
| W2 | query indexes removed from the authoritative store | C2 §3.4 | derived read projection preserves the query capability in full; losing it costs latency, never evidence |

Nothing else in C2/C3 gives anything up. If a future proposal needs a third
entry here, it is a new ADR and a human decision, not an implementation detail.

---

## 8. Interaction with the existing risk / decision register

* **U14** (WAL + `synchronous=NORMAL`, Windows power loss) — **closed by C2-5**,
  at a MEASURED 14% cost that only becomes payable because of C2-1. Note the
  helper that would do it today, `configure_audit_durability()` in
  `src/kernels/audit/durability.py`, has **zero production callers** — the
  capability is built and unwired.
* **U11** (no `UNIQUE(seq)`) — **closed by C2-5**, gated on the fork
  disposition.
* **U9** (benchmarking) — extended: `scripts/bench_audit_chain.py` should gain
  the batch×durability matrix from §2.1 and the shared-vs-sharded matrix from
  §2.3, so the ceiling claim is re-measurable rather than folklore.
* **HD-05** (timestamp authority) — C2-3/C3 make per-epoch external timestamping
  affordable (one `link_hash` head per epoch). Today it would cost one TSA call
  per event.
* **HD-06** (retention) — C3's per-shard files make *whole-shard* retirement
  feasible for a scope-sharded deployment; but see U52: crypto-shredding vs
  deletion is a legal decision, not an engineering one.
* **ROADMAP §Architecture 10× / 100× / 1000×** — this ADR supplies 10× (C2) and
  100× (C3); 1000× is named in §10 and is out of scope here.

---

## 9. Findings about the CURRENT implementation (design-relevant, not cosmetic)

Observed while researching this ADR. Line numbers are as of 2026-09-26 ~12:55;
`src/kernels/audit/__init__.py` is **under concurrent edit by another teammate**
(a retry/`_reopen` path for `SQLITE_READONLY` was being added during this work,
and the file was transiently unparseable at 12:47), so line numbers will drift —
the function names are the stable reference.

1. **`event_id` is 48 bits.** `str(uuid.uuid4())[:12]` at
   `_attempt_log_event` (~L530) and `_attempt_log_event_batch` (~L675). ≈18%
   collision probability at 10⁷ events, ≈18 expected collisions at 10⁸. In the
   batch path a collision is **silently counted as a duplicate and dropped** —
   real evidence lost, no error. In the single path it raises `IntegrityError` →
   fail-closed denial. Either way it is a defect at million scale.
2. **`verify_integrity()` is a full `fetchall()` under the append lock.**
   `_verify_integrity_locked` (~L758-763) loads the entire chain and builds an
   `AuditEvent` per row while holding the lock that `log_event()` needs.
   MEASURED 26.4 µs/row ⇒ ESTIMATE 264 s of total append outage at 10M rows,
   i.e. a >4-minute denial of every HIGH/CRITICAL governed action, plus ~5.4 GB
   RSS. Reachable in production via `refresh_audit_metrics(with_integrity_check=True)`.
3. **`synchronous=NORMAL` is hardcoded** (`_init_db`, ~L271) while
   `durability.configure_audit_durability()` — the FULL-grade helper — has no
   production caller at all (only `tests/kernels/audit/test_durability.py`).
   The store is not as durable as the codebase implies.
4. **No `UNIQUE(seq)`.** The schema (L250-345) has no DB-level guard against the
   HC-01 fork class; correctness rests entirely on transaction discipline. U11
   decided this; it is still absent.
5. **`get_event()` (~L966) and `get_stats()` (~L993) execute on the shared
   connection without `self._lock`**, unlike `query_events`, `verify_integrity`
   and both append paths. The connection is opened with `check_same_thread=False`
   specifically because it is shared across a thread pool, so this is an
   inconsistency, not a considered exemption.
6. **`event_hash` does not cover `prev_event_hash`** (deliberate — see
   `src/common/hash_chain.py`'s note that canonicalization is not unified). The
   consequence is that no single value commits to the ordered prefix: no cheap
   notarization, no inclusion proofs, and truncation defence resting solely on
   the `chain_state` row in the same file. C2-3 fixes this additively.
7. **Lease liveness is pid-based, TTL 30 s** (`fencing.py:296-302`, `:349-354`).
   If a crashed writer's pid is reused by a new process on Windows,
   `_is_process_alive` returns `True` and every other writer is refused for up
   to the full TTL — which the fail-closed gate converts into a denial of all
   HIGH/CRITICAL actions for up to 30 s after a crash. A lease needs an epoch or
   a heartbeat, not just a pid.
8. **`_init_db` performs `ALTER TABLE … ADD COLUMN` migrations on every open**
   (~L291-310) with no cross-process lock. Concurrent schema mutation racing an
   append is a real window; the anchor-seed race was just fixed with
   `INSERT OR IGNORE` (observed 1/12 rounds at 6 processes) but the `ALTER` path
   is still unguarded.
9. **Batch dedup is O(batch) inside the write transaction**, chunked 500 ids per
   `IN (...)` query (~L660-668). Harmless at 250; at 5,000-event batches it is
   10 extra queries plus 5,000 bound parameters inside the critical section.
10. **`timestamp = time.time()` is inside the hash** (`compute_hash`). The chain
    therefore commits to a wall-clock value that nothing external attests, and a
    backwards NTP step produces a chain whose `seq` order and `timestamp` order
    disagree while the chain still "verifies". HD-05 is the compensation and it
    is not wired to the chain head.

---

## 10. The 1000× tier (named, not designed)

C3 exhausts one machine: MEASURED 3.80× at 4 shards on 24 cores, so the
practical single-box ceiling is ~16 shards ≈ 3×10⁵ eps (ESTIMATE). 1000×
(≈2×10⁶ eps) requires **federating shard-sets across nodes**, where the
`global_anchor` construction is applied a second time — each node publishes a
node-checkpoint per epoch, and a **federation anchor** binds node-checkpoints
exactly as the epoch anchor binds shard-checkpoints. The construction is
recursive and the properties carry over unchanged; the new costs are a
cross-node consensus/lease for the federation anchor and a network failure
domain. That is a separate ADR, gated on C3 existing.

---

## 11. Decisions reserved to the human

1. **Is epoch-granular global order acceptable as evidence?** (§4.6) If not, C3
   is not deployable and the ceiling is C2. This is a governance/legal
   judgment about what the audit trail must mean, not an engineering choice.
2. **Retention vs. an append-only chain** (HD-06 / U52): deletion is impossible
   without breaking the chain; the realistic options are whole-shard retirement,
   crypto-shredding of `details`, or "keep everything". Each changes what the
   evidence can later prove.
3. **Shard key policy** (§4.2.1): `correlation_id` (preserves causal order) vs
   tenant/scope (preserves legal-hold locality). Not both.
4. **External timestamp authority provider** (HD-05) — already open; this ADR
   makes it affordable but does not choose it.

## 12. Human-sovereign input (isolated, non-blocking)

Engineering can proceed with **C2** without any human decision: it weakens
nothing (§7) and rolls back by configuration. **C3 deployment** waits on
decision 1 above; C3 *design* and shadow-running at S=1 do not.
