# ADR — Audit Storage C3: million-scale sharded chains, cold tier, and an honest cross-shard evidence claim

**Status:** **DESIGN (not implemented by the author).** No code in `src/` or
`tests/` was changed to produce this document; it is research + design only.
**Date:** 2026-09-27
**Author:** general-purpose-10 (autonomous, team `p08-d19-d20-d21`), branch `p36`
**Supersedes / extends:** `ADR-audit-storage-generation-2.md` (C3 sketch in §4,
now specified); `ADR-audit-writer-lease-identity.md` (writer-lease identity,
kept unchanged); `ADR-audit-single-writer-lease.md` (single-writer lease, kept).
**Relates to:** HC-01 fork (284 broken joins / 169 duplicate seq), CRIT-1C
mandatory-evidence gate (`src/kernels/_crosscutting.py:850-878`), HD-05
(timestamp authority), HD-06 (retention / `retention_cold`), HD-1 (concurrent-
epoch ordering is **not** acceptable as a full-order evidence claim —
UNRESOLVED, non-blocking), HD-2 (downgrading external claims is not acceptable —
UNRESOLVED, non-blocking), `src/kernels/audit/verification.py` (C2 segmented
verification, reused unchanged), `src/kernels/retention/archival.py`
(copy-only cold storage, reused unchanged), `src/kernels/audit/recovery.py`
(I1–I10 non-destructive migration discipline).

---

## 0. How to read the numbers and the honesty boundaries in this document

Every quantitative claim is labelled **MEASURED** (produced by a named probe in
the Gen2 ADR), **ESTIMATE** (derived from MEASURED numbers by stated
arithmetic), or **CITED** (from another document in this wave). No unverified
constants, no vendor figures.

Two human decisions are **open but non-blocking** and shape the honesty model
directly:

- **HD-1** — *concurrent-epoch ordering is NOT acceptable as a full-order
  evidence claim.* Until it is resolved, C3 must never present events in two
  different shards within the same epoch as having a defined relative order. The
  design reports per-shard total order + epoch-granular global binding and keeps
  those as **separate** claims.
- **HD-2** — *downgrading external claims is not acceptable.* C3 must not weaken
  any evidentiary claim the single-file store (C1/C2) already made. In practice
  this means: the C2 honesty model (`rooted_at_genesis` vs `segment_verified`
  reported separately, checkpoints as derived evidence never a trust root) is
  **preserved and composed**, not replaced.

The evidence ceiling this ADR inherits from C2: an anchored **segment** proves
coverage over a **window**, not the entire history from genesis. A claim of
"verified from genesis" requires `rooted_at_genesis = True` and is reported
separately from "this tail re-verifies". **This ADR adds no new trust root.**

---

## 1. Problem: sustained 100M+ events

Hard numbers from the Gen2 ADR (the baseline the roadmap already paid for):

| path | throughput | label |
|---|---|---|
| `log_event()` single event | ~2,000 eps | CITED / **MEASURED 2,916 eps** (probe 1, batch=1, WAL, `NORMAL`) |
| `log_event_batch(250)` | ~21,000 eps | CITED / **MEASURED 21,117 eps** (probe 1, batch=250) |
| one chain, 4 shards | — | **MEASURED 81,218 eps** (probe 2, 3.80× at 4) |
| one chain, 8–16 shards (single box) | 160k–320k eps | ESTIMATE (linear extrapolation of the 3.80× at 4) |
| beyond ~16 shards on one box | — | the next increment is **machines**, not cores (Gen2 §10) |

The single-machine ceiling for C3 is therefore ~3×10⁵ eps (ESTIMATE: 16 shards ×
the ~21k batched per-shard ceiling, near-linear as measured at 4). At that rate
**100M events = ~5–8 minutes of sustained write** — comfortably within the
target. The storage/verification problem is the harder half:

- Verification cost is linear in chain length: **MEASURED ~23–26 µs/event**
  (verification.py module docstring). A 100M-event single chain would take
  ~2,600 s (ESTIMATE: 100M × 25 µs) of full re-verify and ~54 GB RAM at the
  measured 541.7 B/row — both unacceptable, and the reason C2 introduced
  segmented verification.
