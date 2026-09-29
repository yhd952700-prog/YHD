"""Productionization stress / chaos benchmarks for the executor fence.

GOAL: PROVE the Agent-Safety Execution Fence + distributed coordination layer
(UBX-005) fails CLOSED under fault scenarios.  Every scenario below ends in a
concrete ``assert`` that FAILS if the fail-closed guarantee is broken -- no
"runs without error" placeholders.

Scenarios
--------
1. BACKEND OUTAGE      -- unreachable Redis => consume() raises
                          CoordinationUnavailableError, the fence maps it to
                          ExecutorFenceBackendError (a deny), so replays are
                          REFUSED, never silently allowed.
2. SPLIT-BRAIN         -- two fakeredis clients share one ``FakeServer``
                          (== two nodes sharing one state).  Node A consumes an
                          action; node B replaying it raises ReplayDetectedError.
                          Mirrors test_coordination.TestRedisLuaScriptsActuallyExecute
                          .test_consume_is_cross_node_persistent.
3. LEASE EXPIRY        -- a short-TTL lease expires; the fence re-reads current
                          state and DENIES the stale context; a re-acquire (by a
                          different holder after expiry) gets a STRICTLY GREATER
                          token.
4. CRASH-RECOVERY      -- a consuming process is KILLED mid-stream (mirrors
                          tests/kernels/audit/test_storage_faults.py kill()).
                          The coordination state (consumed markers + lease row)
                          survives in the backend; recovery refuses to
                          double-execute action-1 (ReplayDetectedError) while
                          still allowing the never-consumed action-2 (no gap).
5. FAILOVER            -- switching backend (file -> redis) does NOT migrate
                          replay-dedup state.  We HONESTLY assert the dedup is
                          lost, and that the fail-closed mitigation holds: the
                          fresh backend REFUSES the stale (old-backend)
                          authorization context instead of trusting it.

These tests only ADD files; they do not modify coordination.py / fence.py.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import time

import pytest

from src.distribution.coordination import (
    CoordinationUnavailableError,
    DistributedExecutorLease,
    DistributedLease,
    ExecutorFenceBackendError,
    FencingToken,
    FileLockLease,
    RedisLease,
)
from src.kernels.execution.fence import (
    ExecutorFence,
    ExecutorFenceDenied,
    FenceContext,
    ReplayDetectedError,
)

ROOT = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture
def lease_dir(tmp_path):
    d = tmp_path / "leases"
    d.mkdir(exist_ok=True)
    return str(d)


try:
    import fakeredis  # noqa: F401

    _HAS_FAKEREDIS = True
except Exception:  # noqa: BLE001
    _HAS_FAKEREDIS = False


class _DeadRedis:
    """A fake redis client that CONNECTS successfully (ping -> True) but every
    real command fails with ConnectionError.  This is the production "silent
    death" failure mode that MUST be fail-closed: the fence must not assume a
    command succeeded just because connect() passed.
    """

    def __init__(self) -> None:
        self.closed = False

    def ping(self):
        return True

    def eval(self, *args, **kwargs):
        raise ConnectionError("redis backend went away")

    def get(self, key):
        raise ConnectionError("redis backend went away")

    def pttl(self, key):
        raise ConnectionError("redis backend went away")

    def incr(self, key):
        raise ConnectionError("redis backend went away")

    def scan_iter(self, *args, **kwargs):
        raise ConnectionError("redis backend went away")

    def close(self):
        self.closed = True


def _factory_for_dead_redis() -> DistributedLease:
    def _factory(name: str) -> DistributedLease:
        lease = RedisLease(name, client=_DeadRedis(), prefix="dead:")
        lease.connect()
        return lease

    return _factory


def _wait_until_expiry(expires_at, margin: float = 0.10, timeout: float = 20.0) -> None:
    """Sleep until ``expires_at + margin`` (poll-based, not a fixed sleep)."""
    if expires_at is None:
        raise AssertionError("expected the lease to carry an expires_at")
    deadline = time.time() + timeout
    while time.time() <= float(expires_at) + margin:
        if time.time() > deadline:
            raise AssertionError(f"lease did not expire within {timeout}s")
        time.sleep(0.02)


def _fakeredis_nodes():
    """Return (c1, c2) sharing one FakeServer, or None if Lua can't execute."""
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


