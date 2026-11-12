"""Reproducible verification for the executor-fence boot-survival fix (P5).

Run:  .venv/Scripts/python.exe scripts/verify_fence_boot_survival.py
Exit code 0 = all assertions green.

What this proves
---------------
The Agent-Safety Execution Fence (UBX-001 / D19-D21) is a default-DENY gate: a
``@kernel_action`` armed by ``LIUHAO_EXECUTOR_FENCE=on`` raises
``PolicyDeniedError`` unless a valid executor identity (fence context) is bound
on the call stack.

Historically that gate was armed at boot time *after* the kernel lifecycle, but
the gate itself is controlled by the env var independently of whether the fence
object was installed -- so every in-process action performed during startup
(identity seeding, kernel init) ran with NO executor identity bound and was
default-denied. The result: arming the fence made the gateway die on boot
("identity.create_identity -> 0/24 checks, RC=1").

This script reproduces that exact failure mode at the gate level, then proves
the fix (install the fence object AND bind a process-wide executor lease *before*
any in-process fenced call) lets boot survive, while the fence stays
fail-closed for rogue / escalated executors.

HC-01: every verifier REDIRs AUDIT_DB_PATH / LIUHAO_WORKSPACE_ROOT to temp, so the
frozen evidence store is never touched.
"""

from __future__ import annotations

import os
import pathlib
import sys
import tempfile

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

RESULTS = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((ok, name, detail))
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}" + (f" -- {detail}" if detail else ""))


def main() -> int:
    # --- REDIR audit + workspace so HC-01 evidence is untouched --------- #
    tmp = tempfile.mkdtemp(prefix="liuhao_fence_boot_")
    os.environ["AUDIT_DB_PATH"] = os.path.join(tmp, "audit_redir.db")
    os.environ["LIUHAO_WORKSPACE_ROOT"] = os.path.join(tmp, "ws")
    os.makedirs(os.environ["LIUHAO_WORKSPACE_ROOT"], exist_ok=True)

    # --- arm the fence gate (the boot-crash trigger) ------------------- #
    os.environ["LIUHAO_EXECUTOR_FENCE"] = "on"

    import src.kernels.execution.fence as fence_mod
    from src.kernels.execution.fence import (
        ExecutorFenceDenied,
        FenceContext,
        bind_executor_fence,
        current_executor_fence,
        establish_process_executor_lease,
        get_executor_fence,
        reset_process_executor_lease_for_testing,
        set_executor_fence,
        unbind_executor_fence,
    )
    from src.kernels._crosscutting import PolicyDeniedError, kernel_action

    # Fresh fence (no process lease bound), exactly the pre-fix boot state where
    # the fence object may or may not be "installed" but the gate is armed and no
    # executor identity is bound.
    set_executor_fence(fence_mod.ExecutorFence(fence_mod.InMemoryExecutorLease()))
    reset_process_executor_lease_for_testing()

    check(
        "fence gate reports armed",
        fence_mod.executor_fence_armed() is True,
    )

    @kernel_action("test.fence_action")  # note: enforce NOT set; only the fence gate matters
    def fenced_op():
        return "ran"

    # 1. WITHOUT a bound executor identity, an armed fenced action is denied.
    #    This is the boot-crash condition (identity/create_identity seeded with
    #    no executor identity -> PolicyDeniedError -> 0/24 checks, RC=1).
    raised = False
    try:
        fenced_op()
    except PolicyDeniedError:
        raised = True
    check(
        "PRE-FIX: armed fenced action WITHOUT executor identity is denied "
        "(the boot-crash condition)",
        raised,
    )

    # 2. THE FIX: bind a process-wide executor lease (what boot now does before
    #    any in-process fenced call).
    ctx = establish_process_executor_lease()
    check("process lease established and bound", current_executor_fence() is ctx)

    # 3. NOW the same armed fenced action succeeds -- boot survives.
    try:
        out = fenced_op()
        ran = out == "ran"
    except PolicyDeniedError:
        ran = False
    check("POST-FIX: armed fenced action WITH process lease executes (boot survives)", ran)

    # 4. Fail-closed preserved: a rogue executor identity with NO lease is denied.
    rogue = FenceContext(
        executor_id="rogue-no-lease", token=999, epoch=0, granted_capabilities=()
    )
    tkn = bind_executor_fence(rogue)
    try:
        fenced_op()
        rogue_denied = False
    except PolicyDeniedError as exc:
        rogue_denied = isinstance(exc, PolicyDeniedError)
    finally:
        unbind_executor_fence(tkn)
    check("fail-closed: rogue executor (no lease) is still denied", rogue_denied)

    # 5. Capability escalation is still denied: a real (leased) executor whose
    #    grant does not cover the required capability must be refused even with a
    #    bound, valid identity.
    f = get_executor_fence()
    leased = f.acquire_for("legit-exec", "legit-exec", capabilities=("read",), ttl_sec=30.0)
    esc_ctx = FenceContext(
        executor_id="legit-exec", token=leased.token,
        epoch=f.lease.current("legit-exec").epoch or 0,
        granted_capabilities=("read",),
    )
    tkn2 = bind_executor_fence(esc_ctx)
    escalated = False
    try:
        # Require a capability the lease does NOT grant.
        f.enforce(esc_ctx, "x", required_capabilities=("write",), correlation_id="c1")
    except ExecutorFenceDenied:
        escalated = True
    finally:
        unbind_executor_fence(tkn2)
    check("capability escalation still denied (legit identity, missing capability)", escalated)

    # 6. The legitimate (leased, capability-covered) executor is allowed.
    tkn3 = bind_executor_fence(esc_ctx)
    allowed = False
    try:
        f.enforce(esc_ctx, "x", required_capabilities=("read",), correlation_id="c2")
        allowed = True
    except ExecutorFenceDenied:
        allowed = False
    finally:
        unbind_executor_fence(tkn3)
    check("legitimate executor + covered capability is allowed", allowed)

    failed = [r for r in RESULTS if not r[0]]
    print()
    if failed:
        print(f"RESULT: {len(failed)} FAILED / {len(RESULTS)} total")
        for _ok, name, detail in failed:
            print(f"  - {name} :: {detail}")
        return 1
    print(f"RESULT: ALL GREEN ({len(RESULTS)} checks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