- C2's segmented verification bounds the **cost** of verification to O(new
  events), but it does **not** bound the storage: a single growing file still
  holds everything.

**C3's job is to bound both the write path (parallel shards) and the storage +
verification cost (shard-local verification + a cold tier that removes old data
from the hot path) — without inventing a new trust root and without weakening
C2's honesty model.**

---

## 2. Sharding / partitioning strategy

### 2.1 Two-tier scheme (the core decision)

| tier | what | key | parallelism |
|---|---|---|---|
| **Hot** | `S` active shard files, each a single SQLite DB | `shard = hash(correlation_id) mod S` (default) | `S` independent single-writers |
| **Cold** | sealed, read-only shard files moved to `retention_cold` | time/epoch closure of a shard range | no writer; verify-on-read |

#### Hot tier — shard key = `hash(correlation_id) mod S`
This is the Gen2 §4.2.1 choice, adopted and kept. The decisive reasons:

1. **Causal order inside a unit of work is preserved exactly.** Every event
   sharing a `correlation_id` lands in the same shard, so a workflow's events
   keep a total order.
2. **Idempotency stays exact.** The same `event_id` always maps to the same
   shard, so the existing "already-committed `event_id` ⇒ skip" rule is
   undefeated by re-routing.
3. **Cross-shard correlation queries are cheap, not expensive.** A correlation
   walk needs exactly **one** shard (deterministic), not a scatter-gather across
   all `S`.

A **per-tenant / per-scope** mode exists for legal-hold locality (HD-06), at the
documented cost: cross-correlation ordering is lost and a hot tenant can skew a
shard (Gen2 §4.2.1, §4.6). The two modes are mutually exclusive and selected at
a rebalance epoch, never mid-epoch.

#### Cold tier — time/epoch closure, not a second key
A shard is **sealed** (no more appends) once its events are older than the
retention hot-window **and** fully anchored + verified. A sealed shard becomes a
candidate for `retention_cold` archival (`archival.py` — copy-only, never
mutates the original). The cold tier is therefore addressed by *epoch closure of
a shard's append range*, not by re-partitioning on a time key. This keeps the
shard as the unit of both write-parallelism and cold lifecycle, so the two tiers
share one identity scheme (§3).

### 2.2 How the cumulative link + checkpoints compose ACROSS shards WITHOUT a new trust root

The C2 honesty rule (verification.py docstring) is inherited verbatim:

> A stored checkpoint is **derived evidence, never a source of truth**.

C3 applies it at two levels and introduces **no third level of trust**:

```
Level 1 (per shard, S parallel writers) — UNCHANGED from C2:
   each shard owns its own seq space, prev_event_hash links, link_hash,
   chain_state, writer_lease. verify_segment() works exactly as today.

Level 2 (global anchor, ONE writer, ~1/s) — DERIVED, recomputable:
   for epoch E:
     for each shard s:
       checkpoint_s(E) = H(prev_cp_s ‖ s ‖ E ‖ seq_hi ‖ link_hash_tail ‖ count)
   global_anchor(E) = H(global_anchor(E-1)
                        ‖ canon(sorted([(s, checkpoint_s(E)) for all s])))
```

**Why this is not a new trust root:** `global_anchor(E)` is a pure function of
the per-shard `checkpoint_s(E)` values, which are themselves pure functions of
the raw events in each shard (exactly the C2 checkpoint rule). Therefore the
entire cross-shard claim is **recomputable from the raw shard files with no
stored value trusted**:

- `recompute_checkpoint_s(E)` re-derives `checkpoint_s(E)` from shard `s`'s raw
  events over its epoch-E range;
- `recompute_anchor(E)` re-derives `global_anchor(E)` from the recomputed
  per-shard checkpoints plus `global_anchor(E-1)`.

The anchor DB stores these values for *efficiency* (a verifier need not replay
every epoch every time), but like C2 checkpoints it is **never trusted because
it is stored** — any disagreement between a stored anchor and a recomputation is
a DEFECT in derived state, and the recomputation wins. The root of trust remains
the per-event `link_hash` chains in the shards. **The anchor is an aggregator,
not an authority.**