# Child process source for the crash-recovery (kill mid-stream) scenario.
_CHILD_CRASH = """
import sys, time, json
from src.distribution.coordination import FileLockLease
db_dir, prog = sys.argv[1], sys.argv[2]
lease = FileLockLease("exec-crash", directory=db_dir)
ok, tok = lease.acquire("victim-holder", 30.0)
assert ok, "victim failed to acquire"
lease.consume("exec-crash", tok, "action-1")
with open(prog, "w") as f:
    json.dump({"token": int(tok), "consumed": "action-1"}, f)
# Simulate a long-running stream; the parent KILLS this process mid-flight.
while True:
    time.sleep(0.05)
"""


def _run_child(argv, timeout=120.0):
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    return subprocess.run(
        [sys.executable, "-c", _CHILD_CRASH, *argv],
        capture_output=True, text=True, timeout=timeout, env=env,
    )


# =========================================================================== #
# 1) BACKEND OUTAGE -- the central fail-closed proof
# =========================================================================== #
class TestBackendOutageDeniesFailClosed:
    def test_unreachable_redis_refuses_to_connect(self):
        """Pointing RedisLease at an unreachable redis must raise
        CoordinationUnavailableError (fail-closed), never return a usable lease.
        """
        lease = RedisLease("outage", url="redis://127.0.0.1:1", connect_timeout=0.3)
        with pytest.raises(CoordinationUnavailableError):
            lease.connect()

    def test_dead_backend_consume_raises_coordination_unavailable(self):
        """At the lease layer: a backend that dies AFTER connect() must raise
        CoordinationUnavailableError on consume() (and acquire/validate)."""
        lease = RedisLease("outage", client=_DeadRedis(), prefix="outage:")
        lease.connect()
        with pytest.raises(CoordinationUnavailableError):
            lease.consume("exec-out", FencingToken(1), "c1")
        with pytest.raises(CoordinationUnavailableError):
            lease.acquire("owner", 30.0)
        with pytest.raises(CoordinationUnavailableError):
            lease.validate("owner", FencingToken(1))

    def test_fence_denies_replay_when_backend_is_down(self):
        """The CENTRAL proof: with the coordination backend down, the fence must
        DENY (raise ExecutorFenceDenied, specifically ExecutorFenceBackendError)
        rather than silently allowing a replay to proceed.

        We check two paths:
          * a bare consume_token is refused (a replay would be blocked);
          * even an enforce() on a plausible-looking context is refused,
            because the fence cannot verify the lease against a dead backend.
        """
        bridge = DistributedExecutorLease(lease_factory=_factory_for_dead_redis())
        fence = ExecutorFence(bridge)

        # A replay attempt (consume) must be DENIED, not silently allowed.
        with pytest.raises(ExecutorFenceDenied) as exc:
            bridge.consume_token("exec-out", FencingToken(7), "replay-attempt")
        assert isinstance(exc.value, ExecutorFenceBackendError)

        # enforce() on a plausible context is also refused: the fence cannot
        # confirm the lease exists, so it fails CLOSED.
        ctx = FenceContext(executor_id="exec-out", token=7, epoch=0)
        with pytest.raises(ExecutorFenceDenied):
            fence.enforce(ctx, "do-thing", [], correlation_id="c2")


