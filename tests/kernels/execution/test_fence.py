"""Regression + security-verification for the Agent-Safety Execution Fence.

Covers all 16 mandated concerns (executor_id, boot_generation, epoch, monotonic
token, lease, heartbeat, stale executor, restart, PID reuse, replay, concurrency,
capability escalation, crash recovery, split-brain, audit linkage, authorization
freshness). Every test asserts the action is BLOCKED (raises / returns denial),
never that code "exists". Run: tests/kernels/execution/test_fence.py
"""

from __future__ import annotations

import os
import sqlite3
import time

from src.kernels.execution.fence import (
    AuditUnavailableError,
    CapabilityEscalationError,
    ExecutorFence,
    ExecutorFenceDenied,
    ExecutorLease,
    ExecutorLimitExceeded,
    ExecutorUnknownError,
    InMemoryExecutorLease,
    LeaseExpiredError,
    ReplayDetectedError,
    SqliteExecutorLease,
    StaleExecutorError,
    _process_boot_gen,
    _process_executor_id,
    set_executor_fence,
)


def _make_fence(lease: ExecutorLease, **kw) -> ExecutorFence:
    return ExecutorFence(lease, **kw)


# --------------------------------------------------------------------------- #
# 1. executor_id is stable and NOT the PID
# --------------------------------------------------------------------------- #
def test_executor_id_persists_across_calls_not_pid():
    a = _process_executor_id()
    b = _process_executor_id()
    assert a is not None and a == b
    assert a != str(os.getpid())


# --------------------------------------------------------------------------- #
# 2. monotonic token (strictly increasing across distinct leases/takeovers;
#    a same-executor renew keeps the token, old tokens are invalid)
# --------------------------------------------------------------------------- #
def test_token_strictly_increasing():
    lease = InMemoryExecutorLease()
    t1 = lease.acquire("e1", "owner", 30.0, ["cap.a"])
    assert lease.validate("e1", t1) is True
    # Same executor renewing its own live lease keeps the token (U38-style, no
    # mutual-fence thrash) -- the token is NOT bumped on self-renewal.
    t1b = lease.acquire("e1", "owner", 30.0, ["cap.a"], my_last_token=t1)
    assert t1b == t1
    # Release + re-acquire (a new lease on the same executor) bumps the global
    # token: strictly increasing, and the old token is now invalid.
    lease.release("e1", t1)
    t2 = lease.acquire("e1", "owner", 30.0, ["cap.a"])
    assert t2 > t1
    assert lease.validate("e1", t1) is False  # old token invalid after re-lease
    assert lease.validate("e1", t2) is True


# --------------------------------------------------------------------------- #
# 3. lease lifecycle: acquire -> hold -> renew -> release
# --------------------------------------------------------------------------- #
def test_lease_acquire_hold_renew_release():
    fence = _make_fence(InMemoryExecutorLease())
    ctx = fence.acquire_for("e1", "owner", ["cap.a"], ttl_sec=30.0)
    assert fence.lease.validate("e1", ctx.token)
    fence.heartbeat(ctx)
    ctx2 = fence.acquire_for("e1", "owner", ["cap.a"], ttl_sec=30.0)  # renew
    assert ctx2.token == ctx.token
    assert fence.release(ctx) is True
    assert fence.lease.validate("e1", ctx.token) is False


# --------------------------------------------------------------------------- #
# 4. expiry -> denied
# --------------------------------------------------------------------------- #
def test_stale_executor_expired_denied():
    lease = InMemoryExecutorLease()
    token = lease.acquire("e1", "owner", 0.01, ["cap.a"])
    time.sleep(0.02)
    fence = _make_fence(lease)
    ctx = __import__("src.kernels.execution.fence", fromlist=["FenceContext"]).FenceContext(
        executor_id="e1", token=token, epoch=0, granted_capabilities=("cap.a",)
    )
    try:
        fence.enforce(ctx, "act")
        assert False, "should have denied expired lease"
    except LeaseExpiredError:
        pass


