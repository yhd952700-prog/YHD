#!/usr/bin/env python3
"""Standalone chaos/soak benchmark for the executor fence + coordination layer.

Runs the five fail-closed fault scenarios WITHOUT pytest and prints a
PASS / FAIL / SKIP report per scenario.  Each scenario asserts the fail-closed
behaviour with a real check -- it is not a "runs without error" smoke test.

Run:
    D:/LiuHao-Data/UserRoot/.workbuddy/binaries/python/envs/default/Scripts/python.exe \
        scripts/bench_fence_chaos.py

Exit code is 0 only if every non-skipped scenario PASSED.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.distribution.coordination import (  # noqa: E402
    CoordinationUnavailableError,
    DistributedExecutorLease,
    ExecutorFenceBackendError,
    FencingToken,
    FileLockLease,
    RedisLease,
)
from src.kernels.execution.fence import (  # noqa: E402
    ExecutorFence,
    ExecutorFenceDenied,
    FenceContext,
    ReplayDetectedError,
)

try:
    import fakeredis  # noqa: E402

    _HAS_FAKEREDIS = True
except Exception:  # noqa: BLE001
    _HAS_FAKEREDIS = False


class _DeadRedis:
    def ping(self):
        return True

    def eval(self, *a, **k):
        raise ConnectionError("backend gone")

    def get(self, k):
        raise ConnectionError("backend gone")

    def pttl(self, k):
        raise ConnectionError("backend gone")

    def incr(self, k):
        raise ConnectionError("backend gone")

    def scan_iter(self, *a, **k):
        raise ConnectionError("backend gone")

    def close(self):
        pass


def _factory_for_dead_redis():
    def _f(name):
        lease = RedisLease(name, client=_DeadRedis(), prefix="dead:")
        lease.connect()
        return lease

    return _f


def _fakeredis_nodes():
    if not _HAS_FAKEREDIS:
        return None
    server = fakeredis.FakeServer()
    c1 = fakeredis.FakeStrictRedis(server=server)
    c2 = fakeredis.FakeStrictRedis(server=server)
    try:
        assert c1.eval("return 1", 0) == 1
    except Exception:  # noqa: BLE001
        return None
    return c1, c2


# --------------------------------------------------------------------------- #
# Scenario implementations (each raises on failure)
# --------------------------------------------------------------------------- #
def scenario_backend_outage():
    # Unreachable redis refuses to connect (fail-closed).
    lease = RedisLease("outage", url="redis://127.0.0.1:1", connect_timeout=0.3)
    try:
        lease.connect()
        raise AssertionError("unreachable redis must NOT connect")
    except CoordinationUnavailableError:
        pass

    # Dead backend -> consume raises CoordinationUnavailableError.
    dead = RedisLease("outage", client=_DeadRedis(), prefix="outage:")
    dead.connect()
    try:
        dead.consume("exec-out", FencingToken(1), "c1")
        raise AssertionError("consume on dead backend must raise")
    except CoordinationUnavailableError:
        pass

    # The fence maps that to ExecutorFenceBackendError (a deny).
    bridge = DistributedExecutorLease(lease_factory=_factory_for_dead_redis())
    fence = ExecutorFence(bridge)
    try:
        bridge.consume_token("exec-out", FencingToken(7), "replay-attempt")
        raise AssertionError("fence must deny consume on dead backend")
    except ExecutorFenceDenied as exc:
        assert isinstance(exc, ExecutorFenceBackendError)
    ctx = FenceContext(executor_id="exec-out", token=7, epoch=0)
    try:
        fence.enforce(ctx, "do-thing", [], correlation_id="c2")
        raise AssertionError("fence must deny enforce on dead backend")
    except ExecutorFenceDenied:
        pass


def scenario_split_brain():
    nodes = _fakeredis_nodes()
    if nodes is None:
        return "SKIP"
    c1, c2 = nodes
    b1 = DistributedExecutorLease(lease_factory=lambda n: RedisLease(n, client=c1))
    b1._holder = "node-A"
    b2 = DistributedExecutorLease(lease_factory=lambda n: RedisLease(n, client=c2))
    b2._holder = "node-B"
    tok = b1.acquire("exec-sb", "owner-A", 30.0, ["read"])
    assert tok is not None
    b1.consume_token("exec-sb", tok, "action-1")
    try:
        b2.consume_token("exec-sb", tok, "action-1")
        raise AssertionError("cross-node replay not detected")
    except ReplayDetectedError:
        pass
    # A different correlation_id is still executable.
    b2.consume_token("exec-sb", tok, "action-2")
    # Two live holders for the same executor must not both be granted.
    try:
        b2.acquire("exec-sb", "owner-B", 30.0, ["read"])
        raise AssertionError("split-brain: second node was granted the lease")
    except ExecutorFenceDenied:
        pass


def scenario_lease_expiry():
    tmp = tempfile.mkdtemp(prefix="bench-exp-")
    bridge = DistributedExecutorLease(
        lease_factory=lambda n: FileLockLease(n, directory=tmp)
    )
    fence = ExecutorFence(bridge)
    ctx = fence.acquire_for("exec-exp", "owner", ["read"], ttl_sec=0.3)
    tok = ctx.token
    fence.enforce(ctx, "read", ["read"], correlation_id="c1")
    exp = bridge.current("exec-exp").expires_at
    deadline = time.time() + 20
    while time.time() <= float(exp) + 0.1 and time.time() < deadline:
        time.sleep(0.02)
    try:
        fence.enforce(ctx, "read", ["read"], correlation_id="c2")
        raise AssertionError("expired context was allowed")
    except ExecutorFenceDenied:
        pass
    assert bridge.validate("exec-exp", tok) is False
    # Re-acquire by a different holder after expiry -> strictly greater token.
    bridge2 = DistributedExecutorLease(
        lease_factory=lambda n: FileLockLease(n, directory=tmp)
    )
    bridge2._holder = "other-process"
    tok2 = bridge2.acquire("exec-exp", "owner2", 30.0, ["read"])
    assert int(tok2) > int(tok)


def scenario_crash_recovery():
    tmp = tempfile.mkdtemp(prefix="bench-crash-")
    child = """
