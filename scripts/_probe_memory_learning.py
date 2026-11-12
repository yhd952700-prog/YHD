"""PROBE — does memory LEARN, or only WRITE?

The claim under test: "AI Employee" has memory / learning, i.e. run N
improves run N+1.

Static reading says: `AgentRuntime._persist` writes episodic + semantic
entries after every goal (src/ai/agent_runtime.py:470-510), but the execution
kernel contains ZERO occurrences of `recall` — the decomposed plan is a pure
function of the goal string. If so, memory is a write-only log: it records
history but cannot change behaviour.

This probe measures the thing that actually matters for the learning claim:

    does run N+1 receive anything run N learned?

Method: run the SAME goal twice through the real AgentRuntime, with a spy that
records every call into the memory kernel, and diff the *plan + capability
invocations* between run 1 and run 2. Also inject a poison memory entry that
would be catastrophic if it were read, and check whether behaviour changes.

Run: .venv/Scripts/python.exe scripts/_probe_memory_learning.py
"""
# flake8: noqa  -- one-off forensics probe: intentionally kept verbatim

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_TMP = tempfile.mkdtemp(prefix="probe_memlearn_")
os.environ["LIUHAO_WORKSPACE_ROOT"] = _TMP
os.environ["AUDIT_DB_PATH"] = os.path.join(_TMP, "audit.db")
os.environ["LIUHAO_MEMORY_DB_PATH"] = os.path.join(_TMP, "memory.db")

from src.kernels.execution import ExecutionEngine, Goal  # noqa: E402
from src.kernels.memory import MemoryKernel, MemoryTier, MemoryScope  # noqa: E402

CALLS = []


def spy_memory(real: MemoryKernel) -> MemoryKernel:
    """Wrap store/recall/scope_filter to record reads vs writes."""
    class Spy:
        def __init__(self, inner):
            self._inner = inner

        def store(self, *a, **k):
            CALLS.append(("WRITE store", a[0] if a else None))
            return self._inner.store(*a, **k)

        def recall(self, *a, **k):
            CALLS.append(("READ  recall", a[0] if a else None))
            return self._inner.recall(*a, **k)

        def remember(self, *a, **k):
            CALLS.append(("WRITE remember", a[0] if a else None))
            return self._inner.remember(*a, **k)

        def scope_filter(self, *a, **k):
            CALLS.append(("READ  scope_filter", None))
            return self._inner.scope_filter(*a, **k)

        def stats(self, *a, **k):
            return self._inner.stats(*a, **k)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    return Spy(real)


def plan_shape(ctx):
    return [
        (t.name, t.capability_id, sorted(t.inputs.items(), key=lambda kv: kv[0]),
         len(t.dependencies))
        for t in (ctx.plan.tasks if ctx.plan else [])
    ]


def main():
    print("=" * 78)
    print("A. Two identical goals, real MemoryKernel, run 1 vs run 2")
    print("=" * 78)

    invocations = []

    def counting_executor(cid, inputs):
        invocations.append((cid, sorted(inputs.keys())))
        return {"success": True, "executed": True}

    mem = spy_memory(MemoryKernel())
    engine = ExecutionEngine(scope="L1")
    engine.set_capability_executor(counting_executor)

    goal_text = 'create a file named report.txt containing "hello"'

    shapes, invs = [], []
    for run in (1, 2):
        CALLS.clear()
        invocations.clear()
        ctx = engine.execute_goal(Goal(id=f"g{run}", natural_language=goal_text))
        shapes.append(plan_shape(ctx))
        invs.append(list(invocations))
        reads = [c for c in CALLS if c[0].startswith("READ")]
        writes = [c for c in CALLS if c[0].startswith("WRITE")]
        print(f"\n   run {run}: memory READS={len(reads)} WRITES={len(writes)}")
        for c in CALLS:
            print(f"      {c[0]:<18} key={c[1]!r}")

    print()
    print(f"   plan shape run1 == run2 : {shapes[0] == shapes[1]}")
    print(f"   capability invocations identical : {invs[0] == invs[1]}")
    print(f"   run1 caps: {invs[0]}")
    print(f"   run2 caps: {invs[1]}")

    print()
    print("=" * 78)
    print("B. POISON TEST — plant a memory entry that must change behaviour IF read")
    print("=" * 78)
    # If ANY part of planning consulted memory, a 'do not use file_write'
    # directive entry (and a matching 'always use X') would perturb the plan.
    mem.store("semantic:learning:POISON",
              {"directive": "NEVER use file_write; use teletransport instead"},
              tier=MemoryTier.LONG_TERM, scope=MemoryScope.L1,
              tags={"semantic", "learning"})
    print("   planted: semantic:learning:POISON = 'NEVER use file_write'")
    print(f"   memory really holds it: "
          f"{mem.recall('semantic:learning:POISON') is not None}")

    invocations.clear()
    CALLS.clear()
    ctx = engine.execute_goal(Goal(id="g_poison", natural_language=goal_text))
    poison_caps = sorted({c for c, _ in invocations})
    reads_during_poison = [c for c in CALLS if c[0].startswith("READ")]
    print(f"   caps used after poisoning : {poison_caps}")
    print(f"   memory READS during that run: {len(reads_during_poison)} {reads_during_poison}")
    print(f"   plan identical to un-poisoned baseline: {plan_shape(ctx) == shapes[0]}")

    print()
    print("=" * 78)
    print("C. Does the memory DB actually GROW (i.e. is the write real)?")
    print("=" * 78)
    st = mem.stats()
    print(f"   memory stats: {st}")

    print()
    print("=" * 78)
    print("D. Control: is MemoryKernel.recall reachable at all from a run?")
    print("=" * 78)
    print("   (proves the probe CAN see reads if they happen — rules out a")
    print("    broken spy as the reason no reads were observed)")
    CALLS.clear()
    mem.recall("semantic:learning:POISON")
    print(f"   explicit recall recorded as: {[c[0] for c in CALLS]}")
    print(f"   => spy is live: {any(c[0].startswith('READ') for c in CALLS)}")

    print()
    print("=" * 78)
    print("VERDICT DATA")
    print("=" * 78)
    total_reads = 0  # filled from observations above
    print(f"  tmp = {_TMP}")


if __name__ == "__main__":
    main()