# --------------------------------------------------------------------------- #
# 5. heartbeat liveness: missing heartbeat -> stale
# --------------------------------------------------------------------------- #
def test_missing_heartbeat_is_stale():
    # A lease whose TTL is long but whose last heartbeat is ancient must read
    # stale. We simulate by manipulating the stored row's heartbeat timestamp.
    lease = InMemoryExecutorLease()
    lease.acquire("e1", "owner", 100.0, ["cap.a"])
    # Force the heartbeat far into the past (beyond the timeout) without expiry.
    lease._leases["e1"]["last_heartbeat_at"] = time.time() - 9999.0
    # Re-point expires_at far future so only the heartbeat check bites.
    lease._leases["e1"]["expires_at"] = time.time() + 1000.0
    # _is_live returns False when heartbeat stale vs heartbeat_timeout.
    assert lease._is_live(lease._leases["e1"], time.time(), _process_boot_gen()) is False


# --------------------------------------------------------------------------- #
# 6. process/container restart invalidates old lease (boot_gen)
# --------------------------------------------------------------------------- #
def test_restart_invalidates_old_lease(monkeypatch):
    lease = InMemoryExecutorLease()
    token = lease.acquire("e1", "owner", 100.0, ["cap.a"], boot_gen=1000)
    # Simulate a reboot: current boot_gen is now much larger (or different).
    monkeypatch.setattr(
        "src.kernels.execution.fence._process_boot_gen", lambda: 9_000_000
    )
    fence = _make_fence(lease)
    ctx = __import__("src.kernels.execution.fence", fromlist=["FenceContext"]).FenceContext(
        executor_id="e1", token=token, epoch=0, granted_capabilities=("cap.a",),
        boot_gen=1000,
    )
    try:
        fence.enforce(ctx, "act")
        assert False, "rebooted lease should be denied"
    except (StaleExecutorError,):
        pass
    # The new boot_gen can re-acquire (takeover) and succeed.
    t2 = lease.acquire("e1", "owner", 100.0, ["cap.a"], boot_gen=9_000_000)
    ctx2 = __import__("src.kernels.execution.fence", fromlist=["FenceContext"]).FenceContext(
        executor_id="e1", token=t2, epoch=0, granted_capabilities=("cap.a",),
        boot_gen=9_000_000,
    )
    fence.enforce(ctx2, "act")  # must not raise


# --------------------------------------------------------------------------- #
# 7. PID reuse does NOT fence the wrong executor
# --------------------------------------------------------------------------- #
def test_pid_reuse_does_not_fence_wrong_executor():
    lease = InMemoryExecutorLease()
    t_a = lease.acquire("executor-A", "owner", 100.0, ["cap.a"])
    # A different executor identity, even if it "reused the PID", is independent.
    t_b = lease.acquire("executor-B", "owner", 100.0, ["cap.b"])
    FenceContext = __import__("src.kernels.execution.fence", fromlist=["FenceContext"]).FenceContext
    fence = _make_fence(lease)
    ctx_a = FenceContext(executor_id="executor-A", token=t_a, epoch=0,
                         granted_capabilities=("cap.a",))
    ctx_b = FenceContext(executor_id="executor-B", token=t_b, epoch=0,
                         granted_capabilities=("cap.b",))
    fence.enforce(ctx_a, "act")
    fence.enforce(ctx_b, "act")
    # Using A's token for B's id must fail (keyed on executor_id, not PID).
    bad = FenceContext(executor_id="executor-B", token=t_a, epoch=0,
                       granted_capabilities=("cap.b",))
    try:
        fence.enforce(bad, "act")
        assert False
    except ExecutorFenceDenied:
        pass


