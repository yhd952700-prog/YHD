"""PROBE (read-only forensics, not a test).

Question under investigation:
  Is the "multi-agent" claim real, or is it a label?

Method: instrument the *objects that would have to be read* on the execution
path, and count reads at runtime. Static grep says `assigned_agent` is only
WRITTEN in src/kernels/execution; this probe measures whether anything READS
it, and whether N distinct agent ids change any observable outcome.

Run:  .venv/Scripts/python.exe scripts/_probe_multiagent_semantics.py
"""
# flake8: noqa  -- one-off forensics probe: intentionally kept verbatim

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_TMP = tempfile.mkdtemp(prefix="probe_multiagent_")
os.environ["LIUHAO_WORKSPACE_ROOT"] = _TMP
os.environ["AUDIT_DB_PATH"] = os.path.join(_TMP, "audit.db")

from src.kernels.execution import ExecutionEngine, Goal  # noqa: E402

READS = {"assigned_agent": 0, "agent_ids": 0}


class SpyTask(dict):
    """Not used for execution; we instrument the real Task dataclass instead."""


def instrument_reads():
    """Wrap Task.__getattribute__ so every READ of `assigned_agent` is counted."""
    from src.kernels.execution import Task as _Task
    if getattr(_Task, "_probe_instrumented", False):
        return
    orig_getattr = _Task.__getattribute__

    def counting_getattr(self, name):
        if name == "assigned_agent":
            READS["assigned_agent"] += 1
        return orig_getattr(self, name)

    _Task.__getattribute__ = counting_getattr
    _Task._probe_instrumented = True


def main():
    instrument_reads()
    engine = ExecutionEngine(scope="L1")

    # Two DECOMPOSERS: identical, except one is told there are 3 real agents.
    plain = type(engine.decomposer)()
    multi = type(engine.decomposer)(agent_ids=["emp-a0", "emp-a1", "emp-a2"])

    goal_text = 'create a file named report.txt containing "hello"'
    g1 = Goal(id="g_plain", natural_language=goal_text, scope="L1")
    g2 = Goal(id="g_multi", natural_language=goal_text, scope="L1")

    t_plain = plain.decompose(g1)
    t_multi = multi.decompose(g2)

    print("=" * 74)
    print("PROBE 1 — what the decomposer actually produces")
    print("=" * 74)
    for label, tasks in (("no agent pool", t_plain), ("3 'real' agents", t_multi)):
        print(f"\n-- decomposer WITH {label}: {len(tasks)} task(s)")
        for t in tasks:
            print(f"   task={t.name:<14} capability={t.capability_id:<20} "
                  f"assigned_agent={t.assigned_agent!r}")
        caps = [t.capability_id for t in tasks]
        print(f"   capability set identical between the two runs? "
              f"{caps == [x.capability_id for x in (t_plain if label.startswith('no') else t_multi)]}")

    print()
    print("=" * 74)
    print("PROBE 2 — does the ASSIGNED AGENT change any EXECUTION behaviour?")
    print("=" * 74)
    # Deliberately pathological assignment: same task, absurd agent id.
    t_multi[0].assigned_agent = "TOTALLY-NONEXISTENT-AGENT-XYZ"

    # Swap in a counting capability executor so we can see exactly which
    # (capability_id, inputs) reach the executor.
    seen = []

    def counting_executor(capability_id, inputs):
        seen.append((capability_id, dict(inputs)))
        return {"success": True, "executed": True}

    engine.set_capability_executor(counting_executor)

    before = READS["assigned_agent"]
    ctx = engine.execute_goal(Goal(id="g_x", natural_language=goal_text,
                              scope="L1"))
    after = READS["assigned_agent"]

    print(f"   assigned_agent READS during a full engine.execute_goal(): {after - before}")
    print(f"   (note: this run used the engine's OWN decomposer, whose")
    print(f"    agent_ids = {getattr(engine.decomposer, 'agent_ids', None)!r})")
    print(f"   capability invocations actually observed: {len(seen)}")
    for cid, inp in seen:
        print(f"     -> {cid}  inputs={sorted(inp.keys())}")

    print()
    print("=" * 74)
    print("PROBE 3 — is `agent_ids` consulted for DECOMPOSITION decisions?")
    print("=" * 74)
    a = type(engine.decomposer)().decompose(
        Goal(id="q1", natural_language=goal_text, scope="L1"))
    b = type(engine.decomposer)(agent_ids=["z", "y", "x", "w"]).decompose(
        Goal(id="q2", natural_language=goal_text, scope="L1"))
    strip = lambda ts: [(t.name, t.capability_id, sorted(t.inputs.keys()),
                         t.dependencies[:]) for t in ts]
    print(f"   plan shape (name/capability/inputs/deps) identical: {strip(a) == strip(b)}")
    print(f"   the ONLY difference is the assigned_agent STRING:")
    print(f"     no pool : {[t.assigned_agent for t in a]}")
    print(f"     4 agents: {[t.assigned_agent for t in b]}")

    print()
    print("=" * 74)
    print("VERDICT DATA (interpretation is the analyst's job, not the script's)")
    print("=" * 74)
    print(f"  reads_of_assigned_agent_during_execute = {after - before}")
    print(f"  reads_of_assigned_agent_total           = {READS['assigned_agent']}")
    print(f"  plan_changed_by_agent_pool              = {strip(a) != strip(b)}")
    print(f"  distinct_capabilities_invoked           = {len({c for c, _ in seen})}")
    print(f"  tmp_workspace                           = {_TMP}")


if __name__ == "__main__":
    main()
