# Audit Subsystem Identifier Policy

**Status:** DRAFT (policy document — no source changes; `src/` unmodified)
**Scope:** Identifiers emitted by / accepted by the audit kernel
(`src/kernels/audit/`) — `event_id`, `chain_state` id, checkpoint id,
writer-lease `token` / `owner`, `writer_id` (proposed), `correlation_id`.
**Labelling discipline:** Every claim below is marked **MEASURED** (observed in
code / tests), **ESTIMATE** (computed from first principles), or **DESIGN**
(only a proposal, not yet implemented). See `ADR-audit-storage-generation-2.md`.

---

## 1. Anchor decision (the one fixed point)

> **`event_id` is a full-width 128-bit `uuid4`, rendered as 32 lower-case hex
> characters, system-generated.** This is enforced by a test, not by prose:
>
> `tests/kernels/audit/test_scale_defects.py::test_generated_event_ids_are_full_width_and_unique`
> asserts `len(eid) == 32` for every generated id **and** `len(set(ids)) == len(ids)`
> (uniqueness). **MEASURED** from source: `event_id = uuid.uuid4().hex`
> (`src/kernels/audit/__init__.py:671`); batch path falls back to
> `uuid.uuid4().hex` when a supplied id is empty (`__init__.py:829`).

The test comment states the *why*: "48-bit ids would collide inside a
million-event chain." That is the birthday-bound argument this document makes
explicit for every identifier.

---

## 2. Identifier inventory

| Identifier | Stored as | Width | Generation | Caller-supplied? | Uniqueness guarantee | Risk |
|---|---|---|---|---|---|---|
| `event_id` | `audit_events.event_id TEXT PK` | 128 bit | `uuid4().hex` (system) | Yes (optional) | **YES** — uuid4 | none |
| `chain_state.id` | `chain_state.id INTEGER PK CHECK(id=1)` | singleton row | fixed `1` | No | N/A (single row) | none |
| checkpoint `id` | `verification_checkpoints.id INTEGER PK AUTOINCREMENT` | 63-bit int | DB autoincrement (system) | No | **YES** within a DB | none (local only) |
| writer-lease `token` | `writer_lease.token INTEGER` | monotonic int | `SqliteWriterLease` (system) | No | **YES within one DB** (monotonic) | see §6.4 |
| writer-lease `owner` | `writer_lease.owner TEXT` | string | `f"audit-store:{pid}"` (system) | No | **NO** — PID reuse / reboot / multi-host | **HIGH** (§6.4) |
| `writer_id` | (proposed) | 128 bit | (proposed uuid4 per process) | No | **NOT IMPLEMENTED** | **DESIGN-ONLY** (§6.5) |
| `correlation_id` | `audit_events.correlation_id TEXT NOT NULL` | free text | caller, else `uuid4().hex` | **Yes (primary)** | **NO** — free text | **MEDIUM** (§6.6) |

---

## 3. `event_id`

* **Format / width:** 32-char lower-case hex (`[0-9a-f]{32}`), i.e. the raw 128
  bits of a UUID4 with no dashes. **MEASURED** (`uuid.uuid4().hex`).
* **Generation method:** `uuid4()` — random (RFC 4122 variant 1, version 4).
  System-generated on the write path; a caller may pass one in but the kernel
  never *needs* to.
* **Uniqueness guarantee:** **YES.** UUID4 has 122 random bits (6 are fixed
  version/variant); the probability two draws collide is the birthday bound
  below. **MEASURED** unique across a 200-event sample in the test.
* **Caller-supplied rule — accept / reject:**
  * If omitted / `None` / empty → kernel assigns `uuid.uuid4().hex` (**MEASURED**
    `__init__.py:671,829`).
  * If supplied and **already present** → treated as an idempotent *duplicate*
    (skipped, consumes no `seq`), **not** rejected. This is retry-safe by design
    (`AuditBatchResult.duplicates`). **MEASURED** `__init__.py:835`.
  * If supplied and **malformed** (non-hex, wrong width) → the kernel currently
    stores it verbatim (the column is free `TEXT PRIMARY KEY`); it does **not**
    validate width. **Policy recommendation (not yet enforced):** reject
    caller-supplied `event_id` that is not exactly 32 hex chars, fail-closed, so
    a truncated id can never masquerade as a full-width one. Flagged as RISK-LOW
    in §6.1.