The invariant that keeps it honest (Gen2 §4.2.5, carried forward): the anchor
writer **never closes an epoch with a missing member**. An idle shard seals an
*empty* checkpoint so no shard can block the epoch, but a **down** shard blocks
the epoch — deliberately — so it can never add epoch-E events retroactively and
undetectably. Closing an epoch with a missing member would create exactly the
unverifiable gap this ADR is forbidden from creating (HD-2: no downgrade of the
"everything is covered" claim).

---

## 3. Write-path scaling: single-writer-per-shard, lease extended unchanged

### 3.1 C3 keeps single-writer-per-shard

Per Gen2 §4.5, the single-writer property is preserved **per shard**, by the
**same `SqliteWriterLease` primitive**, unmodified. C3 does not invent a new
fencing scheme; it runs `S` instances of the one already proven. Since a shard
is a file, "one writer per shard" is exactly today's guarantee minus the
contention. The anchor DB gets its own lease and its own single writer.

### 3.2 Extending the writer-lease identity model to shards (MUST keep ADR-audit-writer-lease-identity.md)

The lease-identity model from `ADR-audit-writer-lease-identity.md` is **preserved
without change**:

- `writer_id` (`uuid4().hex`, per process) — kills PID reuse, host restart,
  cross-host collision (T1/T2/T6).
- `boot_gen` (host-local monotonic uptime) — the only liveness signal needing no
  shared clock.
- `writer_epoch` — incremented **only on a forced takeover**; the first durable
  record that a fencing event occurred.
- `token` — monotonic fencing token, **never decreases**.
- **The lease row is never deleted.** `FencedWriterError` on a superseded token.

**Extension rule (additive, not a rewrite):** each shard file carries its own
`writer_lease` row, evaluated by the *same* `acquire_within()` /
`release_within()` inside the shard's append transaction. The anchor DB has its
own `writer_lease` row for its own single writer. The identity model does not
care that there are now `S+1` lease rows instead of one — each is independently
fenced exactly as today. No new column, no new decision path, no new failure
mode in the fence itself.

### 3.3 Cross-shard `correlation_id` queries

- **Authoritative path (forensic):** `shard = hash(correlation_id) mod S` is
  deterministic, so a `correlation_id` walk opens exactly **one** shard and reads
  its authoritative chain. No scatter-gather, no ambiguity.
- **Derived read path (operational queries):** the C2-4 derived read projection
  is extended with `shard_id` and `(timestamp, shard_id, seq)` so the projection
  can answer "all events for correlation X across time" without hitting the
  shards. The projection is **non-authoritative and rebuildable** — losing it
  costs query latency, never evidence (Gen2 §3.4, §4.6.3). It is the only place
  a cross-shard query is answered; the authoritative answer always comes from the
  one deterministic shard.

---

## 4. Tiered / cold storage and the evidence claim it does (and doesn't) carry

### 4.1 Integration with `retention_cold`

`src/kernels/retention/archival.py` is **copy-only**: `archive()` writes a byte
payload to cold storage and MUST NOT remove or mutate the original. C3 uses it as
the cold tier unchanged. When a shard is **sealed** (§2.2), C3 writes:

1. a `VACUUM INTO` export of the sealed shard (transaction-consistent, never a
   `cp` of a WAL-mode file — U14/R11) to `retention_cold` under key
   `shard:<id>:epochs:<a>-<b>`;
2. an **archive manifest** (additive side table, I5 authority-provenance) recording:

```sql
CREATE TABLE IF NOT EXISTS shard_archive_manifest (
    manifest_id   TEXT PRIMARY KEY,           -- uuid4, the archive episode
    shard_id      INTEGER NOT NULL,
    epoch_lo       INTEGER NOT NULL,           -- first sealed epoch in this copy
    epoch_hi       INTEGER NOT NULL,           -- last sealed epoch in this copy
    shard_seq_lo   INTEGER NOT NULL,
    shard_seq_hi   INTEGER NOT NULL,
    shard_file_sha256 TEXT NOT NULL,           -- sha256 of the VACUUM INTO copy
    shard_tail_link_hash TEXT NOT NULL,        -- recomputed link_hash at seq_hi
    anchor_at_seal TEXT NOT NULL,             -- global_anchor(epoch_hi) head
    archived_at    REAL NOT NULL,
    archive_method TEXT NOT NULL,
    human_signature TEXT,                      -- see §6 (migration is human-signed)
    notes          TEXT
);
```