import sys, time, json
from src.distribution.coordination import FileLockLease
db_dir, prog = sys.argv[1], sys.argv[2]
lease = FileLockLease("exec-crash", directory=db_dir)
ok, tok = lease.acquire("victim-holder", 30.0)
assert ok
lease.consume("exec-crash", tok, "action-1")
with open(prog, "w") as f:
    json.dump({"token": int(tok), "consumed": "action-1"}, f)
while True:
    time.sleep(0.05)
"""
    prog = os.path.join(tmp, "victim.prog")
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    victim = __import__("subprocess").Popen(
        [sys.executable, "-c", child, tmp, prog],
        stdout=__import__("subprocess").DEVNULL,
        stderr=__import__("subprocess").DEVNULL,
        env=env,
    )
    tok = None
    dl = time.time() + 30
    while time.time() < dl:
        try:
            data = json.loads(open(prog).read())
            if data.get("consumed") == "action-1":
                tok = data["token"]
                break
        except Exception:
            pass
        time.sleep(0.01)
    assert tok is not None, "victim never consumed before kill"
    victim.kill()
    while victim.poll() is None and time.time() < dl + 10:
        time.sleep(0.01)
    assert victim.poll() is not None
    rec = FileLockLease("exec-crash", directory=tmp)
    try:
        rec.consume("exec-crash", FencingToken(tok), "action-1")
        raise AssertionError("double-execution after crash recovery")
    except (ReplayDetectedError, ValueError):
        pass
    rec.consume("exec-crash", FencingToken(tok), "action-2")
    assert rec.current().state in ("held", "expired", "stale")


def scenario_failover():
    tmp = tempfile.mkdtemp(prefix="bench-fo-")
    b1 = DistributedExecutorLease(
        lease_factory=lambda n: FileLockLease(n, directory=tmp)
    )
    fence1 = ExecutorFence(b1)
    ctx = fence1.acquire_for("exec-fo", "owner", ["read"], ttl_sec=30.0)
    fence1.enforce(ctx, "read", ["read"], correlation_id="action-1")
    nodes = _fakeredis_nodes()
    if nodes is None:
        return "SKIP"
    c1, _ = nodes
    b2 = DistributedExecutorLease(lease_factory=lambda n: RedisLease(n, client=c1))
    fence2 = ExecutorFence(b2)
    # HONEST: dedup is NOT migrated across backends.
    replay_detected = False
    try:
        b2.consume_token("exec-fo", ctx.token, "action-1")
    except ReplayDetectedError:
        replay_detected = True
    assert replay_detected is False, "dedup must NOT silently persist across backends"
    # FAIL-CLOSED: fresh backend refuses the stale (old-backend) context.
    try:
        fence2.enforce(ctx, "read", ["read"], correlation_id="action-2")
        raise AssertionError("fresh backend trusted a stale context")
    except ExecutorFenceDenied:
        pass


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #
SCENARIOS = [
    ("BACKEND OUTAGE (fail-closed deny)", scenario_backend_outage),
    ("SPLIT-BRAIN / CROSS-NODE REPLAY", scenario_split_brain),
    ("LEASE EXPIRY (stale denied, greater token)", scenario_lease_expiry),
    ("CRASH-RECOVERY (no double-exec / no gap)", scenario_crash_recovery),
    ("FAILOVER (dedup lost + stale context refused)", scenario_failover),
]


def main() -> int:
    print("=" * 72)
    print("FENCE + COORDINATION CHAOS / SOAK BENCHMARK")
    print("=" * 72)
    results = []
    for name, fn in SCENARIOS:
        start = time.time()
        try:
            ret = fn()
            if ret == "SKIP":
                status = "SKIP"
                detail = "backend/lua unavailable in this environment"
            else:
                status = "PASS"
                detail = "fail-closed guarantee held"
        except Exception as exc:  # noqa: BLE001
            status = "FAIL"
            detail = f"{type(exc).__name__}: {exc}"
        elapsed = time.time() - start
        results.append((name, status, detail, elapsed))
        print(f"[{status}] {name}  ({elapsed:.2f}s)")
        if status != "PASS":
            print(f"        -> {detail}")

    passed = sum(1 for _, s, _, _ in results if s == "PASS")
    skipped = sum(1 for _, s, _, _ in results if s == "SKIP")
    failed = sum(1 for _, s, _, _ in results if s == "FAIL")
    print("-" * 72)
    print(f"SUMMARY: {passed} passed, {skipped} skipped, {failed} failed "
          f"of {len(results)} scenarios")
    print("=" * 72)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
