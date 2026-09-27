# Experiment Matrix — Crash / Power-loss / Storage-failure of the Audit Store

**Status:** MEASURED (2026-09-27), autonomous deliverable, part (A).
**Author:** general-purpose-11 (team `p08-d19-d20-d21`), branch `p36`.
**Harness:** `scripts/experiment_crash_powerloss.py` (NEW — adds files only, never
touches `src/` or `audit_store.db`).
**Environment (MEASURED):**
- Windows 10 Pro, Python 3.13.14 (managed venv
  `D:/LiuHao-Data/UserRoot/.workbuddy/binaries/python/envs/default`),
  SQLite 3.53.1.
- Every experiment uses a THROWAWAY database in the OS temp directory
  (`tempfile.TemporaryDirectory`, prefix `liuhao_crash_`). Nothing in the repo
  or any evidence file was read or written.
- The durability grade is set by `LIUHAO_AUDIT_SYNCHRONOUS` (FULL default;
  NORMAL = WAL, fsync only at checkpoint — a KNOWN, DOCUMENTED trade-off, not a
  bug).

## 0. How to read the verdicts

Each fault carries one of:

- **PASS** — the observed behaviour matches the intended guarantee (including
  "process-crash safe", "fail-closed on disk-full", "storage failure reported
  honestly").
- **EXPECTED-LOSS** — the design *intends* the data to be losable under this
  fault, and the experiment confirms that intent. For NORMAL power loss this is
  a **PASS on honesty**, not a code defect: a test that shows NORMAL *can* lose
  committed data confirms the documented trade-off rather than a regression.
- **FAIL** — observed behaviour contradicts the intended guarantee.
- **N/A** — the fault cannot be faithfully reproduced on this single,
  unprivileged, non-virtualised Windows host; reason is given and we never fake
  a green result.

### The one honesty caveat that governs faults 4/5/6

A process KILL (`TerminateProcess` / SIGKILL) is a **process crash**, not a
**power loss**. A process crash leaves the OS page cache intact, and the WAL
(with the not-yet-checkpointed committed bytes) lives in that cache. So a kill
recovers the data **in BOTH** `FULL` and `NORMAL` — it proves *process-crash*
safety, and it does **NOT** exercise the NORMAL power-loss gap. The NORMAL vs
FULL difference only manifests when the OS page cache is lost (a real power
event / OS crash / cache eviction), which userland on this host cannot trigger
(no `root`, no VM, no RAM-disk power-cut). We therefore:

1. **MEASURE** what is genuinely measurable — process-crash safety, WAL-storage
   loss (delete / bit-rot), ENOSPC fail-closed, and the *applied* `PRAGMA
   synchronous` grade (FULL=2, NORMAL=1) read back from the reopened
   connection; and
2. **CONFIRM** the power-loss asymmetry at the *configuration + SQLite-durability-
   contract* level rather than by a reproduced power event, and we say so
   explicitly.

We did **not** invent a power event. Faults 4/5/6 under NORMAL are honestly
labelled `EXPECTED-LOSS`; the loss is a documented property, not a reproduced
measurement of a real power cut.

## 1. Fault-by-fault results

### Fault 1 — Process kill mid-transaction
- **Intended guarantee:** atomicity. A kill mid-transaction rolls the whole
  in-flight transaction back — no partial row, no broken hash chain, no gap in
  `seq`.
- **Method (MEASURED):** spawn a child, append 400 single-event transactions,
  `TerminateProcess` ~80 ms in, reopen, `verify_integrity()`.
- **Observed (MEASURED):** `committed_before_kill` (per progress file) = 28,
  `events_after_reopen` = 29, `verify_integrity_ok` = True, `seq_contiguous` =
  True. (Reopen count ≥ progress count: the in-flight transaction either fully
  committed or fully rolled back — never a partial row; the +1 is the commit
  that returned just before the kill landed but after the progress flush.)
- **Verdict: PASS.** Process crash is safe; the chain stays intact and
  gap-free. (This does not exercise the NORMAL power-loss gap — see Faults 4-6.)

### Fault 2 — WAL file corruption / deletion before reopen
- **Intended guarantee:** a storage failure (WAL deleted / bit-rotted) must NOT
  silently produce a false-green chain. The un-checkpointed tail is lost;
  verification must report the true (smaller) state, never invent missing events.
- **Method (MEASURED):** commit 20 events with `autocheckpoint=0` (tail only in
  WAL), kill the child, then (a) delete `-wal`/`-shm` and reopen; (b) a bit-rot
  variant that flips one byte of a live `-wal` and reopens.
- **Observed (MEASURED):**
  - WAL **deleted**: `committed_before_wal_loss` = 20, `events_after_wal_deletion`
    = 0, `verify_integrity_ok` = True. The tail is honestly reported as gone.
  - WAL **bit-rot** (one byte flipped): reopen `verify_integrity_ok` = True,
    `events` = 0 — SQLite's WAL recovery discards the corrupt/unreadable tail;
    no false green, no crash.
- **Verdict: PASS.** Deleting the WAL loses the tail in **BOTH** `FULL` and
  `NORMAL` — this is a *storage* fault, not the power-loss gap. Because under
  `NORMAL` a real power loss loses the *same* tail (the WAL was never fsync'd),
  WAL deletion is the faithful proxy for the NORMAL power-loss *consequence*; it
  cannot distinguish FULL from NORMAL because FULL's WAL is still a file that
  can be deleted.

### Fault 3 — Disk-full / ENOSPC on commit
- **Intended guarantee:** ENOSPC at commit must fail-closed — raise, roll back
  the in-flight transaction, leave NO partial/corrupt row, and remain
  recoverable.
- **Method:** a genuine ENOSPC requires filling the volume (destructive /
  privileged on Windows), so this is a **controlled exception injection** — the
  next `commit` raises `sqlite3.OperationalError("database or disk is full")`
  (SQLITE_FULL = 13), which is outside the `BUSY`/`LOCKED`/`READONLY` retry set.
- **Observed (MEASURED):** `raised_on_commit` = "database or disk is full",
  `count_before` = 0, `count_after_failed_commit` = 0,
  `verify_integrity_ok` = True, `recovery_commit_succeeded` = True.
- **Verdict: PASS.** The injected disk-full signal is not retried and
  propagates fail-closed; the event is absent; a subsequent healthy commit
  works. **Limitation:** real disk-fill was not attempted (documented); the
  injected code path is the exact one SQLite raises on a true ENOSPC.

### Fault 4 — Unclean shutdown under `synchronous=NORMAL` after commit, before checkpoint
- **Intended guarantee:** a returned commit that has not yet been checkpointed
  **MAY be lost** on a real power loss (WAL not fsync'd at commit). A *process
  crash* (kill) must NOT lose it.
- **Method (MEASURED):** open under `NORMAL`, commit 20 events
  (`autocheckpoint=0`), hard-kill after COMMIT returns, reopen, re-verify; read
  back `PRAGMA synchronous` from the reopened connection.
- **Observed (MEASURED):** `committed_before_kill` = 20, `events_after_reopen` =
  20, `verify_integrity_ok` = True, `applied_synchronous_pragma` = 1
  (NORMAL), `present_after_process_kill` = True.
- **Verdict: EXPECTED-LOSS.** After a hard *process* kill the event is present
  (correct process-crash behaviour — the page cache survives). This does NOT
  validate the documented power-loss loss, because a real power loss also loses
  the page cache, which userland on this host cannot trigger. The applied grade
  is `synchronous=1` (NORMAL): under NORMAL SQLite does NOT fsync the WAL at
  commit, so a power event between commit and checkpoint loses the tail. This
  is the KNOWN, DOCUMENTED trade-off, confirmed by configuration + SQLite's
  durability contract — a PASS on honesty, not a code bug. (Reproducing the loss
  requires a real power event / cache eviction = **N/A** on this host.)

### Fault 5 — Unclean shutdown under `synchronous=FULL` after commit
- **Intended guarantee:** a returned commit is fsync'd, so it survives a real
  power loss; a process crash must also retain it.
- **Method (MEASURED):** same as Fault 4 but under `FULL`.
- **Observed (MEASURED):** `committed_before_kill` = 20, `events_after_reopen` =
  20, `verify_integrity_ok` = True, `applied_synchronous_pragma` = 2 (FULL),
  `present_after_process_kill` = True.
- **Verdict: PASS.** Process-crash safe (observed). The power-loss guarantee is
  confirmed at the configuration + SQLite-contract level (`synchronous=2` fsyncs
  the WAL at every commit); a reproduced power event cannot be triggered in
  userland here, and is reported honestly rather than faked.

### Fault 6 — Power-loss model = kill -9 right after COMMIT returns, under NORMAL vs FULL
- **This is exactly Faults 4 and 5 run side by side.** `kill -9` (here
  `TerminateProcess`) is a *process crash*, not a power loss, so it is the wrong
  primitive to distinguish the two grades — and the results prove it:
  - **NORMAL:** `events_after_reopen` = 20, `verify_ok` = True, pragma = 1 →
    **EXPECTED-LOSS** (process-crash safe; power-loss gap confirmed at
    config/contract level, not host-reproduced).
  - **FULL:** `events_after_reopen` = 20, `verify_ok` = True, pragma = 2 →
    **PASS** (process-crash safe; power-loss safety confirmed at config/contract
    level).
- The honest reading: both modes survive a process kill; only a real power loss
  separates them, and that separation is a documented property we confirm by the
  applied `PRAGMA` grade + SQLite's contract, never by a faked power event.

## 2. Verdict summary table

| # | Fault | Mode | Intended guarantee | Observed | Verdict |
|---|---|---|---|---|---|
| 1 | Process kill mid-transaction | FULL | Atomicity, no partial row | reopen count ≥ committed, `verify_ok`=True, seq contiguous | **PASS** |
| 2 | WAL deleted before reopen | FULL | No false green; tail honestly lost | 20→0 events, `verify_ok`=True | **PASS** |
| 2b | WAL bit-rot (1 byte) | FULL | Recovery discards bad tail, no crash | `verify_ok`=True, 0 events | **PASS** |
| 3 | ENOSPC on commit (injected) | FULL | Fail-closed, recoverable | raised, 0 events, recovery OK | **PASS** |
| 4 | Unclean shutdown, pre-checkpoint | NORMAL | MAY lose on real power loss | present after kill (process crash); pragma=1 | **EXPECTED-LOSS** |
| 5 | Unclean shutdown, pre-checkpoint | FULL | Survives real power loss | present after kill; pragma=2 | **PASS** |
| 6 | kill -9 after COMMIT | NORMAL | MAY lose on real power loss | present after kill; pragma=1 | **EXPECTED-LOSS** |
| 6 | kill -9 after COMMIT | FULL | Survives real power loss | present after kill; pragma=2 | **PASS** |

## 3. What we could NOT reproduce, and why (honest N/A register)

- **Real power loss / OS-cache eviction (Faults 4/5/6 separation).** A process
  kill is insufficient (page cache survives). Triggering a genuine power event
  needs a VM, a RAM-disk power-cut, or `root` cache-drop (`/proc/sys/vm/drop_caches`
  on Linux; no userland equivalent on Windows). Out of scope for this host. We
  confirm the FULL/NORMAL asymmetry via the *applied* `PRAGMA synchronous` value
  and SQLite's documented durability contract instead.
- **Real disk-full (Fault 3).** Filling the volume is destructive/privileged on
  Windows; replaced by a faithful exception injection of the exact SQLite
  SQLITE_FULL code path.

## 4. Reproduce

```bat
D:\LiuHao-Data\UserRoot\.workbuddy\binaries\python\envs\default\Scripts\python.exe ^
    scripts/experiment_crash_powerloss.py
```

Writes `docs/autonomous/EXPERIMENT-MATRIX-crash-powerloss.json` (raw results) and
prints the table above. Only throwaway temp DBs are created.