The manifest's `shard_tail_link_hash` and `anchor_at_seal` are **recorded at
seal time from a verified state** — they are the values a future verifier
compares against, not values that are themselves trusted.

### 4.2 What the evidence claim is, hot vs cold — stated separately (HD-2)

| data | claim that HOLDS | claim that does NOT hold (honesty) |
|---|---|---|
| **Hot, anchored+verified epoch** | `segment_verified` (tail re-verifies) **and** `rooted_at_genesis` per shard **and** `anchor_chain_verified` cross-shard **and** `completeness` (Σ counts match) | — |
| **Hot, unanchored tail** (after last sealed epoch) | `segment_verified` per shard (durable + per-shard tamper-evident) | NOT globally anchored; reported as `unanchored_tail_events`, never "verified" |
| **Cold, with manifest** | recompute shard `link_hash` genesis→tail; compare to `shard_tail_link_hash`; compare the resulting tail against `anchor_at_seal` by recomputing the anchor path | the manifest's recorded hashes are the comparison baseline — **if the cold file AND the manifest are swapped together, the forgery is undetectable from the shard alone** |
| **Cold, manifest trust** | protected by the **human-signed** migration record (§6) and, optionally, an external TSA stamp on `anchor_at_seal` (HD-05, not yet chosen) | without either, cold verification is "as strong as the manifest's integrity," not absolute |

**The honest statement for cold data:** a cold shard is verifiable to the *same*
standard as hot data **if the archive manifest is trusted**. The manifest's
integrity is what stands between "cold copy is genuine" and "cold copy + manifest
were both replaced." That gap is closed by (a) the human-signed migration record
(§6) and (b) an optional external timestamp on `anchor_at_seal` (HD-05). Until
HD-05 is chosen, the manifest's trust is the human sign-off, and C3 says so
rather than implying the cold copy is self-authenticating.

### 4.3 Verifying a cold shard was not tampered before / after archiving

Two independent checks, both required:

1. **At archive time (before cold copy leaves the hot path):** the sealed shard is
   `verify_segment(1, seq_hi)` (full, since it is bounded and sealed) →
   `rooted_at_genesis = True`; the recomputed `shard_tail_link_hash` is written
   to the manifest; `global_anchor(epoch_hi)` is recomputed from raw shards and
   written as `anchor_at_seal`. This is the *honest provenance* step — it records
   what was true, derived from raw events, not from a stored value.
2. **At read time (audit of a cold shard):** `sha256(cold_copy) ==
   manifest.shard_file_sha256` (byte-integrity of the copy); then
   `verify_segment(1, seq_hi)` on the cold copy; the recomputed tail link hash is
   compared to `manifest.shard_tail_link_hash`; and that tail is checked against
   `anchor_at_seal` by recomputing the anchor path from genesis. Any mismatch is
   reported as a DEFECT and escalates — never "probably fine."

This reuses `verify_segment` / `recompute_checkpoint` / `recompute_anchor`
unchanged; the cold tier adds only the manifest and the sha256 gate.

---

## 5. Verification scaling: per-shard, then an honest cross-shard statement

### 5.1 Per shard (reuses C2 primitives, unchanged)

- `verify_segment(conn, a, b, start_link_hash)` — recomputes every event a..b,
  reports `verified` AND `rooted_at_genesis` SEPARATELY (verification.py
  `SegmentResult`). Cost O(segment), **MEASURED ~23–26 µs/event**.
- `verify_rolling` / incremental — verifies only the new tail anchored on the
  last checkpoint; cost O(new events), not O(chain) (C2 §3.6).
- `verification_coverage(conn)` — returns `covered_through`, `uncovered_events`,
  `rooted_at_genesis`, `uncovered_ranges`, `oldest_verified_at` (verification.py).
  Per shard, this is computed independently and **in parallel** across the `S`
  shards.