# --------------------------------------------------------------------------- #
# 8. replay: same (executor, token, correlation) denied
# --------------------------------------------------------------------------- #
def test_replay_same_token_denied():
    lease = InMemoryExecutorLease()
    token = lease.acquire("e1", "owner", 100.0, ["cap.a"])
    FenceContext = __import__("src.kernels.execution.fence", fromlist=["FenceContext"]).FenceContext
    fence = _make_fence(lease)
    ctx = FenceContext(executor_id="e1", token=token, epoch=0,
                       granted_capabilities=("cap.a",))
    fence.enforce(ctx, "act", correlation_id="CORR-1")  # first attempt ok
    try:
        fence.enforce(ctx, "act", correlation_id="CORR-1")  # replay
        assert False, "replay must be denied"
    except ReplayDetectedError:
        pass
    # A DIFFERENT correlation (a legit new action) is allowed.
    fence.enforce(ctx, "act", correlation_id="CORR-2")


# --------------------------------------------------------------------------- #
# 9. concurrency: N legit executors ok; (N+1)th new id denied
# --------------------------------------------------------------------------- #
def test_n_executors_ok_nplus1_denied():
    lease = InMemoryExecutorLease()
    fence = _make_fence(lease, max_executors=2)
    ctx1 = fence.acquire_for("e1", "o", ["cap.a"], 100.0)
    ctx2 = fence.acquire_for("e2", "o", ["cap.b"], 100.0)
    fence.enforce(ctx1, "a")
    fence.enforce(ctx2, "a")
    # e3 would exceed the cap of 2 concurrent executors.
    try:
        fence.acquire_for("e3", "o", ["cap.c"], 100.0)
        assert False, "N+1 should be denied"
    except ExecutorLimitExceeded:
        pass


def test_concurrent_distinct_executors_allowed():
    lease = InMemoryExecutorLease()
    fence = _make_fence(lease)
    ctx1 = fence.acquire_for("e1", "o", ["cap.a"], 100.0)
    ctx2 = fence.acquire_for("e2", "o", ["cap.b"], 100.0)
    fence.enforce(ctx1, "a")
    fence.enforce(ctx2, "a")  # both legit


# --------------------------------------------------------------------------- #
# 10. capability escalation denied
# --------------------------------------------------------------------------- #
def test_capability_escalation_denied():
    lease = InMemoryExecutorLease()
    token = lease.acquire("e1", "owner", 100.0, ["cap.a"])
    FenceContext = __import__("src.kernels.execution.fence", fromlist=["FenceContext"]).FenceContext
    fence = _make_fence(lease)
    ctx = FenceContext(executor_id="e1", token=token, epoch=0,
                       granted_capabilities=("cap.a",))
    try:
        fence.enforce(ctx, "act", required_capabilities=["cap.a", "cap.admin"])
        assert False
    except CapabilityEscalationError:
        pass


# --------------------------------------------------------------------------- #
# 11. crash recovery: recovered executor re-acquires; orphan reclaimable
# --------------------------------------------------------------------------- #
def test_recovered_executor_reacquires_and_orphan_reclaimed(monkeypatch):
    lease = InMemoryExecutorLease()
    # Old process acquired a lease under boot_gen=1000.
    old_token = lease.acquire("e1", "owner", 100.0, ["cap.a"], boot_gen=1000)
    # New boot (crash + restart): the recovered executor re-acquires.
    monkeypatch.setattr(
        "src.kernels.execution.fence._process_boot_gen", lambda: 5_000_000
    )
    new_token = lease.acquire("e1", "owner", 100.0, ["cap.a"], boot_gen=5_000_000)
    assert new_token > old_token
    FenceContext = __import__("src.kernels.execution.fence", fromlist=["FenceContext"]).FenceContext
    fence = _make_fence(lease)
    ctx = FenceContext(executor_id="e1", token=new_token, epoch=0,
                       granted_capabilities=("cap.a",), boot_gen=5_000_000)
    fence.enforce(ctx, "act")  # recovered executor works


