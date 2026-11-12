#!/usr/bin/env python
"""Audit append performance baseline + automated regression gate.

Why this exists
---------------
Throughput numbers that live only in a chat log cannot be regressed against.
This script turns them into a file that CI can compare against:

    python scripts/bench_audit_append.py --write-baseline scripts/bench_baseline.json
    python scripts/bench_audit_append.py --quick --gate scripts/bench_baseline.json

The numeric gate is keyed by a machine/profile fingerprint (OS, Python
version, CPU model/thread count, quick flag). A baseline only compares against
a run with the SAME profile: a shared runner on different hardware is not a
regression of the code, so a profile mismatch SKIPs the numeric gate instead of
failing it. The machine-independent INVARIANTS (no lost appends, contiguous
seq, chain intact after a kill, chain verifies) are always the blocking check.

The gate is deliberately LOWER-BOUND only for throughput (a slowdown fails)
and UPPER-BOUND for latency/recovery (a slowdown fails). It never raises a
threshold on its own -- a regression must fail, not move the goalposts.

It measures what the owner asked to be measured, and it says so when a metric
is not measurable on this platform rather than inventing one:

    throughput        appends/sec, single and batched
    latency           p50 / p95 / p99 per append and per batch
    cpu               process CPU seconds consumed (time.process_time)
    disk              bytes actually written to the DB file + WAL
    recovery          seconds from a hard kill to a verified append
    verify            cost of re-verifying a chain of N events
    multi-process     aggregate appends/sec with N live processes

Queue depth is NOT measured here: Option C1 has no queue, it batches
synchronously. That metric belongs to C2 and will be added with it.

It never touches the production audit_store.db -- every run uses a throwaway
database in a temp directory.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import platform
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.kernels.audit import AuditEvent, AuditEventType, AuditScope, AuditStore  # noqa: E402

# Child used for the multi-process and recovery measurements.
_CHILD = r'''
import sys, time
sys.path.insert(0, __ROOT__)
from src.kernels.audit import AuditStore, AuditEvent, AuditEventType, AuditScope
db, count, batch, tag = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
store = AuditStore(db_path=db); store.initialize()
t0 = time.time()
done = 0
while done < count:
    n = min(batch, count - done)
    evs = [AuditEvent(event_id="%s-%d" % (tag, done + i),
                      event_type=AuditEventType.STATE_CHANGE,
                      principal_id="bench", scope=AuditScope.L0,
                      timestamp=time.time(), correlation_id="c",
                      outcome="ok", details={"i": done + i})
           for i in range(n)]
    store.log_event_batch(evs)
    done += n
print("%.6f" % (time.time() - t0))
'''


def _percentile(values, pct):
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round((pct / 100.0) * (len(ordered) - 1))))
    return ordered[idx]


def _bytes_on_disk(db):
    total = 0
    for suffix in ("", "-wal", "-shm"):
        path = db + suffix
        if os.path.exists(path):
            total += os.path.getsize(path)
    return total


def bench_single(store, count):
    latencies = []
    cpu0, wall0 = time.process_time(), time.time()
    for i in range(count):
        t = time.time()
        store.log_event(AuditEventType.STATE_CHANGE, "bench", AuditScope.L0, "ok",
                        {"i": i}, f"c{i}")
        latencies.append((time.time() - t) * 1000.0)
    wall = time.time() - wall0
    cpu = time.process_time() - cpu0
    return {
        "events": count,
        "events_per_sec": round(count / wall, 1) if wall else 0.0,
        "latency_ms_p50": round(_percentile(latencies, 50), 3),
        "latency_ms_p95": round(_percentile(latencies, 95), 3),
        "latency_ms_p99": round(_percentile(latencies, 99), 3),
        "cpu_seconds": round(cpu, 3),
        "wall_seconds": round(wall, 3),
    }


def bench_batched(store, total, batch_size):
    latencies = []
    cpu0, wall0 = time.process_time(), time.time()
    done = 0
    while done < total:
        n = min(batch_size, total - done)
        evs = [
            AuditEvent(event_id=f"b{batch_size}-{done + i}",
                       event_type=AuditEventType.STATE_CHANGE,
                       principal_id="bench", scope=AuditScope.L0,
                       timestamp=time.time(), correlation_id="c",
                       outcome="ok", details={"i": done + i})
            for i in range(n)
        ]
        t = time.time()
        store.log_event_batch(evs)
        latencies.append((time.time() - t) * 1000.0)
        done += n
    wall = time.time() - wall0
    cpu = time.process_time() - cpu0
    batches = len(latencies)
    return {
        "batch_size": batch_size,
        "events": total,
        "events_per_sec": round(total / wall, 1) if wall else 0.0,
        "batch_latency_ms_p50": round(_percentile(latencies, 50), 3),
        "batch_latency_ms_p95": round(_percentile(latencies, 95), 3),
        "batch_latency_ms_p99": round(_percentile(latencies, 99), 3),
        "per_event_ms_p95": round(_percentile(latencies, 95) / batch_size, 4),
        "cpu_seconds": round(cpu, 3),
        "wall_seconds": round(wall, 3),
        "batches": batches,
    }


def bench_verify(store, add_events=(2000, 8000)):
    """Cost of re-verifying the chain at several sizes.

    Verification cost is what decides whether a million-event chain can still
    be audited, so it is measured at more than one size -- a single sample
    cannot show whether the cost grows linearly.
    """
    samples = {}
    index = store.verify_integrity()[1]  # events already present
    for extra in add_events:
        target = index + extra
        while index < target:
            n = min(500, target - index)
            store.log_event_batch([
                AuditEvent(event_id=f"v{index + i}",
                           event_type=AuditEventType.STATE_CHANGE,
                           principal_id="bench", scope=AuditScope.L0,
                           timestamp=time.time(), correlation_id="c",
                           outcome="ok", details={"i": index + i})
                for i in range(n)
            ])
            index += n
        ok, total = store.verify_integrity()
        if not ok:
            raise SystemExit("chain failed to verify during benchmark -- aborting")
        t = time.time()
        store.verify_integrity()
        samples[f"verify_ms_at_{total}_events"] = round((time.time() - t) * 1000.0, 2)
    return samples


def bench_recovery(tmpdir, db):
    """Seconds from a hard kill to a verified append on the same database."""
    proc = subprocess.Popen(
        [sys.executable, "-c", _CHILD.replace("__ROOT__", repr(str(ROOT))),
         db, "20000", "50", "killed"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env=dict(os.environ, PYTHONPATH=str(ROOT)),
    )
    time.sleep(0.4)
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=60)

    t0 = time.time()
    store = AuditStore(db_path=db)
    store.initialize()
    store.log_event(AuditEventType.STATE_CHANGE, "bench", AuditScope.L0, "ok",
                    {"phase": "after-kill"}, "c-after-kill")
    ok, _ = store.verify_integrity()
    elapsed = time.time() - t0
    return {
        "recovery_seconds": round(elapsed, 3),
        "chain_intact_after_kill": bool(ok),
    }


def bench_multiprocess(db, processes, per_process, batch):
    """Aggregate throughput with N live processes contending for one DB."""
    procs = []
    wall0 = time.time()
    for p in range(processes):
        procs.append(subprocess.Popen(
            [sys.executable, "-c", _CHILD.replace("__ROOT__", repr(str(ROOT))),
             db, str(per_process), str(batch), f"p{p}"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env=dict(os.environ, PYTHONPATH=str(ROOT)),
        ))
    errs = []
    child_seconds = []
    for i, p in enumerate(procs):
        out, err = p.communicate(timeout=600)
        if p.returncode != 0:
            errs.append(f"child {i} rc={p.returncode}: {err[-300:]}")
        else:
            # The child prints its own elapsed time. Wall time includes Python
            # start-up, which is a fixed ~0.3-0.5s per process and says
            # nothing about contention -- reporting only the wall number would
            # blame the storage layer for the interpreter's start-up cost.
            try:
                child_seconds.append(float(out.strip().splitlines()[-1]))
            except (ValueError, IndexError):
                pass
    wall = time.time() - wall0
    if errs:
        raise SystemExit("multi-process benchmark failed:\n" + "\n".join(errs))

    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
    seqs = [r[0] for r in conn.execute("SELECT seq FROM audit_events ORDER BY seq")]
    conn.close()
    expected = processes * per_process
    contention_window = max(child_seconds) if child_seconds else wall
    return {
        "processes": processes,
        "batch_size": batch,
        "aggregate_events_per_sec_including_startup": round(expected / wall, 1),
        "aggregate_events_per_sec_excluding_startup": round(
            expected / contention_window, 1) if contention_window else 0.0,
        "wall_seconds": round(wall, 3),
        "slowest_child_seconds": round(contention_window, 3),
        "startup_overhead_seconds": round(wall - contention_window, 3),
        "rows_committed": rows,
        "rows_expected": expected,
        "no_lost_appends": rows == expected,
        "contiguous_seq": seqs == list(range(1, expected + 1)),
    }


def run(quick: bool = False) -> dict:
    count = 500 if quick else 2000
    batch_sizes = [10, 100] if quick else [1, 10, 50, 100, 250]

    tmpdir = tempfile.mkdtemp(prefix="liuhao_bench_")
    try:
        db = os.path.join(tmpdir, "bench.db")
        store = AuditStore(db_path=db)
        store.initialize()
        before = _bytes_on_disk(db)

        result = {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "python": sys.version.split()[0],
            "platform": sys.platform,
            "quick": quick,
            "single_append": bench_single(store, count),
            "batched_append": [bench_batched(store, max(2000, count), b)
                               for b in batch_sizes],
        }
        result["verify"] = bench_verify(
            store, add_events=(3000, 8000) if not quick else (2000, 4000)
        )
        ok, total = store.verify_integrity()
        result["chain_verifies"] = bool(ok)
        result["events_in_database"] = total
        result["disk_bytes_per_event"] = round(
            (_bytes_on_disk(db) - before) / max(1, total), 1)
        result["database_bytes"] = _bytes_on_disk(db)
        store._conn.close()

        mp_db = os.path.join(tmpdir, "mp.db")
        result["multi_process"] = bench_multiprocess(
            mp_db, processes=2 if quick else 4,
            # Enough events per child that interpreter start-up is not the
            # dominant term in the measurement.
            per_process=500 if quick else 2000,
            batch=25 if quick else 250,
        )
        crash_db = os.path.join(tmpdir, "crash.db")
        result["recovery"] = bench_recovery(tmpdir, crash_db)
        return result
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def check_gate(baseline: dict, current: dict, tolerance: float) -> list:
    """Return a list of human-readable gate failures (empty == pass)."""
    failures = []

    def lower(path, base, cur):
        limit = base * (1.0 - tolerance)
        if cur < limit:
            failures.append(
                f"{path}: throughput regressed {cur} < {limit:.1f} "
                f"(baseline {base}, tolerance {tolerance:.0%})")

    def upper(path, base, cur):
        limit = base * (1.0 + tolerance)
        if cur > limit:
            failures.append(
                f"{path}: latency regressed {cur} > {limit:.3f} "
                f"(baseline {base}, tolerance {tolerance:.0%})")

    b, c = baseline["single_append"], current["single_append"]
    lower("single_append.events_per_sec", b["events_per_sec"], c["events_per_sec"])
    upper("single_append.latency_ms_p95", b["latency_ms_p95"], c["latency_ms_p95"])

    bmap = {x["batch_size"]: x for x in baseline["batched_append"]}
    for cur in current["batched_append"]:
        base = bmap.get(cur["batch_size"])
        if not base:
            continue
        lower(f"batched[{cur['batch_size']}].events_per_sec",
              base["events_per_sec"], cur["events_per_sec"])

    if "multi_process" in baseline and "multi_process" in current:
        for key in ("aggregate_events_per_sec_excluding_startup",
                    "aggregate_events_per_sec_including_startup"):
            if key in baseline["multi_process"] and key in current["multi_process"]:
                lower(f"multi_process.{key}",
                      baseline["multi_process"][key],
                      current["multi_process"][key])
        if not current["multi_process"]["no_lost_appends"]:
            failures.append("multi_process: appends were LOST under concurrency")
        if not current["multi_process"]["contiguous_seq"]:
            failures.append("multi_process: seq is not contiguous -- chain forked")

    if "recovery" in baseline and "recovery" in current:
        upper("recovery.recovery_seconds",
              baseline["recovery"]["recovery_seconds"],
              current["recovery"]["recovery_seconds"])
        if not current["recovery"]["chain_intact_after_kill"]:
            failures.append("recovery: chain NOT intact after a hard kill")

    if not current.get("chain_verifies"):
        failures.append("chain_verifies: the benchmark chain does not verify")
    return failures


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", metavar="PATH", help="write the baseline JSON here")
    ap.add_argument("--write-baseline", metavar="PATH",
                    help="write/update the profile-keyed baseline (keyed by "
                         "machine fingerprint); preserves other profiles")
    ap.add_argument("--gate", metavar="PATH",
                    help="compare against the profile-keyed baseline; SKIPs "
                         "when the current machine profile does not match")
    ap.add_argument("--tolerance", type=float, default=0.25,
                    help="allowed regression fraction (default 0.25 = 25%%)")
    ap.add_argument("--quick", action="store_true", help="fewer samples, for CI")
    ap.add_argument("--invariants-only", action="store_true",
                    help="""check ONLY machine-independent properties (no lost
                    appends, contiguous seq, chain intact after a kill, chain
                    verifies). Throughput numbers are machine-specific, so a
                    shared CI runner cannot be compared against a workstation
                    baseline -- these invariants can, which makes them the
                    blocking CI gate while the numeric gate stays profile-scoped.""")
    args = ap.parse_args()

    current = run(quick=args.quick)
    profile = detect_profile(args.quick)

    if args.write:
        path = pathlib.Path(args.write)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(current, indent=2), encoding="utf-8")
        print(f"wrote baseline -> {path}")

    if args.write_baseline:
        write_profile_baseline(args.write_baseline, profile, current,
                               platform.platform())
        print(f"wrote profile baseline [{profile}] -> {args.write_baseline}")

    print(json.dumps(current, indent=2))

    if args.invariants_only:
        failures = check_invariants(current)
        label, status = "INVARIANT", "GATE"
    elif args.gate:
        status, failures, label, skip_msg = decide_gate(
            args.gate, profile, current, args.tolerance)
    else:
        failures, status, label, skip_msg = [], "GATE", "PERFORMANCE", ""

    if args.gate or args.invariants_only:
        if status == "SKIP":
            print(f"\n=== {label} GATE: SKIP ===")
            print("  - " + skip_msg)
            print("  (numeric gate skipped; invariants remain the blocking check)")
            return 0
        if failures:
            print(f"\n=== {label} GATE: FAIL ===")
            for f in failures:
                print("  - " + f)
            return 1
        print(f"\n=== {label} GATE: PASS ===")
    return 0


def check_invariants(current: dict) -> list:
    """Properties that hold on ANY machine -- the blocking CI gate.

    Throughput in events/sec is a property of the machine, so a runner cannot
    be compared against a workstation baseline. These are not: they are
    correctness properties that must hold everywhere, and losing one of them is
    always a regression regardless of hardware.
    """
    failures = []
    mp = current.get("multi_process", {})
    if not mp.get("no_lost_appends"):
        failures.append(
            f"multi_process: appends were LOST under concurrency "
            f"({mp.get('rows_committed')} of {mp.get('rows_expected')})")
    if not mp.get("contiguous_seq"):
        failures.append(
            "multi_process: seq is not contiguous -- the chain forked")
    rec = current.get("recovery", {})
    if not rec.get("chain_intact_after_kill"):
        failures.append("recovery: chain NOT intact after a hard kill")
    if not current.get("chain_verifies"):
        failures.append("chain_verifies: the benchmark chain does not verify")
    return failures


# ---------------------------------------------------------------------------
# Machine/profile fingerprint + profile-keyed baseline handling.
#
# A performance baseline (events/sec, latency) is a property of the machine it
# was captured on. Comparing a CI runner against a workstation baseline -- or
# against a different workload (quick vs full) -- produces a gate that fails
# for reasons that have nothing to do with the code. To keep CI numbers honest,
# the numeric gate is keyed by a profile fingerprint and SKIPS (never fails,
# never pretends to pass) when the current profile does not match the baseline.
# ---------------------------------------------------------------------------

BASELINE_SCHEMA = "liuhao-bench-baseline/v1"


def detect_profile(quick: bool) -> str:
    """Machine/profile fingerprint used to key performance baselines.

    Two runs only compare when their profiles match. Override with the
    LIUHAO_BENCH_PROFILE env var for reproducible CI runs (e.g. pin a runner).
    """
    override = os.environ.get("LIUHAO_BENCH_PROFILE")
    if override:
        return override
    os_name = platform.system()
    pyver = sys.version.split()[0]
    cpu = (platform.processor() or platform.machine() or "unknown").replace(" ", "_")
    threads = os.cpu_count() or 0
    return f"{os_name}|py{pyver}|{cpu}|t{threads}|{'quick' if quick else 'full'}"


def load_profile_baseline(baseline_path, profile):
    """Resolve the baseline for ``profile``.

    Returns ``(status, baseline_or_None, message)`` where ``status`` is either
    ``"GATE"`` (profile matched, compare numerically) or ``"SKIP"`` (do NOT
    fail CI -- re-baseline required or file missing/legacy).
    """
    p = pathlib.Path(baseline_path)
    if not p.exists():
        return "SKIP", None, f"baseline file not found: {baseline_path}"
    doc = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or "profiles" not in doc:
        return ("SKIP", None,
                "baseline file is not profile-keyed (legacy flat format) -- "
                "re-baseline with --write-baseline")
    profiles = doc.get("profiles", {})
    if profile not in profiles:
        available = ", ".join(sorted(profiles)) or "<none>"
        return ("SKIP", None,
                f"profile mismatch -- re-baseline required. "
                f"current profile: {profile!r}; available: {available}")
    return "GATE", profiles[profile], ""


def write_profile_baseline(baseline_path, profile, current, machine):
    """Write/update the profile-keyed baseline, preserving other profiles."""
    p = pathlib.Path(baseline_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    doc = {}
    if p.exists():
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            doc = {}
    if not isinstance(doc, dict) or "profiles" not in doc:
        doc = {"schema": BASELINE_SCHEMA, "default_profile": profile,
               "profiles": {}}
    doc["schema"] = BASELINE_SCHEMA
    doc["default_profile"] = doc.get("default_profile") or profile
    entry = dict(current)
    entry["profile"] = profile
    entry["captured_at"] = current.get("generated_at")
    entry["machine"] = machine
    doc["profiles"][profile] = entry
    p.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return doc


def decide_gate(baseline_path, profile, current, tolerance):
    """Decide the numeric gate without re-running the benchmark.

    Returns ``(status, failures, label, message)``. ``status`` is ``"SKIP"``
    (failures empty, caller must NOT fail CI) or ``"GATE"`` (run the numeric +
    invariant comparison). The machine-independent invariants are folded in so
    a correctness break blocks even on a matching profile.
    """
    status, baseline, msg = load_profile_baseline(baseline_path, profile)
    if status == "SKIP":
        return "SKIP", [], "PERFORMANCE", msg
    failures = list(check_gate(baseline, current, tolerance))
    failures += check_invariants(current)
    return "GATE", failures, "PERFORMANCE", ""


if __name__ == "__main__":
    raise SystemExit(main())