### 5.2 Cross-shard coverage — one honest statement

```
verify_global(shards, upto_epoch):
  1. per shard s (PARALLEL):
       link chain genesis→tail; seq contiguous; content hashes recompute;
       every checkpoint_s(E) == recompute from shard's raw events
       report: segment_verified_s, rooted_at_genesis_s
  2. per epoch 0..upto:
       anchor_hash == recompute(global_anchor(E-1), members)
       members == exactly the S shards (no extra, none missing)
       epochs increase by exactly 1 (no gap, no reuse)
  3. completeness:  Σ_shards count(events with epoch==E) == Σ in anchor E
  4. tail: report sealed_upto_epoch, unanchored_tail_events
  ⇒ head = global_anchor[upto].anchor_hash  (single 32-byte commitment)
```

The verifier returns a **structured result, never a single boolean collapse**:

```python
@dataclass
class GlobalVerificationResult:
    per_shard: dict[int, SegmentResult]      # each carries its own rooted_at_genesis
    anchor_chain_verified: bool              # global_anchor recomputes genesis→upto
    completeness_ok: bool                    # Σ counts match per epoch
    sealed_upto_epoch: int
    unanchored_tail_events: int
    rooted_at_genesis_all: bool              # AND over all shards' rooted_at_genesis
    head: str                                # global_anchor[upto].anchor_hash
```

**The honest cross-shard statement** is the conjunction of the *separately
reported* components, and it is **never** summarized into "verified" unless every
component holds:

- `rooted_at_genesis_all AND anchor_chain_verified AND completeness_ok`
  ⇒ "the **entire** set is verified from genesis."
- otherwise ⇒ a report of **exactly which** component fails, e.g.
  "shard 3 has an unverified gap [seq 40001–45000] (rooted_at_genesis=False);
  remaining S−1 shards + anchor chain verified; completeness holds." No cached
  green, no silent downgrade (HD-2).

Because HD-1 is unresolved, `verify_global` **does not** assert a total order
across shards within an epoch. It asserts completeness + per-shard order +
epoch-level binding only. The ordering claim stays at "epoch-granular global +
per-shard total + correlation-affinity" (Gen2 §4.6), and that limitation is
printed in the result, not omitted.

### 5.3 Cost of verification at 100M events

With `S=16` shards, 100M events ≈ 6.25M/shard (ESTIMATE). Full per-shard verify
= 6.25M × 25 µs ≈ 156 s (ESTIMATE) **per shard, in parallel** ⇒ wall-clock
≈ 156 s for a full sweep, vs ~2,600 s for one 100M chain (ESTIMATE). Incremental
`verify_rolling` keeps steady-state cost at O(new events) per shard (C2 §3.6), so
it is bounded regardless of total volume. The cold tier removes sealed shards
from the hot verification set entirely — they are verified on read (§4.3), not on
every sweep.

---

## 6. Migration path: human-signed, non-destructive, reversible

Follows the HC-01 I1–I10 discipline already in `src/kernels/audit/recovery.py`
(no DELETE / no UPDATE of existing `audit_events` rows; no second genesis;
additive side tables; authority provenance; parameterized manifest) and the
HC-01 human-signature migration convention documented in `docs/autonomous/`.

### Step 0 — Freeze the starting state (immutable snapshot)
`VACUUM INTO` the live single-file store to `pre_c3_<ts>.db`; record
`sha256(file)`, `COUNT(*)`, `MAX(seq)`, `SUM(LENGTH(event_hash))`, schema
version in a manifest. The copy is immutable for the duration.

### Step 1 — Pre-flight (mandatory, can fail)
Run `recovery.detect()` on the copy. The production DB is **forked** (169
duplicate seq). As with C2, **no C3 migration runs against a forked DB** — the
fork disposition is the open HUMAN DECISION carried from Gen2 §6 Step 1; C3
refuses fail-closed on a forked source.