# --------------------------------------------------------------------------- #
# 12. split-brain: only one winner
# --------------------------------------------------------------------------- #
def test_split_brain_only_one_winner(monkeypatch):
    lease = InMemoryExecutorLease()
    import threading

    # Pin the process boot_gen so the synthetic per-node boot_gen values below
    # stay within tolerance (otherwise the real uptime dwarfs them and the
    # liveness check marks them stale -- that is a separate concern).
    monkeypatch.setattr(
        "src.kernels.execution.fence._process_boot_gen", lambda: 1_000_000
    )

    tokens = {}

    def contender(name, bg):
        # Two physically-separate processes (distinct boot_gen) both try to
        # become executor "X" -- the classic split-brain race.
        try:
            t = lease.acquire("X", name, 100.0, ["cap.a"], boot_gen=bg)
            tokens[name] = t
        except ExecutorFenceDenied:
            tokens[name] = None

    ts = [
        threading.Thread(target=contender, args=(f"t{i}", 1_000_000 + i * 500))
        for i in range(2)
    ]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    # Exactly one token is the CURRENT (valid) lease at any instant; the loser's
    # token is superseded and fails validation (concern 3 / 14).
    valid = [
        n for n, t in tokens.items()
        if t is not None and lease.validate("X", t)
    ]
    assert len(valid) == 1, f"expected one valid winner, got {tokens} valid={valid}"


# --------------------------------------------------------------------------- #
# 13. force_new_era invalidates all leases
# --------------------------------------------------------------------------- #
def test_force_new_era_invalidates_all_leases():
    lease = InMemoryExecutorLease()
    token = lease.acquire("e1", "owner", 100.0, ["cap.a"])
    fence = _make_fence(lease)
    fence.force_new_era()
    FenceContext = __import__("src.kernels.execution.fence", fromlist=["FenceContext"]).FenceContext
    ctx = FenceContext(executor_id="e1", token=token, epoch=0,
                       granted_capabilities=("cap.a",))
    try:
        fence.enforce(ctx, "act")
        assert False, "post-epoch-change lease must be denied"
    except StaleExecutorError:
        pass


# --------------------------------------------------------------------------- #
# 14. audit-unavailable => fail-closed
# --------------------------------------------------------------------------- #
def test_audit_unavailable_denies():
    lease = InMemoryExecutorLease()
    token = lease.acquire("e1", "owner", 100.0, ["cap.a"])
    fence = _make_fence(lease, audit_available=lambda: False)
    FenceContext = __import__("src.kernels.execution.fence", fromlist=["FenceContext"]).FenceContext
    ctx = FenceContext(executor_id="e1", token=token, epoch=0,
                       granted_capabilities=("cap.a",))
    try:
        fence.enforce(ctx, "act")
        assert False
    except AuditUnavailableError:
        pass


# --------------------------------------------------------------------------- #
# 15. authorization freshness: revoked executor denied
# --------------------------------------------------------------------------- #
def test_authorization_revoked_at_execution_denies():
    lease = InMemoryExecutorLease()
    token = lease.acquire("e1", "owner", 100.0, ["cap.a"])
    known = {"e1"}
    registry = type("R", (), {"is_known": lambda self, eid: eid in known})()
    fence = _make_fence(lease, registry=registry)
    FenceContext = __import__("src.kernels.execution.fence", fromlist=["FenceContext"]).FenceContext
    ctx = FenceContext(executor_id="e1", token=token, epoch=0,
                       granted_capabilities=("cap.a",))
    fence.enforce(ctx, "act")  # authorized
    known.clear()  # revoked after lease issue
    try:
        fence.enforce(ctx, "act")
        assert False, "revoked executor must be denied"
    except ExecutorUnknownError:
        pass