# =========================================================================== #
# 2) SPLIT-BRAIN / CROSS-NODE REPLAY
# =========================================================================== #
@pytest.mark.skipif(
    not _HAS_FAKEREDIS,
    reason="needs fakeredis (+lupa) to actually execute the Redis Lua scripts.",
)
class TestSplitBrainCrossNodeReplay:
    @pytest.fixture
    def nodes(self):
        n = _fakeredis_nodes()
        if n is None:
            pytest.skip("fakeredis cannot execute Lua (lupa missing)")
        return n

    def test_cross_node_replay_is_detected(self, nodes):
        c1, c2 = nodes
        b1 = DistributedExecutorLease(lease_factory=lambda n: RedisLease(n, client=c1))
        b1._holder = "node-A"
        b2 = DistributedExecutorLease(lease_factory=lambda n: RedisLease(n, client=c2))
        b2._holder = "node-B"

        tok = b1.acquire("exec-sb", "owner-A", 30.0, ["read"])
        assert tok is not None
        # Node A consumes the action -> recorded in the shared backend state.
        b1.consume_token("exec-sb", tok, "action-1")
        # Node B, sharing the SAME backend state, MUST detect the replay.
        with pytest.raises(ReplayDetectedError):
            b2.consume_token("exec-sb", tok, "action-1")
        # A different correlation_id is still executable (not a global lock).
        b2.consume_token("exec-sb", tok, "action-2")

    def test_split_brain_prevents_two_live_holders(self, nodes):
        c1, c2 = nodes
        b1 = DistributedExecutorLease(lease_factory=lambda n: RedisLease(n, client=c1))
        b1._holder = "node-A"
        b2 = DistributedExecutorLease(lease_factory=lambda n: RedisLease(n, client=c2))
        b2._holder = "node-B"

        tok_a = b1.acquire("exec-sb2", "owner-A", 30.0, ["read"])
        assert tok_a is not None
        # A second node trying to take the SAME executor while it is live must
        # be DENIED (fail-closed; no split-brain / dual execution).
        with pytest.raises(ExecutorFenceDenied):
            b2.acquire("exec-sb2", "owner-B", 30.0, ["read"])

    def test_chaos_soak_replay_holds_under_interleaved_load(self, nodes):
        """Stress: hammer consume/enforce across two nodes; the replay invariant
        must hold on every iteration.  No double-execution, no false negatives."""
        c1, c2 = nodes
        b1 = DistributedExecutorLease(lease_factory=lambda n: RedisLease(n, client=c1))
        b1._holder = "node-A"
        b2 = DistributedExecutorLease(lease_factory=lambda n: RedisLease(n, client=c2))
        b2._holder = "node-B"
        tok = b1.acquire("exec-soak", "owner-A", 30.0, ["read"])
        assert tok is not None
        for i in range(300):
            cid = f"soak-{i}"
            b1.consume_token("exec-soak", tok, cid)
            # The other node sees the same consumed marker -> replay blocked.
            with pytest.raises(ReplayDetectedError):
                b2.consume_token("exec-soak", tok, cid)
        # A brand-new correlation_id is still allowed (no global lockout).
        b1.consume_token("exec-soak", tok, "soak-fresh")


# =========================================================================== #
# 3) LEASE EXPIRY
# =========================================================================== #
class TestLeaseExpiryFailClosed:
    def test_file_lease_expiry_denies_stale_and_reacquire_gets_greater_token(
        self, lease_dir
    ):
        bridge = DistributedExecutorLease(
            lease_factory=lambda n: FileLockLease(n, directory=lease_dir)
        )
        fence = ExecutorFence(bridge)

        ctx = fence.acquire_for("exec-exp", "owner", ["read"], ttl_sec=0.3)
        tok = ctx.token
        # While live, enforce() succeeds.
        fence.enforce(ctx, "read", ["read"], correlation_id="c1")

        exp = bridge.current("exec-exp").expires_at
        _wait_until_expiry(exp)

        # The stale context must now be DENIED (fence re-reads current state).
        with pytest.raises(ExecutorFenceDenied):
            fence.enforce(ctx, "read", ["read"], correlation_id="c2")
        # And the old token no longer validates.
        assert bridge.validate("exec-exp", tok) is False

        # A DIFFERENT holder (simulating a restarted process) re-acquires after
        # expiry and MUST get a STRICTLY GREATER token (no stale authority reuse).
        bridge2 = DistributedExecutorLease(
            lease_factory=lambda n: FileLockLease(n, directory=lease_dir)
        )
        bridge2._holder = "other-process"
        tok2 = bridge2.acquire("exec-exp", "owner2", 30.0, ["read"])
        assert int(tok2) > int(tok), "re-acquire after expiry must mint a greater token"
        assert bridge2.validate("exec-exp", tok2) is True

    @pytest.mark.skipif(
        not _HAS_FAKEREDIS,
        reason="needs fakeredis (+lupa) to execute Redis Lua.",
    )
    def test_redis_lease_expiry_denies_stale(self):
        nodes = _fakeredis_nodes()
        if nodes is None:
            pytest.skip("fakeredis cannot execute Lua (lupa missing)")
        c1, c2 = nodes
        lease = RedisLease("exec-exp-r", client=c1)
        lease.connect()
        ok, tok = lease.acquire("owner", 0.3)
        assert ok is True
        assert lease.validate("owner", tok) is True
        time.sleep(0.5)
        # After expiry the token is no longer valid on the SAME backend.
        assert lease.validate("owner", tok) is False
        # A different node can now take over with a strictly greater token.
        lease2 = RedisLease("exec-exp-r", client=c2)
        lease2.connect()
        ok2, tok2 = lease2.acquire("owner2", 30.0)
        assert ok2 is True and int(tok2) > int(tok)