### Step 2 — Additive schema (offline backfill, never on the live tail)
For each of the `S` target shards:
```sql
ALTER TABLE audit_events ADD COLUMN link_hash TEXT;          -- nullable, backfilled
-- backfill on an OFFLINE VACUUM INTO copy (Gen2 §3.3 invariant: the backfill
-- is exclusive with appends for its whole duration, so a live backfill is a
-- declared write-stop; prefer offline cut-over):
--   link_hash_1 = H("genesis" || event_hash_1)
--   link_hash_i = H(link_hash_{i-1} || event_hash_i)
```
New columns only; existing rows untouched. `event_id` widened to `uuid4().hex`
(128-bit) on the write path, existing ids untouched (Gen2 §3.5).

### Step 3 — Split + seed (additive, recorded)
The single source chain is split across `S` shards by
`hash(correlation_id) mod S`, preserving each event's `seq` as `(shard_id, seq)`
— global `seq` becomes the composite identity so **duplicate `seq` across shards
is impossible** (distinct files). A `chain_anchor_v2`-style seed records the
split epoch. No original row is deleted or reordered (I1/I2).

### Step 4 — Human-signed cut-over manifest (the discipline this ADR adds)
Before the system is pointed at the shards, a **migration manifest** is produced
and **signed off by a human** (the HC-01 convention): it records the source file
hash, the `S` shard hashes, the shard-key policy, the seed epoch, and a
human signature / sign-off token. The system **refuses to serve C3** until a
valid signed manifest exists — this is the gate that makes the migration
reversible-and-accountable rather than silent. Migration is **not** complete and
**not** authoritative until the signature is present.

### Step 5 — Proof the converted set still verifies (all must pass or discard)
1. Old verifier still passes on each shard (prev-hash joins, contiguous `seq`,
   recomputed content hashes).
2. New `link_hash` chain recomputes genesis→tail per shard; final `link_hash`
   equals `chain_state.last_link_hash`.
3. Independent recompute (a second implementation) agrees row-by-row.
4. No-row-changed proof over pre-existing columns (count / max / sum / sha of
   canonical dump identical before and after the split).
5. Determinism proof — re-run the split on a fresh copy, byte-identical shard
   hashes.
6. **Cross-shard:** `verify_global` returns `rooted_at_genesis_all = True` and
   `completeness_ok = True`; `head` equals `global_anchor[final].anchor_hash`.

### Step 6 — Rollback (`S → 1`, deterministic merge, no data loss)
Run C3 at `S=1` ⇒ reduces exactly to C2 + an anchor chain. Full rollback to the
single-file store:
1. seal a final epoch;
2. replay **all** shards into one file in the deterministic derived order
   `(epoch, shard_id, seq)`;
3. recompute `link_hash` over the merged chain and compare its tail against
   `global_anchor[final].anchor_hash`. A mismatch means the merge is wrong and
   the merge is **refused** — never silently produces a chain that merely looks
   fine.

Because Step 0 froze an immutable copy and no source row was ever deleted,
rollback is a controlled replay, not a destructive restore. The signed manifest
(Step 4) is the audit record that the forward path was authorized.

### Step 7 — Continuous gate
`verify_global` runs on a schedule off snapshots, persists `verified_upto_*`, and
feeds `audit_integrity_ok`. It never reports a cached green for a range it did
not actually verify (Gen2 §6 Step 6, carried forward).

---

## 7. What C3 does NOT guarantee (explicit)

1. **No global total order across shards within an epoch (HD-1 unresolved).**
   Events in different shards in the same epoch have no defined relative
   position. C3 claims "epoch-granular global + per-shard total +
   correlation-affinity," never a full total order. If the owner's legal/governance
   judgment requires a single global total order, **C3 must not be deployed** and
   the ceiling stays at C2 (~5×10⁴ eps) — this is the Gen2 §4.6 human decision,
   not an engineering choice.
2. **No full-history proof beyond anchored windows.** A segment anchored on a
   checkpoint proves coverage over a **window**, not genesis. `rooted_at_genesis`
   is reported separately and, when `False`, no "verified" is claimed for that
   range (C2 honesty model, preserved).
3. **No new trust root.** The `global_anchor` is derived and recomputable from
   raw shard events; it is never trusted because it is stored. If every shard
   file is lost, the anchor alone proves nothing. The anchor is an aggregator,
   not an authority.
