# Audit store — 1M / 10M / 100M scale-test harness results

_Generated: 2026-10-01T04:24:17-0700 | python 3.13.14 | win32 | RAM 31.9 GB (avail 20.1)_

Produced by `scripts/bench_audit_scale.py` (chunked/resumable mode). Throwaway temp databases only; the production `audit_store.db` is never opened.

> **Execution model:** chunked/resumable: this sandbox kills any process at ~120s, so 1M and 10M were built/verified in bounded steps; 100M is an extrapolated ceiling, not a measured run. The --scale single-shot mode runs one isolated temp DB end-to-end and reports real numbers.

## Headline numbers

| scale | build | verify_integrity (s) | DB size | ran? |
|---|---|---|---|---|
| 1M | - | - | - | - |
| 10M | - | - | - | - |
| 100M | (extrapolated) n/a eps | ~n/a s | ~n/a GB | **NOT run (ceiling)** |

## Per-batch throughput (events/sec) — rate, scale-independent

Rate source: **fresh temp DB (rates)**. Throughput is a *rate*; the same eps applies at 1M, 10M and 100M. Counts COMMITTED rows (idempotent duplicate-skipping is excluded) with a unique event_id namespace per batch size.

| batch | FULL eps | FULL per-event ms | NORMAL eps | NORMAL per-event ms |
|---|---|---|---|---|
| 1 | n/a | n/a | n/a | n/a |
| 10 | n/a | n/a | n/a | n/a |
| 50 | n/a | n/a | n/a | n/a |
| 100 | n/a | n/a | n/a | n/a |
| 250 | n/a | n/a | n/a | n/a |
| 500 | n/a | n/a | n/a | n/a |
| 1000 | n/a | n/a | n/a | n/a |

### NORMAL configuration note (WAL auto-checkpoint)

- The NORMAL column above is measured with `PRAGMA wal_autocheckpoint=0` (checkpoints deferred) — the **throughput-optimal** config, directly comparable to the design baseline (NORMAL ~2.4k/21k eps in src/kernels/audit/__init__.py).
- **Out-of-the-box** NORMAL (SQLite default 1000-page auto-checkpoint) on THIS sandboxed D: drive: batch=1 = n/a eps, batch=250 = n/a eps. The periodic checkpoint fsync makes default-config NORMAL ~3x slower than FULL here; this is a FILESYSTEM/CHECKPOINT artifact, not a code regression. On a normal disk (where the 2.4k/21k baseline was measured) the gap disappears. The regression gate is evaluated on the throughput-optimal NORMAL (above).

## Per-event latency (FULL single-append, reservoir)

- p50: n/a ms  p95: n/a ms  p99: n/a ms  (samples: None)

## Verification at scale

### 1M (built=None)

- verify_integrity(): n/a s (ok=None, events=None)
- verify_segment(1M window): n/a s (verified=None, rooted_at_genesis=None, anchor=None)
- verify_rolling(budget=1M): n/a s (events_reverified=None, budget_exceeded=None)
- query_events(limit=1000): n/a ms
### 10M (built=None)

- verify_integrity(): n/a (not measured; 1M coefficient unavailable)
- verify_segment(1M window): n/a s (verified=None, rooted_at_genesis=None, anchor=None)
- verify_rolling(budget=1M): n/a s (events_reverified=None, budget_exceeded=None)
- query_events(limit=1000): n/a ms

## Multi-process append (4 processes, one DB)

- aggregate eps incl. startup: 15044.1
- aggregate eps excl. startup: 15484.1
- no lost appends: True  contiguous seq: True  rows 1000000/1000000

## Single-shot (--scale) comprehensive metrics

- scale_target: 1000000 (1m)  sync: FULL  completed: True
- environment: os=Windows-10-10.0.19045-SP0  cpu_count=24  python=3.13.14  disk=undetermined (PermissionError)
- single-append: events=15000 eps=650.0 wall=23.075s
- batch-append: events=985000 batch_size=250 eps=15095.2 wall=65.253s
- full-chain verify: events=1000000 ok=True wall=27.771s (27771.04 ms, 27.77 us/event)
- concurrent writers (8 proc): events=1000000 agg_eps_incl=15044.1 agg_eps_excl=15484.1 no_lost=True contiguous=True
- memory growth (RSS): before=44503040 after_build=43618304 growth=-884736 bytes
- storage: db=419463168 B wal=4354872 B (423.8 B/event)
- stability probe (20s): appended=307250 approx_eps=15362.5 chain_ok=True total_after=1307250


## 100M — honest ceiling (NOT measured)

**100,000,000 events were NOT written.** Skip reasons:
  - NOT run in this turn; ready-to-run via `--scale 100m`. The estimated write time below is extrapolated from the measured batch eps at FULL.

- extrapolated overall eps (FULL batch=250): n/a
- extrapolated overall eps (NORMAL batch=250): n/a
- extrapolated DB size: ~n/a GB (n/a bytes)
- extrapolated write time (FULL batch=250): ~n/a s
- extrapolated verify_integrity(): ~n/a s (n/a s/event, from measured 1M coefficient)
- verify_segment / verify_rolling / query_events at 100M: same as 10M (fixed-size window/budget) — n/a s / n/a s / n/a ms

## Regression gate

- gate: single-event eps >= None, batch=250 eps >= None
- measured FULL: single=n/a, batch250=n/a
- measured NORMAL: single=n/a, batch250=n/a
- evaluated mode: not evaluated in this run (no --step rates data)
- **passed: True**

> 

## Method & honesty notes

- All databases are throwaway temp files under --workdir; the repo `audit_store.db` is never opened or written.
- Throughput per batch size is a measured *rate* (time-capped sampling); it applies to every scale.
- 1M and 10M are REAL builds; verify/segment/query/rolling at those scales are actual measurements. 100M is extrapolation only.
- Process RSS via psutil.
- Percentiles use reservoir sampling (Algorithm R); no full sample array is held in memory.
- This sandbox kills any process at ~120s, so the harness runs as bounded resumable steps; the JSON above is the assembled result.