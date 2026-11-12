"""PROBE 2b — WHO reads `assigned_agent` during execution, and does it matter?

Follow-up to _probe_multiagent_semantics.py PROBE 2, which counted 2 reads of
`assigned_agent` during a full execute_goal(). Two hypotheses:

  H1  the execution path reads it to ROUTE work to an agent  -> multi-agent real
  H2  something merely COPIES it into an observability payload -> label only

This probe captures the CALL STACK of every read, so the two are
distinguishable by evidence rather than by argument. It also fixes a defect in
the parent probe: PROBE 3's "plan shape identical" comparison included
dependency task-ids (uuid4), so two runs could never match. Here the shape
comparison strips ids.

Run: .venv/Scripts/python.exe scripts/_probe_assigned_agent_readers.py
"""
# flake8: noqa  -- one-off forensics probe: intentionally kept verbatim

import os
import sys
import tempfile
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_TMP = tempfile.mkdtemp(prefix="probe_aa_readers_")
os.environ["LIUHAO_WORKSPACE_ROOT"] = _TMP
os.environ["AUDIT_DB_PATH"] = os.path.join(_TMP, "audit.db")

from src.kernels.execution import ExecutionEngine, Goal, Task  # noqa: E402

STACKS = []


def instrument():
    if getattr(Task, "_probe_instrumented", False):
        return
    orig = Task.__getattribute__

    def counting(self, name):
        if name == "assigned_agent":
            # Record only frames inside src/ (skip this probe + import machinery)
            frames = [
                f for f in traceback.extract_stack()
                if "/src/" in f.filename.replace("\\", "/")
            ]
            STACKS.append([
                (os.path.basename(f.filename), f.lineno, f.name) for f in frames
            ][-6:])
        return orig(self, name)

    Task.__getattribute__ = counting
    Task._probe_instrumented = True


def shape(tasks):
    """Id-independent plan shape."""
    return [
        (t.name, t.capability_id, t.capability_namespace, sorted(t.inputs.keys()),
         sorted(t.expected_outputs.keys()), len(t.dependencies), t.status.value)
        for t in tasks
    ]


def main():
    instrument()

    goal_text = 'create a file named report.txt containing "hello"'

    print("=" * 78)
    print("A. Decomposition shape: does the agent POOL change the PLAN?")
    print("=" * 78)
    D = type(ExecutionEngine(scope="L1").decomposer)
    a = D().decompose(Goal(id="q1", natural_language=goal_text))
    b = D(agent_ids=["z", "y", "x", "w"]).decompose(Goal(id="q2",
                                                           natural_language=goal_text))
    print(f"   id-stripped plan shapes identical: {shape(a) == shape(b)}")
    for x, y in zip(a, b):
        print(f"   {x.name:<14} assigned_agent: {x.assigned_agent!r:<12} vs {y.assigned_agent!r}")
    if shape(a) != shape(b):
        print("   DIFFERENCES:")
        for x, y in zip(a, b):
            if x != y:
                print(f"     {x}\n     {y}")

    print()
    print("=" * 78)
    print("B. Call stacks of every `assigned_agent` READ during execute_goal()")
    print("=" * 78)
    seen = []

    def counting_executor(capability_id, inputs):
        seen.append(capability_id)
        return {"success": True, "executed": True}

    engine = ExecutionEngine(scope="L1")
    engine.set_capability_executor(counting_executor)

    STACKS.clear()
    engine.execute_goal(Goal(id="g_live", natural_language=goal_text))
    print(f"   total reads during execute_goal: {len(STACKS)}")
    for i, st in enumerate(STACKS, 1):
        print(f"\n   --- read #{i} ---")
        for fn, ln, func in st:
            print(f"       {fn}:{ln} {func}()")

    print()
    print("=" * 78)
    print("C. Falsification: does a DELIBERATELY BOGUS agent id change anything?")
    print("=" * 78)
    # If routing-by-agent were real, an unknown agent id should fail loudly.
    for bogus in ("NO-SUCH-AGENT-1", "", None):
        eng2 = ExecutionEngine(scope="L1")
        eng2.set_capability_executor(
            lambda cid, i: {"success": True, "executed": True, "cid": cid})
        eng2.decomposer.agent_ids = [bogus]
        ctx = eng2.execute_goal(Goal(id="g_b", natural_language=goal_text))
        st = {t.name: t.status.value for t in ctx.plan.tasks}
        print(f"   agent_ids=[{bogus!r}] -> task states {st}, "
              f"assigned={[t.assigned_agent for t in ctx.plan.tasks]}")

    print()
    print("=" * 78)
    print("D. Capability invocations observed (what actually DID the work)")
    print("=" * 78)
    print(f"   {seen}")
    print(f"   distinct capabilities that performed work: {set(seen)}")
    print(f"   tmp workspace: {_TMP}")


if __name__ == "__main__":
    main()