4. **Cold-data verification depends on the archive manifest's integrity.** If a
   cold copy and its manifest are swapped together, the forgery is undetectable
   from the shard alone. This gap is closed only by the **human-signed** migration
   record (§6) and an optional external TSA on `anchor_at_seal` (HD-05, not yet
   chosen). C3 does not pretend the cold copy is self-authenticating.
5. **No atomic multi-file snapshot across shards.** The epoch seal is the
   consistency boundary. Events written after the last sealed epoch are durable
   and per-shard-verified but **not yet globally anchored**; they are reported as
   `unanchored_tail_events`, never as "verified."
6. **A single lost/corrupted shard file ⇒ global verify fails.** `verify_global`
   is a conjunction; losing one shard reports the **whole set** as BROKEN, never
   partially green (R-G3-05). Reconstruction needs the cold copy + manifest.
7. **Cross-shard operational queries rely on the derived projection**
   (non-authoritative). The authoritative correlation walk goes to the one
   deterministic shard and reads its chain; the projection is a convenience that
   can be lost without losing evidence.
8. **C3 does not lower durability.** `synchronous=FULL` + `UNIQUE(seq)` per shard
   + the anchor writer's own lease. It does not invent a weaker store to gain
   throughput (Gen2 §7 refusals all carry forward).
9. **C3 does not downgrade external claims (HD-2).** It will not report a stronger
   property than it can prove, and it will not present the weakened global order
   as if it were C2's total order. The weakening (W1, Gen2 §7) remains a recorded
   human decision, not an implementation default.

---

## 8. Interaction with the existing register

- **HD-05** (timestamp authority) — C3 makes per-epoch external timestamping
  affordable: `global_anchor(E)` head is one 32-byte value per epoch to hand to a
  TSA, instead of one call per event. C3 records `anchor_at_seal` in the archive
  manifest so a future TSA stamp has a stable target. Provider still open.
- **HD-06** (retention) — the cold tier is the retention integration: sealed
  shards move to `retention_cold` copy-only. Deletion of the *original* remains
  forbidden (HD-06 safe default); only a copy is archived. Crypto-shredding vs
  deletion of `details` is still a legal decision, not an engineering one.
- **HD-1 / HD-2** — see §0 and §7. Both open, non-blocking; C3 is designed so
  that resolving them later (accept epoch-granular order / choose a TSA) requires
  no schema change, only a policy/provider config and the human sign-off already
  built into §6.
- **HC-01 fork** — C3 migration refuses a forked source (§6 Step 1), same as C2.
  The fork disposition remains the open human decision.
- **CRIT-1C mandatory-evidence gate** (`_crosscutting.py:850-878`) — untouched in
  meaning. An event is evidence when its **shard transaction commits**; the caller
  blocks on that commit. The anchor is a bounded-lag secondary commitment, never
  on the acknowledge path (Gen2 §4.3).

---

## 9. Decisions reserved to the human (isolated, non-blocking)

1. **Is epoch-granular global order acceptable as evidence?** (HD-1) If not, C3 is
   not deployable; ceiling stays at C2. Governance/legal judgment, not
   engineering.
2. **External timestamp authority provider** (HD-05) — C3 makes it affordable; it
   does not choose it.
3. **Shard key policy** — `correlation_id` (causal order) vs tenant/scope
   (legal-hold locality). Not both (Gen2 §4.2.1, §4.6).
4. **Final retention / deletion rule** (HD-06) — C3 archives copies; it does not
   delete originals. The final rule is sovereign.
5. **Migration sign-off authority** — §6 Step 4 requires a human signature before
   C3 serves traffic; who signs is a sovereign/operational call, not an
   engineering one.

## 10. Human-sovereign input (isolated, non-blocking)

Engineering can proceed with C3 *design*, shadow-running at `S=1`, and the
additive schema — none of that requires a human decision. **C3 deployment**
waits on decision 1 (HD-1); the **cold-tier trust** waits on decision 2 (HD-05)
and the human signature in §6 Step 4. C3 *design* honours HD-1/HD-2 by reporting
`rooted_at_genesis` and `segment_verified` separately and never collapsing them
into a single "verified."