* **Birthday bound:** see §7, row "128-bit". At 1M / 10M / 100M events the
  collision probability is ≈ 1.5×10⁻²⁷ / 1.5×10⁻²⁵ / 1.5×10⁻²³ — effectively
  zero. This is the ceiling the audit chain is anchored to.

---

## 4. `chain_state` id

* **Format / width:** a single `INTEGER PRIMARY KEY` row, **fixed to `1`** by
  schema constraint `CHECK (id = 1)` (**MEASURED** `__init__.py:121`).
* **Generation method:** not generated — it is the singleton anchor row holding
  `last_seq` / `last_hash` so tail-truncation is detectable.
* **Uniqueness guarantee:** N/A — there is exactly one such row per database.
  No collision surface.
* **Caller-supplied rule:** never caller-supplied. Not an external identifier.
* **Risk:** none. It is internal state, identified by position, not by a value
  that can repeat.

---

## 5. Checkpoint id (`verification_checkpoints.id`)

* **Format / width:** `INTEGER PRIMARY KEY AUTOINCREMENT` — a 64-bit signed
  SQLite rowid, system-assigned, monotonically increasing per database.
  **MEASURED** `verification.py:56`.
* **Generation method:** database autoincrement, system-generated at checkpoint
  write time (`write_checkpoint`).
* **Uniqueness guarantee:** **YES, within one database.** A checkpoint is
  *derived evidence* — never trusted as source of truth; a disagreeing
  checkpoint is discarded and recomputed (**MEASURED** `verification.py` docstring,
  "the recomputation wins").
* **Caller-supplied rule:** never caller-supplied.
* **Risk:** none for integrity, *with the caveat that the id is local to one DB*.
  If checkpoints from two databases are ever merged, the `id` space overlaps
  (both start at 1). That is acceptable because checkpoints are never used as a
  global cross-DB key — only `(start_seq, end_seq)` segments matter. Flagged
  informational only.

---

## 6. Writer-lease identifiers (`token`, `owner`, `writer_id`)

The writer lease provides single-writer *fencing*: a process must hold the
**current** token to append; acquiring a newer lease invalidates every older
token, fencing a split-brain writer. **MEASURED** `fencing.py`.

### 6.1 `token` — fencing token

* **Format / width:** monotonic `INTEGER`, seeded at 1 per DB, `+1` on each
  acquire. **MEASURED** `fencing.py:187,303,356`.
* **Generation method:** `SqliteWriterLease` inside the append's write
  transaction (`acquire_within`). System-generated.
* **Uniqueness guarantee:** **YES within one database** — it is a per-row
  monotonic counter, never reused while the same DB lives.
* **Caller-supplied rule:** never caller-supplied; internal fence only.
* **Risk — medium (cross-DB / cross-reboot):** the *value* `token` carries **no
  epoch, host, or "who"**. Two different databases (or the same DB after a full
  reset) can both legitimately hold `token = 1`. Fencing therefore only means
  something *relative to a specific `writer_lease` row in a specific DB*. This is
  fine for the current single-DB design but becomes a hazard the moment lease
  state is ever compared or replicated across stores. Documented, not yet
  exploitable.

### 6.2 `owner` — who holds the lease

* **Format / width:** string `f"audit-store:{os.getpid()}"`. **MEASURED**
  `__init__.py:447-449`.
* **Generation method:** system-generated from the current process PID.
* **Uniqueness guarantee:** **NO.** A PID is reused across process restarts and
  across hosts; the same `owner` string can denote two *different* writer
  processes at different times. `fencing.py._owner_pid()` parses the PID back out
  to decide liveness (`GetExitCodeProcess` / `STILL_ACTIVE` on Windows), so PID
  reuse directly weakens the dead-owner takeover check: a recycled PID that
  happens to be alive can be mistaken for the original owner.
* **Caller-supplied rule:** never caller-supplied.
* **Risk — HIGH (flagged):** `owner` is the *only* identity currently bound to a
  lease, and it is not unique across reboot / host / PID reuse. This is the
  central threat catalogued in `ADR-audit-writer-lease-identity.md` (threats
  T1/T2/T6: PID reuse across reboot, across hosts, and across container
  reschedules). Until replaced by a globally-unique `writer_id`, the lease's
  "recognise the real writer" property rests on a non-unique key. **This is the
  top identifier risk in the subsystem.**

