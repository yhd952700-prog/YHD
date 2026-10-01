# ADR — Audit writer lease identity: replacing PID with writer_id + writer_epoch

**Status:** **IMPLEMENTED (2026-09-27)** on branch `p36`. The identity model
below — `writer_id` (per-process uuid4) + `boot_gen` (host uptime) +
`writer_epoch` (takeover counter) replacing the PID decision, plus
`FencedWriterError` for explicit super-admin fencing — is now realised in
`src/kernels/audit/fencing.py` and wired through `src/kernels/audit/__init__.py`.
Default `LIUHAO_AUDIT_LEASE_IDENTITY=pid` is **unchanged** (the change is purely
additive and rolls back safely — ADR §6.5). Verification:
`tests/kernels/audit/test_lease_identity.py` (13 cases, all green) and the full
audit suite (159 passed, 0 failed). Design baseline was HEAD `008ae92e`; the
implementation delta is recorded in §10.
**Date:** 2026-09-27
**Author:** lease-epoch (autonomous, team p08-d19-d20-d21)
**Relates to:** U38 (self-fence), U39 (per-append leasing), HC-01 hash-chain
fork (284 broken joins / 169 duplicate seq), CRIT-1C mandatory-evidence gate,
`ADR-audit-single-writer-lease.md`, `ADR-audit-storage-generation-2.md` §4.5
(C3 reuses this primitive unmodified) and §9 item 7 (the finding this ADR
closes).

---

## 0. How to read the numbers in this document

Every quantitative claim is labelled **MEASURED** (with the command or the
artifact it came from) or **ESTIMATE** (with the arithmetic shown). Nothing is
quoted from memory. Where a number comes from another document rather than from
a measurement made for this ADR, it says so.

Measurements made *for this ADR* used throwaway databases in the OS temp
directory (`tempfile.mkdtemp`), WAL + `synchronous=FULL`, and never touched
`audit_store.db` or any evidence file.

---

## 1. What actually enforces "one writer" today

This matters, because it determines what the identity model has to do.

**MEASURED** — after a normal append, the lease row carries no live owner:

```
A: lease row after 1 append: (1, None, 1790533612.8645587, 0.0)
   #              columns ->  token=1, owner=NULL, acquired_at=<wall>, expires_at=0.0
```

(`AuditStore(db_path=<temp>)` → `initialize()` → `log_event(...)`, then
`SELECT token, owner, acquired_at, expires_at FROM writer_lease WHERE id=1`
on a second connection.)

That is the U39 contract working: `acquire_within()` takes the lease and
`release_within()` yields it **inside the same `BEGIN IMMEDIATE` transaction**
(`__init__.py:582-585` and `:652`, `:721-724` and `:799`), so a committed append
always leaves `owner = NULL, expires_at = 0`.

Consequences, stated because the rest of the design depends on them:

1. **The fork invariant is enforced by `BEGIN IMMEDIATE`, not by the lease.**
   Since U39, the lease row is only ever "live" *inside* an open write
   transaction, and only one process can hold that transaction. The PID
   liveness check in `acquire_within` (`fencing.py:349-354`) is therefore
   defence-in-depth against a **foreign live lease row**, not the thing that
   stops two writers from interleaving.
2. **The lease identity model's job is therefore narrow but real:** decide, when
   a foreign live lease row *is* present, whether to refuse or to take over —
   and refuse wrongly (denial) rather than take over wrongly (fork), always.
3. **Any future design that separates "hold the lease" from "check the lease at
   append time" re-opens HC-01.** If a writer holds a lease across appends and
   only re-checks it on renewal, then a takeover between renewals lets a writer
   that believes it still holds the lease keep appending. That is exactly the
   pre-U39 shape. See hard constraint H-1 (§5).

### 1.1 When is a foreign live lease row actually reachable?

Worth being precise, because it decides whether the threat model is theoretical:

* **Legacy rows.** Any deployment that ran the *pre-U39* code (lease held for
  the writer's whole process lifetime) and whose writer died without releasing
  has a `writer_lease` row with `expires_at` up to 30 s in the future. The first
  post-U39 process to start then hits the PID check. **This is live today for
  rolling restarts.**
* **The `acquire()` public API.** `SqliteWriterLease.acquire()`
  (`fencing.py:245-318`) commits a lease **standalone**, outside any append. It
  is the documented, exported way to take a lease. Today only
  `tests/kernels/audit/test_fencing.py` calls it, but "only tests call it" is
  not a guarantee for the next caller.
* **A database restored or copied from a snapshot taken while a lease was
  live** (backup restore, VM image, container volume re-attach).
* **A writer that opens a write transaction and then stops making progress**
  (paused VM, frozen container, a multi-minute batch): the transaction — and
  with it the lease — stays open, but peers block on `BEGIN IMMEDIATE` rather
  than reaching the lease check (they hit `SQLITE_BUSY` after
  `_SQLITE_BUSY_TIMEOUT_SEC = 30.0`, `__init__.py:40`).

---

## 2. Threat model

Two failure classes, and they are not symmetric:

* **(a) two writers appending simultaneously** — the HC-01 fork class
  (284 broken joins / 169 duplicate seq, reported in
  `ADR-audit-single-writer-lease.md` §1; **not re-measured here**). This is the
  failure the lease exists to prevent. It is unrecoverable: the evidence chain
  is silently untrue.
* **(b) zero writers able to append** — which the mandatory-evidence gate
  (`_crosscutting.py:850-878`) converts into `PolicyDeniedError`, i.e. a denial
  of every HIGH/CRITICAL governed action. Recoverable, expensive, but honest.

The design rule throughout: **when the two cannot both be avoided, choose (b).**

### T1 — PID reuse

A writer crashes holding a lease; the OS later assigns its PID to an unrelated
process. `_owner_pid()` parses the PID out of `owner`
(`fencing.py:29-43`), `_is_process_alive()` returns `True`, and
`if alive is not False: raise StaleWriterError` (`fencing.py:351`) refuses
everyone else for the remaining TTL.

* → **(b)**. **MEASURED**: with a lease row planted as
  `owner='audit-store:12172'` (a live, unrelated child process) and
  `expires_at = now + 30`:

```
B: append REFUSED after 0.0003s -> StaleWriterError: lease held by 'audit-store:12172' (alive=True) until 1790533659.75
```

* Windows makes no non-reuse guarantee for PIDs, so the collision is a matter of
  when, not whether. **ESTIMATE**: no measured collision rate is claimed here;
  the point is that a single collision costs up to the full 30 s TTL of total
  denial, and the denial is not attributable from the error message (the PID
  looks healthy).
* Also note the *inverse* hazard: if the new process that inherits the PID is
  itself an audit writer, `cur_owner == owner` matches and it **renews the dead
  writer's lease** (`fencing.py:342-348`) instead of taking over. No fence event
  is recorded, and the new writer silently inherits the old one's identity.

### T2 — Host restart / VM or container restart

PID namespaces reset on boot, so a stale lease row can name a PID that is
"alive" in the new boot. Same outcome as T1 → **(b)**, and it is *systematic*
rather than accidental: **every** restart of a container whose volume persists
the DB leaves a lease row that the new boot may misread as live.

There is currently nothing in the row that records *which boot* it came from.

### T3 — Clock skew

`expires_at` is a wall-clock float produced by `time.time()` **in the acquiring
process** (`fencing.py:335`, `:342-347`) and compared against `time.time()` in
**every other** process (`fencing.py:341`, `:394`). There is no skew budget.

* Acquirer's clock **ahead** → its `expires_at` is further in the future for
  everyone else → everyone else is refused for up to `TTL + skew` → **(b)**.
* Acquirer's clock **behind** → its lease looks expired to others → they take
  over while it still writes → **(a) in principle**. Under per-append leasing
  this is currently contained (the victim re-acquires a new token on its next
  append, inside `BEGIN IMMEDIATE`), but it produces a **silent** takeover: the
  victim cannot tell that it was fenced. That is defect T5, and it is the reason
  a future held-across-appends lease would turn this into a real fork.

### T4 — Process resurrection (paused / resumed, VM snapshot)

A writer paused mid-append holds the SQLite write lock and its lease; on resume
it continues with state that is arbitrarily stale. Today peers block on
`BEGIN IMMEDIATE` (30 s busy timeout) and then fail with `SQLITE_BUSY` → **(b)**.
Nothing in the lease row lets anyone distinguish "paused for 10 minutes" from
"healthy".

### T5 — No generation / epoch: a fenced writer cannot prove it was fenced

`token` is monotonic, but nothing records *who* held it or *that a takeover
happened*. A writer whose token has been superseded simply calls
`acquire_within()` again on its next append and receives a brand-new valid
token. It cannot distinguish:

* "nobody ever took my lease" from
* "someone took my lease, fenced me, and I am now appending over their tail".

→ **(a) latent**. Today `BEGIN IMMEDIATE` prevents the actual fork, so the
observable symptom is silence: a fencing event that leaves no trace. That is
unacceptable for an evidence store, where "who wrote seq N, and was anyone
fenced while it happened" is a forensic question.

It is also the reason C3 (`ADR-audit-storage-generation-2.md` §4.5: "promote a
replica **only after the dead writer's lease is provably expired or its process
provably dead**") cannot currently be *proven* — only assumed.

### T6 — Identity is not unique across hosts

`_lease_owner()` returns `f"audit-store:{os.getpid()}"` (`__init__.py:417-419`).
Two hosts sharing a database over a network filesystem can each have a live
process with the same PID, producing **identical owner strings** → the
`cur_owner == owner` renewal branch fires → both renew → both append →
**(a)**. (SQLite locking over NFS/CIFS is itself unreliable, so the real answer
there is the C2 dedicated-writer service, not a better lock. But the identity
model must not make it *worse* by colliding.)

---

## 3. Candidate identity models, compared

Costs are stated against the current append path: one `BEGIN IMMEDIATE`, one
lease `SELECT`, one lease `INSERT … ON CONFLICT`, N event `INSERT`s, one
`chain_state` upsert, one lease `UPDATE` (yield), one `COMMIT`
(`__init__.py:575-654`).

| # | Model | Binds identity to | Cost | What it fails to detect |
|---|---|---|---|---|
| 1 | **PID only** *(status quo)* | OS process id, parsed from `owner` | Zero extra writes | T1 PID reuse, T2 host restart, T6 cross-host collision, T5 no epoch. Costs a 30 s total denial when it is wrong (§2 T1, MEASURED). |
| 2 | **PID + boot id** | `(pid, host-boot-generation)` | One extra column; boot id read once per process, not per append | T1 *within* one boot (PID reuse before the next reboot), T5. Still no epoch, still no proof of fencing. Removes T2 and T6 only partially (two hosts can share a boot id source). |
| 3 | **Writer instance UUID + heartbeat** | `uuid4` per writer instance; liveness from heartbeat freshness | UUID: one extra column, generated once. Heartbeat: **one extra write transaction** — MEASURED −45 % at batch=1 if placed outside the append transaction (§4.7) | T5 (no epoch: a fenced writer just gets a new UUID and continues). Heartbeat alone also cannot distinguish "paused" from "dead" without a freshness gap rule, and a heartbeat that gates the append adds a new failure domain to the evidence path. |
| 4 | **Epoch / generation number in the lease row** | A monotonic count of *takeovers* | One extra integer column, written in the **same** `UPDATE` the lease already performs → MEASURED free (§4.7) | Nothing by itself — it is a *record*, not an identity. Useless without #3 (who) and useless for liveness without #2 (which boot). |
| 5 | **Fencing token from a monotonic source** | A token that only ever increases | Already present (`writer_lease.token`). A monotonic *source* (hardware counter, external sequencer) would add a dependency | The current token already satisfies "monotonic within this database". It does **not** survive a database restored from an older backup (token can move backwards), and it says nothing about *who* or *which boot*. |
| 6 | **`lease_version` incremented on every acquisition** | Version = token, renamed | Same as today | Same as #5. Under per-append leasing this grows by ~1 per append, so it cannot double as a "writer generation" signal — it is far too coarse-grained to mean "a takeover happened". |
| 7 | **External coordinator** (Redis/etcd/ZooKeeper lease) | A service that owns the lease | New subsystem, new failure domain, new network dependency, cross-process clock assumptions, and — fatally — **a second round trip that cannot be inside the SQLite transaction** | Violates hard constraint H-1 (§5). It also converts "coordinator down" into "all governed actions denied", which is strictly worse availability than today with no gain in fork-safety, since SQLite's write lock already serialises appends. **Rejected.** |

### 3.1 Why no single candidate is sufficient

* #1 is the status quo and fails four of the six threats.
* #2 fixes T2 at near-zero cost but not T1 (reuse happens far more often than
  reboot) and not T5.
* #3 fixes T1/T2/T6 (a UUID never collides across hosts, boots, or reuses) but
  not T5, and its heartbeat is the only candidate that can cost real throughput.
* #4 fixes T5 but is meaningless without #3.
* #5/#6 are already present as `token`; renaming or re-sourcing them adds
  nothing.
* #7 violates the atomicity constraint.

**The research therefore supports: #3 (writer instance UUID) + #4
(writer_epoch) + #2 (boot generation), keeping #5/#6 (`token`) exactly as they
are, with the heartbeat from #3 placed deliberately OFF the append path.**
That is the recommended design.

---

## 4. Recommended design

### 4.1 Identity

* **`writer_id`** — `uuid4().hex`, generated **once per process** and cached in
  a module-level global, shared by every `AuditStore` instance in that process.
  Per-process (not per-`AuditStore`) is deliberate: two `AuditStore` instances
  in one process must not fence each other (U38), and sharing one identity makes
  that fall out of the model instead of out of a special case.
  A `uuid4` cannot collide across hosts, boots, or PID reuse — it kills T1, T2,
  T6 in one move.
* **`boot_gen`** — a host-local **monotonic uptime** reading in milliseconds,
  captured once per process (`GetTickCount64()` on Windows;
  `CLOCK_BOOTTIME`-equivalent on POSIX). Every process *on the same host*
  agrees on it without any wall clock, which is what makes the reboot test in
  §4.4 possible.
  *Caveat to validate in tests:* `GetTickCount64` / `CLOCK_MONOTONIC` differ in
  whether suspend time advances them. The design never depends on the *value*,
  only on the two-sided comparison in §4.4, and every ambiguous comparison
  resolves to "refuse".
* **`writer_epoch`** — a monotonic integer, incremented **only on a forced
  takeover** (§4.4). Not per append. This is the number that answers "was anyone
  fenced, and how many times".
* **`token`** — unchanged. Still incremented on every acquisition, still the
  fencing token.
* **PID** — retained for diagnostics only (`writer_registry.pid`). It is
  **removed from every decision path**. This is the single most important change.

### 4.2 Schema

`writer_lease` — extended additively. Existing rows keep working; new columns
are `NULL`-tolerant and are **only** used as decision inputs when non-`NULL`.

```sql
CREATE TABLE IF NOT EXISTS writer_lease (
    id           INTEGER PRIMARY KEY CHECK (id = 1),
    token        INTEGER NOT NULL,          -- unchanged: monotonic fencing token
    owner        TEXT,                      -- unchanged FORMAT, DEPRECATED (see §6.3)
    acquired_at  REAL,                      -- unchanged
    expires_at   REAL,                      -- unchanged; 0 == released
    -- NEW, additive:
    writer_id    TEXT,                      -- uuid4 hex; NULL on pre-migration rows
    writer_epoch INTEGER NOT NULL DEFAULT 0,-- monotonic; +1 on forced takeover only
    boot_gen     INTEGER,                   -- host uptime-ms at acquire; NULL == unknown
    released_at  REAL                       -- explicit yield marker (replaces the
                                            --   expires_at = 0 sentinel)
);
```

`writer_registry` — new table, **never read by the append decision**:

```sql
CREATE TABLE IF NOT EXISTS writer_registry (
    writer_id         TEXT PRIMARY KEY,
    host              TEXT NOT NULL,        -- socket.gethostname()
    pid               INTEGER,              -- DIAGNOSTIC ONLY — never a decision input
    boot_gen          INTEGER,
    first_seen_at     REAL NOT NULL,
    last_heartbeat_at REAL NOT NULL,
    epoch             INTEGER NOT NULL DEFAULT 0,
    state             TEXT NOT NULL DEFAULT 'live'   -- 'live' | 'stopped'
);
```

The two-table split is the point: the fence (`writer_lease`) stays in the append
transaction and gains zero round trips; the liveness evidence
(`writer_registry`) is written by a background thread and **cannot** deny an
append, because nothing on the append path reads it.

### 4.3 Acquisition protocol

Still `acquire_within()` — no transaction control, called inside the append's
existing `BEGIN IMMEDIATE`. **One `SELECT`, one `UPDATE`/`INSERT`, zero extra
round trips**:

```python
def acquire_within(self, writer_id, boot_gen, ttl_sec=30.0, my_last_token=None):
    now = time.time()
    row = SELECT token, writer_id AS holder, writer_epoch, expires_at, boot_gen, released_at
            FROM writer_lease WHERE id = 1

    if row is None:
        return self._install(token=1, epoch=0, writer_id, boot_gen, now, ttl_sec)

    if row.holder == writer_id:
        # ---- same writer ------------------------------------------------
        if row.token > (my_last_token or -1):
            # Someone took MY token. I was fenced. Do NOT silently renew.
            raise FencedWriterError(
                fenced_epoch=row.writer_epoch, holder=row.holder, token=row.token)
        # Still mine (released or expired, nobody stole it) -> renew, no epoch bump.
        return self._renew(row.token, row.writer_epoch, writer_id, boot_gen, now, ttl_sec)

    # ---- different writer ---------------------------------------------
    if self._lease_is_live(row, now, boot_gen):
        raise StaleWriterError(
            f"lease held by writer {row.holder!r} (boot_gen={row.boot_gen}) "
            f"until {row.expires_at}")
    # Not live -> forced takeover: token +1 AND epoch +1.
    return self._install(token=row.token + 1, epoch=row.writer_epoch + 1,
                         writer_id, boot_gen, now, ttl_sec)
```

Decision table — every ambiguous cell resolves to **refuse**:

| `holder` vs me | `token` vs my last | Lease live? | Action | Epoch |
|---|---|---|---|---|
| — (no row) | — | — | install, `token=1` | 0 |
| same | equal | — | renew (U38 preserved) | unchanged |
| same | **higher** | — | **`FencedWriterError`** — fail closed | unchanged (report it) |
| different | — | yes | `StaleWriterError` — refuse (status quo) | unchanged |
| different | — | no | takeover, `token+1` | **+1** |
| different | — | **unknown** | refuse | unchanged |

### 4.4 The liveness test (`_lease_is_live`) — replaces `_is_process_alive(pid)`

```python
def _lease_is_live(row, now, my_boot_gen):
    # 1. Explicitly released / plainly expired (with a skew budget).
    if row.expires_at <= now - SKEW_BUDGET_SEC:      # SKEW_BUDGET_SEC = 5.0
        return False
    # 2. Boot generation: needs NO shared clock, only a host-local monotonic.
    if row.boot_gen is None or my_boot_gen is None:
        return True          # unknown -> REFUSE (never steal on ambiguity)
    if row.boot_gen > my_boot_gen + BOOT_TOL_MS:
        return True          # uptime went backwards or a FOREIGN host -> REFUSE (T6)
    if row.boot_gen < my_boot_gen - BOOT_TOL_MS:
        return False         # the host rebooted since -> stale, takeover allowed (T2)
    # 3. Same boot: fall back to the wall-clock TTL, budget included.
    return row.expires_at > now - SKEW_BUDGET_SEC
```

`BOOT_TOL_MS` absorbs uptime-source jitter; **ESTIMATE** 1 000 ms is ample
(uptime sources differ by well under a second between two processes on one host;
no measured value is claimed). Every branch that cannot *prove* staleness
returns `True`, i.e. refuses. This preserves the existing conservative rule
("do not steal an unconfirmed-orphan lease", `fencing.py:293-295`) and never
inverts it.

#### 4.4b — the pid-mode fallback: unparseable or unqueryable owner

The `uuid` test above replaces the *liveness* question in uuid mode. While the
default is still `pid`, the legacy path (`fencing.py:468-473`) collapses two
different unknowns into one refusal:

```python
pid = _owner_pid(cur_owner)
alive = _is_process_alive(pid) if pid is not None else None
if alive is not False:              # True AND None both refuse
    raise StaleWriterError(...)
```

* **(a) liveness unknown** — Windows `ERROR_ACCESS_DENIED` / POSIX `EPERM`
  → `None` → refuse. **Intended.** This is the fail-closed rule of §4.4; it is
  not a defect and must not be "fixed" into a takeover.
* **(b) owner does not parse to a pid** — `_owner_pid()` returns `None` → same
  branch → refuse. Correct for the same reason (a non-parseable owner is an
  unknown writer, and an unknown writer is never stolen from). **In the current
  #99 code this branch is unreachable**: `owner` is always `audit-store:<pid>`
  (§6.3, verified against the implementation — `_lease_owner()` is the only
  source, and every `writer_lease.owner` write point receives that pid-shaped
  string), so the only live source of `None` is `_is_process_alive` (case a).
  The unparseable-owner case is therefore a **regression guard**, not a present
  defect: it must be pinned so that any *future* change which breaks the owner
  format (or a test/ops row that writes a non-`prefix:int` owner) cannot
  silently turn into a self-lock. It must never be described as a current bug.

  **MEASURED** (throwaway DB, `LIUHAO_AUDIT_LEASE_IDENTITY=uuid` writer, then a
  `pid`-mode writer on the same file):

  ```
  after uuid-mode append: (token=1, owner=None, writer_id='eef1502c…', epoch=0,
                           boot_gen=33380390, expires_at=0.0)
  MIXED FLEET (pid reader after uuid writer): append OK
  ```

  So a mixed fleet does **not** deadlock: uuid mode leaves `owner = NULL` /
  `expires_at = 0` exactly as pid mode does.

  **MEASURED** for the adversarial case (a live row whose owner is
  `'writer_id:boot_gen:epoch'`):

  ```
  t=0.0s: refused -> StaleWriterError   # alive=None
  t=0.8s (after expires_at): OK         # self-heals at TTL
  ```

  The refusal is **bounded by `expires_at`**, not permanent — it self-heals the
  moment the row's TTL elapses. The only unbounded variant is a *legacy pre-U39*
  writer (topology A: lease held for the process lifetime and refreshed on every
  write) that keeps extending `expires_at`; against such a writer a pid-mode peer
  is refused indefinitely. That is the pre-existing U39 symptom, not something
  this ADR introduces, and it is one more argument for retiring topology A
  rather than for loosening this branch.

**Diagnosability gap (amendment, not a behaviour change):** the pid-mode message
`lease held by '…' (alive=None)` does not distinguish *why* liveness is unknown
— no permission, unparseable owner, or unsupported platform. An operator seeing
`alive=None` cannot tell a transient permissions problem from a foreign writer.
Recommendation: extend the message with a reason code (`alive=None` +
`reason=unparseable_owner|no_permission|unsupported_platform`) and **keep the
refusal**. This is a message change only; the branch stays fail-closed.

### 4.5 Release, takeover, restart

* **Release** (`release_within`, unchanged shape): set `owner = NULL`,
  `expires_at = 0`, `released_at = now`. **Keep `token`, `writer_id` and
  `writer_epoch`.** Keeping `writer_id` is required: nulling it would make every
  subsequent append look like a takeover and bump the epoch once per append,
  which would destroy its meaning.
* **Takeover after expiry**: `token+1`, `epoch+1`, new `writer_id`/`boot_gen`.
  The takeover is now *recorded* and *counted*, which is what makes the C3
  failover rule ("promote only after the dead writer's lease is provably
  expired", Gen-2 ADR §4.5) provable rather than assumed.
* **Restart**: a restarted process has a **new `writer_id`**, so it can never
  mistake the previous run's row for its own — T5's second half is closed by
  construction. If the old row is still live and from the **same boot**
  (`boot_gen` equal within tolerance), the design still refuses for the
  remaining TTL: a crashed-and-immediately-restarted writer is indistinguishable
  from a live one without a heartbeat, and refusing is the correct side to err
  on. The error message now names `writer_id` and `boot_gen`, so the operator
  can attribute it. With the heartbeat enabled (§4.7) that window shrinks to
  ~3 heartbeat intervals instead of the full TTL.

### 4.6 How a fenced writer learns it was fenced

Three mechanisms, all of them new:

1. **A distinct exception.** `FencedWriterError(StaleWriterError)` — note the
   base class, so every existing `except StaleWriterError` site keeps working —
   raised when `row.token > my_last_token` while `row.writer_id == me`. It
   carries `fenced_epoch` and the token that superseded me.
2. **A counter that survives.** `writer_epoch` on the row. The victim reads it
   in the exception; an operator reads it in `audit_stats()`; a metric
   `audit_writer_fence_total` counts it. Today a fencing event leaves **no
   trace at all** (§2 T5).
3. **Fail-closed, never silent.** The victim's next append raises →
   `record_audit_failure()` (`__init__.py:547`) → the mandatory-evidence gate
   denies the action (`_crosscutting.py:878`). It does **not** quietly acquire a
   new token and carry on.

Note that the writer must remember `my_last_token` across appends to make this
work; it is one instance attribute, and `None` means "first append — nothing to
have been fenced from" (which is why the comparison is `row.token >
(my_last_token or -1)`).

### 4.7 Heartbeat: what it costs and where it lives

The heartbeat is **not** part of the identity decision. It exists to detect
T4 (pause/resurrection) and to shorten T1/T2's denial window. It must therefore
be measurable as *free* on the append path, or not exist.

**MEASURED** (throwaway DB, WAL, `synchronous=FULL`, 1 200 single-row appends
per variant, lease row updated exactly as `_attempt_log_event` does):

| variant | events/sec | ms per append | vs baseline |
|---|---|---|---|
| no heartbeat | 843.4 / 895.7 / 890.4 | 1.186 / 1.116 / 1.123 | — (run-to-run spread ≈ 6 %) |
| heartbeat = one extra `UPDATE` **inside the append transaction** | 921.3 | 1.085 | **+9.2 %, i.e. inside noise → free** |
| heartbeat = one **separate** transaction per append | 461.3 / 466.1 | 2.168 / 2.146 | **−45.3 %** |

One extra commit under `synchronous=FULL` costs **≈ 0.98–1.03 ms** (measured
delta 2.168 − 1.186 and 2.146 − 1.123).

**ESTIMATE**, composing that measured delta with the measured batch costs in
`docs/autonomous/performance-baseline.json`:

| batch | transaction cost (MEASURED) | + 1 heartbeat transaction (ESTIMATE) | throughput change |
|---|---|---|---|
| 1 | 1.367 ms (731 eps) | 2.37 ms | 731 → ~422 eps (**−42 %**) |
| 250 | 12.163 ms (17 738 eps) | 13.16 ms | 17 738 → ~16 400 eps (**−8 %**) |

(The brief's "~3×" is the same effect read the other way up: ~1.83×–2.37×
depending on batch. Either way the conclusion is identical: **a heartbeat
transaction per append is unaffordable at batch=1 and unnecessary at batch=250.**)

**Where it lives — recommendation, in priority order:**

1. **Nowhere (v1 ships without it).** `writer_id` + `boot_gen` + `writer_epoch`
   close T1/T2/T5/T6 at **zero** throughput cost and with no new failure
   domain. T4 (pause) remains undetected — acceptable, because a paused writer
   is currently contained by the 30 s busy timeout.
2. **A daemon background thread**, `threading.Thread(daemon=True)`, one per
   process, interval `min(TTL / 4, 5 s)` = **5 s**. It runs
   `BEGIN IMMEDIATE; INSERT INTO writer_registry … ON CONFLICT(writer_id) DO
   UPDATE SET last_heartbeat_at = …; COMMIT` — **one fsync every 5 s**, i.e.
   ≈ 0.02 % of a core (**ESTIMATE**: 1.0 ms / 5 000 ms). Any exception is
   counted and logged and then **ignored**: it can never deny an append.
   Detection rule: a writer whose `last_heartbeat_at` is more than
   `3 × interval` old, **and** whose `boot_gen <= my_boot_gen` (same boot), is
   treated as not-live → takeover allowed.
   **Precondition, stated as a constraint:** heartbeat-based takeover is sound
   *only because the lease is acquired per append inside `BEGIN IMMEDIATE`. If
   the lease is ever held across appends again, heartbeat-based takeover must be
   disabled**, or a live-but-not-heartbeating writer could be fenced over while
   still appending → class (a).
3. **Piggyback on the append transaction** — MEASURED free, but useless for an
   idle writer and therefore unable to answer "is this writer alive?", which is
   the only question a heartbeat exists to answer.
4. **Per-N-appends** — arbitrary, and couples liveness to write volume.

Takeover-window comparison, **ESTIMATE** (arithmetic shown):

| | denial window after a crash with a live lease row |
|---|---|
| today (PID, TTL 30 s) | up to **30 s** (MEASURED in §2 T1) |
| v1 (`boot_gen` + TTL) | up to 30 s, but bounded by boot: **0 s after a reboot** |
| v1.1 (+ heartbeat, 5 s) | **~15 s** (3 × 5 s) |

---

## 5. Hard constraints — how the design meets each

**H-1 — the lease check and the append stay atomic on one SQLite transaction.**
`acquire_within()` / `release_within()` keep their exact current shape and call
sites. All new columns are read by the single existing `SELECT` and written by
the single existing `INSERT … ON CONFLICT` / `UPDATE`. **Zero additional round
trips, zero additional statements on the critical path.** The heartbeat is a
separate transaction *by construction* and is never read by the append path, so
it cannot break atomicity. No external coordinator (model #7) is used, which is
precisely because one could not be made atomic with the append.

**H-2 — fail-closed is preserved.** Every refusal raises
(`StaleWriterError` / `FencedWriterError`), propagates out of
`_attempt_log_event`, triggers `record_audit_failure()`, and the
mandatory-evidence gate denies the action. The design adds a *new* refusal case
(a fenced writer) and removes none. There is no "allow and hope" branch
anywhere: `_lease_is_live` returns `True` (refuse) on every ambiguity.

**H-3 — anti-fork outranks availability.** Enforced structurally: the takeover
branch is the only branch that lets a second writer in, and it requires
*positive proof* of staleness (expired beyond the skew budget, or a boot
generation strictly older than ours). Unknown `boot_gen`, foreign host, and
legacy `NULL` rows all refuse. `token` never decreases, so a superseded writer
can never become current again.

**H-4 — heartbeat cost.** §4.7. In v1 the heartbeat does not exist, so the cost
is **0**; when it is added it is one fsync per 5 s in a background thread
(≈ 0.02 % of a core, ESTIMATE), and it is never on the append path. A
per-append heartbeat transaction is explicitly rejected at **−45 %** MEASURED
throughput at batch=1.

---

## 6. Migration and rollback

### 6.1 Forward migration (additive, online)

`_LEASE_SCHEMA` uses `CREATE TABLE IF NOT EXISTS` (`fencing.py:223`), which is a
**no-op on every existing database** — so new columns need an explicit `ALTER`,
exactly like the `seq` / `hash_alg` migration in `_apply_schema`
(`__init__.py:362-389`). The migration must:

1. run **inside the same `BEGIN IMMEDIATE`** that `_init_db` already opens
   (the unguarded `ALTER` race is finding #8 in §8);
2. be driven by `PRAGMA table_info(writer_lease)`, so it is idempotent and
   order-independent;
3. **not** use `executescript()` (finding #1 in §8);
4. backfill nothing: pre-migration rows keep `writer_id = NULL`,
   `boot_gen = NULL`, `writer_epoch = 0`.

### 6.2 Mixed-version semantics (the rule that makes rollback safe)

> **A new column is a decision input only when it is non-`NULL`. `NULL` means
> "the writer that produced this row did not know about this column", and the
> code falls back to today's behaviour.**

* `writer_id IS NULL` → treated as "some unknown previous writer"; liveness is
  decided by the wall-clock TTL alone, exactly as today. A legacy row that
  looks live still refuses — **status quo preserved, no new takeover path is
  opened.**
* `boot_gen IS NULL` → no reboot test; wall-clock TTL only.
* `writer_epoch = 0` → no epoch history yet; the first forced takeover makes it
  1.

Therefore the old binary running against a new database ignores the extra
columns, sees unchanged `token` semantics, and works. **Rollback = redeploy the
old binary**, with no data conversion. The only thing lost is epoch history.

### 6.3 One migration trap that must not be fallen into

Do **not** change the `owner` string format (e.g. appending the `writer_id`) in
the same release that old binaries are still in the fleet. `_owner_pid()`
(`fencing.py:29-43`) does `owner.rsplit(":", 1)[1]` and `int(...)`; a
non-numeric tail returns `None`, `alive` becomes `None`, and the old binary's
`if alive is not False: raise` (`fencing.py:298`) then refuses **everything**.
Keep `owner` exactly `audit-store:<pid>` for one full release; it is a
diagnostic column only, and can be dropped later.

**Verified against the #99 implementation:** `_lease_owner()` is unchanged
(`audit-store:<pid>`) and is what uuid mode writes into the row — the uuid
identity lives in the separate `writer_id` column. **MEASURED**: a uuid-mode
writer leaves `owner = NULL, expires_at = 0.0` after its append, and a
subsequent pid-mode writer on the same file appends successfully. So the
mixed-fleet case in §4.4b does not arise in the shipped code. The test that must
pin this is stronger than "the function returns the right string": it must
assert the **row written by uuid mode has a pid-parseable `owner`** (or is NULL
post-release), because that is the property the legacy reader depends on.

### 6.4 The operator escape hatch — and what is forbidden

When a live lease row blocks every writer (T1/T2), the documented remedy is:

```sql
UPDATE writer_lease SET expires_at = 0, released_at = <now> WHERE id = 1;
```

**Forbidden:** `DELETE FROM writer_lease WHERE id = 1` (it resets `token` to 1,
so a fenced writer's superseded token can become "current" again — the one
operation in this whole design that can produce class (a)), and any statement
that decreases `token`. The invariant is: **`token` never decreases, and the row
is never deleted.**

### 6.5 Rollback of the design itself

Two flags, both defaulting to the *old* behaviour until the new path is proven:

* `LIUHAO_AUDIT_LEASE_IDENTITY=pid|uuid` (default `pid` on first release, `uuid`
  after the test plan in §7 is green).
* `LIUHAO_AUDIT_LEASE_HEARTBEAT=off|on` (default `off`; v1 does not need it).

---

## 7. Test plan

Every test must be **discriminating**: it must fail when the change is reverted.
A test that passes both ways proves nothing (this is the standard the U39 work
already applied — `ADR-audit-single-writer-lease.md` §6).

### Identity / threat coverage

1. **PID reuse** — plant a lease row with `owner = 'audit-store:<live pid>'`
   and `writer_id = NULL` (legacy shape) → still refused (status quo preserved,
   regression guard). Then plant `writer_id = <other uuid>`,
   `boot_gen = <current>` → refused. Then `boot_gen = <current − 60 000>` →
   takeover allowed. Three assertions, one test.
2. **Host restart simulation** — monkeypatch the uptime source; plant
   `boot_gen > my_boot_gen` (foreign host / impossible clock) → **refuse**;
   `boot_gen < my_boot_gen` → **takeover, `epoch+1`**. No real reboot needed.
3. **Clock skew** — monkeypatch `time.time` per writer:
   (a) incumbent's clock +120 s → peers refused, and the refusal duration is
   bounded by `TTL + SKEW_BUDGET` (assert the bound, not an exact sleep);
   (b) incumbent's clock −120 s → a takeover occurs **and the incumbent's next
   append raises `FencedWriterError`** — it must *not* silently continue with a
   new token. This is the key new assertion.
4. **Stale owner takeover** — legacy row, dead PID, `writer_id = NULL` →
   takeover succeeds, `token+1`, `writer_epoch` 0 → 1.
5. **Fenced writer detection** — writer A installs a live lease; writer B is
   forced to take over (test injects "not live"); A's next append raises
   `FencedWriterError` carrying `fenced_epoch == 1`, and `audit_writer_fence_total`
   increments. Assert A did **not** receive a valid new token.
6. **Cross-host collision (T6)** — two writers with the same PID but different
   `writer_id` must be treated as **different** writers → the second is refused,
   not silently renewed. (Under the status quo this test fails, which is the
   discrimination proof.)
7. **Restart** — a new `writer_id` never inherits an old live lease: it is
   refused rather than renewing it.

### Migration / rollback

8. **Old schema + new code** — a 4-column `writer_lease` created by the
   pre-migration `CREATE TABLE` → the migration adds the columns, appends work.
9. **New schema + old code** — simulate by running the current
   `acquire_within` logic against a 9-column table → appends work (proves
   rollback).
10. **`owner` format unchanged** — `_owner_pid(f"audit-store:{os.getpid()}")`
    still parses to the pid (guards §6.3).
11. **Never-decrease-token invariant** — 1 000 interleaved
    acquire / release / takeover cycles; assert `token` is strictly increasing
    and the row is never deleted.

### Non-regression

12. **Happy path** — `tests/kernels/audit/*` green (97 tests as of the U39
    baseline), and `python scripts/bench_audit_append.py --gate
    docs/autonomous/performance-baseline.json` passes: throughput lower bound
    731 eps unchanged within the 25 % tolerance. The design adds zero fsyncs and
    zero round trips, so nothing should move.
13. **Multi-process** — `tests/kernels/audit/test_multiprocess_append.py`
    (2/4/6 processes) still produces exact row counts, `seq` 1..N with no
    duplicates or gaps, 0 broken joins, `verify_integrity() is True`.
14. **U38 preserved** — two `AuditStore` instances in one process share a
    `writer_id` → renewal, no self-fence (existing
    `test_two_audit_stores_in_one_process_both_append` must still pass).
15. **Heartbeat is off the critical path** — force the heartbeat thread to
    raise on every tick; assert appends still succeed and
    `record_audit_failure()` was not called. (This is what proves the heartbeat
    cannot become a new denial source.)
16. **C6 guard** — `scripts/verify_armed_actions_are_inert.py` ALL GREEN.

### What the tests must NOT do

* Do not test liveness by killing real PIDs on Windows (flaky, and
  `_is_process_alive_win32` has a legitimate "unknown" return).
* Do not `sleep(30)` to observe TTL expiry — inject the clock.
* Do not test against `audit_store.db`; every test uses `tmp_path`.

---

## 8. Findings about the CURRENT code

Line numbers are as of `p36` @ `008ae92e`. Function names are the stable
reference.

1. **`SqliteWriterLease.__init__` calls `conn.executescript(_LEASE_SCHEMA)`
   (`fencing.py:243`), which commits any open transaction.** MEASURED:

   ```
   A2: rows still present after rollback (=> executescript committed): 1
   ```

   This contradicts the codebase's own rule, stated 400 lines away at
   `__init__.py:86-87`: *"never executescript() so it can run inside one
   BEGIN IMMEDIATE"*. Today it is harmless (it runs after `_apply_schema`'s
   `commit()` at `__init__.py:409`), but it also means the lease table is
   created **outside** the `_init_db` write transaction — the exact class of
   cold-start race already fixed for `chain_state` with `INSERT OR IGNORE`.
   Worse, `self._lease = SqliteWriterLease(self._conn)` at `__init__.py:349`
   sits **outside** the `try`/`except sqlite3.OperationalError` block that ends
   at `:343`, so a `SQLITE_BUSY` there escapes `_init_db` unhandled and is not
   covered by `_write_with_retry`. **ESTIMATE**: low probability, because
   `_SQLITE_BUSY_TIMEOUT_SEC = 30.0` means a peer would have to hold the write
   lock for over 30 s; but it is an unguarded path in bootstrap.
2. **`_LEASE_SCHEMA` is `CREATE TABLE IF NOT EXISTS` (`fencing.py:223`)**, so
   there is no forward-migration path for new columns — any schema extension
   needs an explicit `ALTER` (§6.1). Easily missed, and silently a no-op.
3. **PID liveness is the takeover decision (`fencing.py:296-302`, `:349-354`).**
   MEASURED to produce a total denial for the full remaining TTL:
   `StaleWriterError: lease held by 'audit-store:12172' (alive=True)` in 0.0003 s,
   with 29.7 s of TTL left. This is finding #7 of Gen-2 ADR §9, now measured
   end-to-end.
4. **`require_writer_lease(self._lease, self._writer_token)` at
   `__init__.py:585` and `:724` can never raise.** `acquire_within` returns a
   token that satisfies `validate()` by construction, and the lease row cannot
   change while we hold the write lock. So the explicit fence assertion on the
   append path is currently a tautology, and `is_stale()`/`validate()` are two
   calls to the same predicate (`fencing.py:157`). The design in §4.3 makes the
   assertion *meaningful* by comparing against the writer's remembered token, so
   it becomes a real check rather than a formality.
5. **`_lease_owner()` (`__init__.py:417-419`) is not unique across hosts** —
   two hosts sharing a database can produce identical owner strings and
   therefore **mutual renewal instead of fencing** (T6). Not currently
   exploited because SQLite on a network filesystem is not a supported
   deployment, but the identity is wrong in principle.
6. **No skew budget anywhere** (`fencing.py:341`, `:394` compare raw
   `time.time()` values written by different processes), and
   `expires_at = 0` doubles as both "released" and "expired long ago"
   (`fencing.py:381`, `:407`). The design separates them with `released_at`.
7. **`SqliteWriterLease.acquire()` unconditionally executes `BEGIN IMMEDIATE`
   (`fencing.py:247`)** without checking `conn.in_transaction`. MEASURED:
   `OperationalError: cannot start a transaction within a transaction`. Only
   tests call it today, but it is exported API and it is a direct footgun for
   anyone adding a heartbeat.
8. **`_init_db` performs `ALTER TABLE … ADD COLUMN` migrations on every open**
   with no cross-process lock — already recorded as finding #8 of Gen-2 ADR §9;
   the lease migration in §6.1 must land *inside* the existing `BEGIN IMMEDIATE`
   rather than adding a second unguarded one.

None of these were changed: this ADR is documentation only.

---

## 9. Open questions / human decisions

1. **Should the heartbeat ship at all in v1?** The recommendation is *no*
   (§4.7): it costs a thread and a table, and everything it buys beyond `boot_gen`
   is pause detection, which the 30 s busy timeout already contains. This is an
   engineering default, not a sovereignty question — no human decision required.
2. **Is a shared/network filesystem ever a supported deployment?** If yes, the
   answer is C2 (dedicated append-only writer service), not a better lease, and
   T6 should be re-read as a blocker rather than a hardening item.
3. **Where should `writer_epoch` be surfaced?** Options: a metric only, or also
   an additive `audit_events.writer_epoch` column. The latter is hash-safe —
   `event_payload()` (`hashutil.py:27-41`) commits to exactly eight fields, none
   of which it would be — but it widens every row. **ESTIMATE**: ~8 bytes/event,
   +1.8 % on the measured 455.4 bytes/event. Left out of v1; recorded here so
   the option is not lost.

---

## 10. Summary of the recommendation

1. Keep the current **per-append, in-transaction** lease acquisition exactly as
   it is — it is what makes the append atomic, and it is the reason the identity
   model only has to answer "refuse or take over?".
2. Replace PID with **`writer_id` (per-process `uuid4`)** — kills PID reuse,
   host restart and cross-host collision in one change, at zero throughput cost.
3. Add **`boot_gen`** (host-local monotonic uptime) — the only liveness signal
   that needs no shared clock, and the only one that can prove a reboot happened.
4. Add **`writer_epoch`**, incremented **only on a forced takeover** — the first
   durable record that a fencing event occurred.
5. Add **`FencedWriterError`** so a fenced writer fails closed and loudly
   instead of silently acquiring a new token.
6. Keep **`token`** as the fencing token and never let it decrease; never delete
   the lease row.
7. Put the **heartbeat off the critical path** (background thread, 5 s) or ship
   without it; a per-append heartbeat transaction costs **−45 %** MEASURED at
   batch=1 and buys nothing that `boot_gen` does not already buy.
8. Every ambiguous liveness decision resolves to **refuse**: availability is the
   correct thing to spend, a fork is not.

---

## 11. Implementation delta (2026-09-27) — what actually shipped

The design above is realised on branch `p36` with no behavioural change in the
default `pid` mode. Recorded here so the ADR and the code do not drift.

**Shipped:**
- `src/kernels/audit/fencing.py` — module-level per-process `writer_id`
  (uuid4), `boot_gen` (platform uptime: `GetTickCount64` on Windows,
  `CLOCK_BOOTTIME`/`CLOCK_MONOTONIC`/`time.monotonic` elsewhere), and a
  process-global last-token. `FencedWriterError(StaleWriterError)` carries
  `fenced_epoch`/`fenced_holder`/`fenced_token`; all existing
  `except StaleWriterError` sites are preserved. `acquire_within()` routes by
  `LIUHAO_AUDIT_LEASE_IDENTITY` (`pid` default | `uuid` opt-in) to
  `_acquire_within_pid` / `_acquire_within_uuid`; the uuid path implements the
  §4 decision table exactly (install / renew / `FencedWriterError` / refuse /
  take-over-with-epoch+1), with `_lease_is_live()` using `_SKEW_BUDGET_SEC=5.0`
  and `_BOOT_TOL_MS=1000`.
- `writer_lease` schema extended to 9 columns (`writer_id`, `writer_epoch`,
  `boot_gen`, `released_at`) **additively** inside `_apply_schema`'s
  `BEGIN IMMEDIATE` (PRAGMA-driven `ALTER`, idempotent) — §6.5 rollback holds:
  code in `pid` mode reads the 5 legacy columns and the extra columns are inert.
- `released_at` replaces the dual-purpose `expires_at=0` sentinel; release keeps
  `token`/`writer_id`/`writer_epoch` and only clears the live marker — §6.3.
- `src/kernels/audit/__init__.py` — wired `acquire_within(...)` with
  `writer_id`/`boot_gen`/`my_last_token`; the last-token is **process-global**
  (bound to `writer_id`), not per-`AuditStore` instance — this was the load-bearing
  fix that keeps two stores in one process (U38) from falsely fencing each other.
  `get_stats()` exposes `writer_id`/`writer_epoch`/`lease_identity_mode`/
  `writer_fence_total`.
- `tests/kernels/audit/test_lease_identity.py` — 13 cases (first-install, renew,
  live-refuse, expired-takeover, `FencedWriterError` detection, 1000× strict
  token monotonicity with no row deletion, boot-gen refuse/take-over, legacy
  5-col fallback, incremental migration, pid-default round-trip, two-instances-
  same-process U38 guard). All green; full audit suite 159 passed / 0 failed.

**Deferred (per §4.7 / §9, engineering defaults, no human decision required):**
- The v1 heartbeat is **not** implemented — `boot_gen` already contains pause
  detection within the 30 s busy timeout, and a per-append heartbeat measured
  −45 % at batch=1 for no added guarantee.
- `audit_events.writer_epoch` column (§9 option 3) is **not** added; it is
  hash-safe but widens every row (~+1.8 %), and the lease row already carries
  the durable fencing record.

**Residual risk carried forward:** the uuid identity model is opt-in and
unexercised in production until an operator sets `LIUHAO_AUDIT_LEASE_IDENTITY=
uuid`; the default `pid` path is the only one with live coverage today.