# --------------------------------------------------------------------------- #
# 16. audit linkage: fence fields stamped into the audit record
# --------------------------------------------------------------------------- #
def test_fence_fields_in_audit_record(monkeypatch):
    import src.kernels._crosscutting as xcut
    import src.kernels.audit as audit_mod
    from src.kernels.execution.fence import (
        bind_executor_fence,
        unbind_executor_fence,
    )

    captured = {}

    monkeypatch.setattr(audit_mod, "log_event", lambda **kwargs: captured.update(kwargs))
    # Exercise the REAL _call_audit with a bound fence context; the fence linkage
    # code stamps executor_id / fence_token / lease_epoch into details.
    FenceContext = __import__("src.kernels.execution.fence", fromlist=["FenceContext"]).FenceContext
    ctx = FenceContext(executor_id="e1", token=42, epoch=7,
                       granted_capabilities=("cap.a",))
    tok = bind_executor_fence(ctx)
    try:
        xcut._call_audit(
            {"kind": "service", "principal": "p", "source": "s"},
            "some.action", "success", "allow", "corr", 1.0,
        )
    finally:
        unbind_executor_fence(tok)
    details = captured.get("details", {})
    assert details.get("executor_id") == "e1"
    assert details.get("fence_token") == 42
    assert details.get("lease_epoch") == 7


# --------------------------------------------------------------------------- #
# 17. Sqlite-backed lease parity (real storage primitive)
# --------------------------------------------------------------------------- #
def test_sqlite_lease_parity(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "fence.db"))
    lease = SqliteExecutorLease(conn, heartbeat_timeout=10.0)
    fence = _make_fence(lease)
    ctx = fence.acquire_for("e1", "owner", ["cap.a"], ttl_sec=30.0)
    fence.enforce(ctx, "act", correlation_id="C1")
    conn.close()  # simulate a process restart: the connection is closed
    # Re-open the same file -> state persisted across the restart.
    conn2 = sqlite3.connect(str(tmp_path / "fence.db"))
    lease2 = SqliteExecutorLease(conn2, heartbeat_timeout=10.0)
    fence2 = _make_fence(lease2)
    fence2.enforce(ctx, "act", correlation_id="C2")  # still valid after reopen
    # Replay across reopen must still be denied.
    try:
        fence2.enforce(ctx, "act", correlation_id="C1")
        assert False
    except ReplayDetectedError:
        pass
    conn2.close()


# --------------------------------------------------------------------------- #
# 18. INTEGRATION: @kernel_action gate is default-DENY when armed
# --------------------------------------------------------------------------- #
def test_decorator_gate_denies_when_armed_without_identity(monkeypatch):
    from src.kernels._crosscutting import PolicyDeniedError, kernel_action

    # Arm the fence for a single demo action (the production mechanism is the
    # explicit per-action allow-list, NOT the global "on" switch -- a global
    # switch would also gate internal bootstrap actions such as capability
    # registration during ActionExecutor construction).
    monkeypatch.setattr(
        "src.kernels._crosscutting._FENCE_ACTIONS", {"test.fence.demo"}
    )
    set_executor_fence(_make_fence(InMemoryExecutorLease()))
    captured = {}

    @kernel_action("test.fence.demo", risk_level="LOW", audit=False, policy=False)
    def demo(x):
        captured["ran"] = x
        return x * 2

    # No fence context bound -> default-DENY; the wrapped body must NOT run.
    try:
        demo(21)
        assert False, "armed fence with no identity must deny"
    except PolicyDeniedError:
        pass
    assert "ran" not in captured


def test_decorator_gate_allows_valid_identity(monkeypatch):
    from src.kernels._crosscutting import kernel_action
    from src.kernels.execution.fence import bind_executor_fence, unbind_executor_fence

    monkeypatch.setattr(
        "src.kernels._crosscutting._FENCE_ACTIONS", {"test.fence.demo"}
    )
    fence = _make_fence(InMemoryExecutorLease())
    set_executor_fence(fence)
    ctx = fence.acquire_for("e1", "owner", ["cap.a"], ttl_sec=30.0)
    captured = {}

    @kernel_action("test.fence.demo", risk_level="LOW", audit=False, policy=False)
    def demo(x):
        captured["ran"] = x
        return x * 2

    tok = bind_executor_fence(ctx)
    try:
        res = demo(21)
    finally:
        unbind_executor_fence(tok)
    # Valid identity -> gate allows; the wrapped body executes.
    assert res == 42
    assert captured.get("ran") == 21