# =========================================================================== #
# 4) CRASH-RECOVERY (kill a consumer mid-stream)
# =========================================================================== #
class TestCrashRecoveryNoDoubleExecution:
    def test_killing_consumer_mid_stream_preserves_dedup(self, tmp_path):
        """Kill a live consumer mid-stream and assert the coordination state
        survives: recovery refuses to double-execute action-1
        (ReplayDetectedError) while still allowing the never-consumed action-2
        (no gap).  Mirrors test_storage_faults.py's kill()/conn-close pattern.
        """
        db_dir = str(tmp_path / "coord-crash")
        FileLockLease("exec-crash", directory=db_dir)  # create the dir/store
        prog = str(tmp_path / "victim.prog")

        victim = subprocess.Popen(
            [sys.executable, "-c", _CHILD_CRASH, db_dir, prog],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=dict(os.environ, PYTHONPATH=str(ROOT)),
        )

        # POLL (not sleep) until the victim has consumed action-1.
        deadline = time.time() + 30
        tok = None
        while time.time() < deadline:
            try:
                data = json.loads(open(prog).read())
                if data.get("consumed") == "action-1":
                    tok = data["token"]
                    break
            except (FileNotFoundError, ValueError, json.JSONDecodeError):
                pass
            time.sleep(0.01)
        assert tok is not None, "victim never consumed before being killed"

        # Hard kill == process crash mid-stream.
        victim.kill()
        while victim.poll() is None and time.time() < deadline + 10:
            time.sleep(0.01)
        assert victim.poll() is not None, "victim process did not die"

        # Recovery: a fresh lease object on the SAME directory (a restarted
        # process re-opening the coordination state).
        rec = FileLockLease("exec-crash", directory=db_dir)
        # No double-execution: action-1 was consumed by the dead process.
        # (The raw lease raises ValueError; the bridge converts it to
        #  ReplayDetectedError -- either proves the dedup survived the crash.)
        with pytest.raises((ReplayDetectedError, ValueError)):
            rec.consume("exec-crash", FencingToken(tok), "action-1")
        # No gap: action-2 (never consumed) is still executable.
        rec.consume("exec-crash", FencingToken(tok), "action-2")
        # The lease row itself persisted (not 'unknown'/'free').
        view = rec.current()
        assert view.state in ("held", "expired", "stale")


# =========================================================================== #
# 5) FAILOVER (honest limitation + fail-closed mitigation)
# =========================================================================== #
class TestBackendFailoverHonest:
    def test_failover_loses_dedup_but_refuses_stale_context(self, tmp_path):
        """Honest documentation of a real limitation: switching backends
        (file -> redis) does NOT migrate replay-dedup state, so a replay that
        was recorded on the old backend is NOT detected on the new one.

        The fail-closed mitigation that MUST hold: the fresh backend refuses the
        stale (old-backend) authorization context, instead of trusting it.  We
        assert BOTH facts honestly -- we do not hide the gap.
        """
        file_dir = str(tmp_path / "file-coord")

        # Phase 1: file backend. Acquire + consume (records dedup on file).
        b1 = DistributedExecutorLease(
            lease_factory=lambda n: FileLockLease(n, directory=file_dir)
        )
        fence1 = ExecutorFence(b1)
        ctx = fence1.acquire_for("exec-fo", "owner", ["read"], ttl_sec=30.0)
        fence1.enforce(ctx, "read", ["read"], correlation_id="action-1")

        # Phase 2: "fail over" to a fresh redis backend (empty state).
        nodes = _fakeredis_nodes()
        if nodes is None:
            pytest.skip("fakeredis cannot execute Lua (lupa missing)")
        c1, _ = nodes
        b2 = DistributedExecutorLease(
            lease_factory=lambda n: RedisLease(n, client=c1)
        )
        fence2 = ExecutorFence(b2)

        # HONEST LIMITATION: dedup state is NOT migrated across backends.
        # Consuming the SAME correlation_id on the new backend must NOT raise
        # ReplayDetectedError -- i.e. the old dedup is gone.
        replay_detected = False
        try:
            b2.consume_token("exec-fo", ctx.token, "action-1")
        except ReplayDetectedError:
            replay_detected = True
        assert replay_detected is False, (
            "dedup must NOT silently persist across a backend switch; this test "
            "documents the real gap so it cannot be hidden."
        )

        # FAIL-CLOSED MITIGATION: the fresh backend has no lease for this
        # executor, so the OLD (file-backend) context is REFUSED -- a replay
        # carrying the stale authorization is denied, not trusted.
        with pytest.raises(ExecutorFenceDenied):
            fence2.enforce(ctx, "read", ["read"], correlation_id="action-2")


if __name__ == "__main__":
    # Allow a quick manual run without pytest (subset, best-effort).
    sys.exit(pytest.main([__file__, "-v", "-p", "no:phoenix"]))