### 6.3 Accept / reject for lease ids

Internal only. `acquire_within` refuses a *different live owner* (split-brain
prevention) and fences over a *dead* owner. **MEASURED** `fencing.py:287-303`.
The refusal is keyed on `owner` string equality + PID liveness — see the §6.2
weakness.

### 6.4 `writer_id` (PROPOSED — NOT IMPLEMENTED)

* **Format / width (proposed):** 128-bit `uuid4` assigned **per writer process**,
  plus a `boot_gen` / `writer_epoch` to survive PID reuse. **DESIGN** —
  `ADR-audit-writer-lease-identity.md` only.
* **Generation method (proposed):** uuid4 at writer start; persisted as
  `writer_id` + monotonic `writer_epoch`; lease takeover bumps `writer_epoch`,
  not PID.
* **Uniqueness guarantee (proposed):** **YES** globally — uuid4 + epoch defeats
  PID reuse (T1/T2/T6) and host ambiguity (T2).
* **Current status:** **NOT in the code.** `grep` for `writer_id` /
  `writer_epoch` / `boot_gen` returns only the ADR prose and one
  `recovery.py:400` `epoch = epoch or uuid.uuid4().hex[:16]` (a *different*,
  recovery-local epoch, not the lease identity). The live lease identity is
  still the PID-based `owner` of §6.2.
* **Risk:** relying on `writer_id` for anything today is a **phantom
  dependency** — it does not exist. Any consumer assuming it is set will find
  `None`.

### 6.5 `correlation_id`

* **Format / width:** free-text `TEXT NOT NULL`. **MEASURED**
  `__init__.py:107`. Callers typically pass a UUID or a request id, but the
  schema enforces no format.
* **Generation method:** **caller-supplied is the primary path.** If a caller
  passes `None`, the kernel fills `uuid.uuid4().hex` (`__init__.py:627,834`) —
  but that is a *fallback*, not the contract. Many call sites pass their own
  string. **MEASURED** `log_event(correlation_id=...)`.
* **Uniqueness guarantee:** **NO.** Nothing prevents two unrelated events from
  sharing a `correlation_id`, and nothing prevents one logical operation from
  reusing another's id. It is an *index*, not a key. There is even a
  `UNIQUE`-free index `idx_correlation` (`__init__.py:119`) precisely because
  many events legitimately share one correlation id (one request → many audit
  lines).
* **Caller-supplied rule — accept / reject:**
  * Accepted verbatim, any non-`None` string, no validation, no width limit.
  * If `None` → system assigns `uuid.uuid4().hex`.
  * **No rejection** for collision or format — by design it is not unique.
* **Risk — MEDIUM (flagged):** because `correlation_id` is caller-controlled and
  non-unique, it **must never be used as a deduplication key, an idempotency
  key, or a primary key**. Using it as such would let a caller collapse distinct
  events, or let a retried request silently drop a new event. Its only safe use
  is *correlation-aware query* (`query_events(correlation_id=...)`), which the
  API already scopes correctly (**MEASURED** `__init__.py:1677`). Policy
  recommendation: document `correlation_id` as "query index only; never a key."

---

## 7. Birthday-collision probability bounds

Generic birthday bound: `p ≈ 1 − exp(−n² / (2·2^b)) ≈ n² / (2·2^b)` for small `p`,
where `n` = number of identifiers drawn and `b` = random width in bits.
**ESTIMATE** from first principles; the `n²/2` form is the standard birthday
approximation.

| Width `b` | Source / use | n = 1M (10⁶) | n = 10M (10⁷) | n = 100M (10⁸) |
|---|---|---|---|---|
| **128 bit** (uuid4, `event_id`) | **MEASURED** src | ≈ 1.5×10⁻²⁷ | ≈ 1.5×10⁻²⁵ | ≈ 1.5×10⁻²³ |
| 64 bit (hypothetical) | ESTIMATE | ≈ 2.7×10⁻⁸ | ≈ 2.7×10⁻⁶ | ≈ 2.7×10⁻⁴ |
| **48 bit** (the *old* `event_id` the test rejects) | ESTIMATE / **MEASURED** intent | ≈ 1.8×10⁻³ (~0.18%) | ≈ 1.8×10⁻¹ (~18%) | ≈ 1.0 (certain) |
| monotonic int (`token`, checkpoint `id`) | not birthday — sequential | N/A (unique by construction, per DB) | N/A | N/A |

