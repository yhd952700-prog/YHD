# Audit append — performance baseline and regression gate

Numbers that live only in a conversation cannot be regressed against. This is
the measured baseline for the audit append path, how to reproduce it, and what
fails when it regresses.

Machine: the development workstation (win32, CPython 3.13.14, SQLite via the
stdlib driver). **Absolute numbers are machine-specific**; the gate below is
therefore tolerance-based and re-baselined deliberately, never automatically.

## How to run

```
python scripts/bench_audit_append.py --write docs/autonomous/performance-baseline.json   # re-baseline
python scripts/bench_audit_append.py --gate  docs/autonomous/performance-baseline.json    # CI check
python scripts/bench_audit_append.py --quick --gate docs/autonomous/performance-baseline.json
```

The gate is one-directional on purpose:

* throughput → **lower bound** (a slowdown fails)
* latency and recovery → **upper bound** (a slowdown fails)

It never raises a threshold by itself. A regression must fail the build rather
than quietly move the goalpost. `--tolerance` defaults to 25%, which is wide
enough to survive a noisy shared runner and narrow enough to catch a real
regression.

## Baseline

From `performance-baseline.json` in this directory.

Measured with the shipping durability default (`synchronous=FULL`).

| metric | value |
|---|---|
| single append throughput | **731 events/sec** |
| single append latency | p95 2.033 ms · p99 3.664 ms |
| batch = 1 | 739/sec |
| batch = 10 | 5,342/sec |
| batch = 50 | 12,689/sec |
| batch = 100 | 15,056/sec |
| batch = 250 | **17,738/sec** (≈24× a single append) |
| hashing alone (no database) | ~67,000/sec — **not** the bottleneck |
| 4 processes, one DB, batch 250 | 44,185/sec excluding interpreter start-up; 4,564/sec including it |
| re-verify 15,000 events | 351 ms |
| re-verify 23,000 events | 611 ms |
| disk per event | ~455 bytes |
| recovery after a hard kill | 0.103 s, chain intact |

### Why single-append throughput dropped from ~2,500 to ~731

This baseline was re-baselined on purpose, with a reason: the store now opens
`synchronous=FULL`, so a commit survives a power cut (it previously only
survived a process crash). The fsync costs ~1.4 ms per **transaction**, which
is the entire cost when you append one event per transaction, and a rounding
error when you append 250:

| batch | NORMAL | FULL | cost of durability |
|---|---|---|---|
| 1 | 2,435/sec | 741/sec | 3.29× |
| 50 | 19,722/sec | 13,969/sec | 1.41× |
| 250 | 21,434/sec | 20,249/sec | **1.06×** |

So the correct reading is not "the store got 3× slower" but **"batching is now
worth 24× instead of 8×, because it amortises the fsync that makes the evidence
durable"**. Any caller that needs throughput should batch; that is the same
conclusion the C1 work already reached, now with a durability reason on top.

### Two numbers that are easy to misread

**Multi-process throughput is reported twice.** Interpreter start-up is a fixed
~0.3–0.5 s per process; with 4 processes that is 1.45 s of the 1.75 s wall time.
Reporting only the wall-clock figure would blame the storage layer for the cost
of starting Python. The "excluding start-up" figure uses the slowest child's own
elapsed time, which is the real contention window.

**Verification cost is linear, and that is the million-scale problem.**
590 ms at 23,000 events ≈ 26 µs per event. Extrapolating (measured, two points):

| chain size | full re-verification |
|---|---|
| 100,000 events | ~2.6 s |
| 1,000,000 events | **~26 s** |
| 10,000,000 events | ~4.3 min |

Append throughput is *not* what breaks first at a million events — **audit
verification is**. A 26-second verification cannot run synchronously in a
readiness probe, and a 4-minute one cannot run at all in a request path. The fix
is segmented/incremental verification (verify the new tail incrementally, keep a
checkpointed position), which is scoped as C3 work. Until then, the honest
statement is: *the chain can be appended to at a million-event scale, but a full
re-verification of a million-event chain takes about half a minute.*

## What is deliberately NOT measured

* **Queue depth** — Option C1 batches synchronously; there is no queue. The
  metric belongs to C2 and will be added with it. Reporting a zero or a
  placeholder would be worse than reporting nothing.
* **Memory** — no portable, dependency-free RSS reading on Windows; inventing a
  number would be worse than leaving it out.
* **Disk I/O in bytes/s** — what matters operationally is bytes *per event*
  (~478, measured) and the resulting file size, both of which are recorded.

### What the gate found on its first run

The gate caught a defect that 104 passing unit tests had not: **the cold-start
`SQLITE_READONLY` race can be lost by `_init_db` itself**, not just by an
append. `_init_db` has no transaction to roll back, so the write-path retry
could not help it — a deployment whose processes all start at once could fail
before logging anything. `_init_db` now retries the whole open-and-initialise.
Regression test: `test_init_db_itself_recovers_from_a_readonly_cold_start`.

That is the argument for keeping this gate: intermittent, timing-dependent
faults are exactly the class that a deterministic unit test misses and a
repeated measurement finds.

## When to re-baseline

Re-baseline (and say why in the commit message) when: the schema changes, the
hash algorithm set changes, SQLite or CPython is upgraded, or the machine class
changes. Do not re-baseline to make a failing gate pass — investigate first.