**Reading:**
* The audit chain's `event_id` sits at 128 bits, so even at **100 million**
  events the collision probability is ~10⁻²³ — far below any operational
  threshold. This is why the test hard-requires 32-char ids.
* The 48-bit column is included *because the test explicitly rejects it*: at 10M
  events it would already collide ~18% of the time, and at 100M a collision is
  effectively certain. That is the failure mode the 128-bit move eliminates.
* Sequential ids (`token`, checkpoint `id`) have no birthday surface *within one
  DB*; their hazard is cross-DB/reset reuse (§6.1, §5), not random collision.

---

## 8. Risk register (identifiers lacking a uniqueness guarantee)

| # | Identifier | Uniqueness guarantee? | Severity | Why it matters | Remediation (policy, not yet code) |
|---|---|---|---|---|---|
| R1 | `owner` (`audit-store:<pid>`) | **NO** (PID reuse / reboot / host) | **HIGH** | Sole lease identity; PID reuse can mask a dead writer (T1/T2/T6) | Implement `writer_id` + `writer_epoch` from `ADR-audit-writer-lease-identity.md`; stop keying liveness on PID |
| R2 | `correlation_id` | **NO** (free text) | **MEDIUM** | Caller-supplied; unsafe as dedup/idempotency/primary key | Document as query-index-only; reject any code path that keys on it |
| R3 | `token` (no epoch/who) | per-DB only | **MEDIUM** | Same numeric token in two DBs/reboots is indistinguishable | Bind token to `writer_id`+`writer_epoch` once R1 lands |
| R4 | `writer_id` / `writer_epoch` / `boot_gen` | **NOT IMPLEMENTED** | **DESIGN-ONLY** | Phantom dependency: consumers may assume it exists | Implement before any multi-writer / multi-host deployment |
| R5 | `event_id` malformed caller input | N/A (stored verbatim) | **LOW** | Non-32-char caller id could weaken width guarantee | Reject caller-supplied `event_id` not matching `^[0-9a-f]{32}$`, fail-closed |
| R6 | checkpoint `id` cross-DB merge | per-DB only | **INFO** | Overlaps if checkpoints merged across stores | Never use checkpoint `id` as a cross-DB key (already the case) |

---

## 9. Acceptance rules — summary for implementers

1. **`event_id`** — MUST be a full 128-bit uuid4 (32 hex). System-generated;
   caller may supply but it MUST match width; duplicate is idempotent-skip, not
   error; malformed should be rejected (R5).
2. **`chain_state.id`** — fixed `1`; internal; no action.
3. **Checkpoint `id`** — autoincrement; internal; never a cross-DB key.
4. **Lease `token`** — monotonic; internal fence; MUST be interpreted only
   relative to its own DB's `writer_lease` row (R3).
5. **Lease `owner`** — replace PID-based identity with `writer_id`+epoch before
   multi-writer use (R1). Until then, treat the lease as single-host,
   single-process-safe only.
6. **`writer_id`** — implement per `ADR-audit-writer-lease-identity.md` before
   relying on it anywhere (R4).
7. **`correlation_id`** — free text, caller-supplied, **never a key** (R2).

---

## 10. Honesty notes

* This document describes the code **as it is** (`src/` read-only, unmodified).
  All "MEASURED" claims cite file:line; all "DESIGN" claims cite the ADR that
  proposes them. No identifier behaviour was assumed green that is not in the
  source.
* The single identifier that *lacks* a uniqueness guarantee and is *already in
  production use* as an identity is the lease `owner` (R1, HIGH). Everything
  else either has a guarantee (`event_id`, checkpoint `id`, `token` per-DB,
  `chain_state`) or is explicitly non-unique by design (`correlation_id`, R2,
  MEDIUM, query-only).
* `writer_id`/`writer_epoch` are **design-only** (R4): they are the intended fix
  for R1 but do not exist in `src/` yet. Flagging them as "implemented" would be
  a false green and is explicitly avoided.
