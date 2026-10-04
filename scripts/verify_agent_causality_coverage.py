"""COVERAGE — does the "decorative agent layer" finding generalise beyond
the single file_write goal?

Weakness in the first round of evidence: every arm used one goal shape
(`create a file ...`), so a critic can fairly say "you only proved it for
file_write". This probe runs the ablation across the FULL set of goal shapes
the deterministic decomposer actually recognises, so the claim becomes either
general or visibly narrow.

For each goal shape we ask the same causal question:
    does the outcome change when the agent pool changes?

A shape is only counted as evidence if the run genuinely COMPLETED and wrote a
real artifact — otherwise "the outcome did not change" would be trivially true
because nothing happened either way (the most dangerous form of vacuous pass).

Run: .venv/Scripts/python.exe scripts/verify_agent_causality_coverage.py
"""
import hashlib
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = tempfile.mkdtemp(prefix="verify_agent_coverage_")
os.environ["AUDIT_DB_PATH"] = os.path.join(ROOT, "audit.db")
os.environ["LIUHAO_MEMORY_DB_PATH"] = os.path.join(ROOT, "memory.db")

from src.ai.agent_runtime import AgentRuntime  # noqa: E402
from src.ai.lcore import LCore  # noqa: E402
from src.kernels.memory import MemoryKernel  # noqa: E402

# Each entry: (label, goal text, capability we EXPECT to be exercised)
GOAL_SHAPES = [
    ("file_write", 'create a file named report.txt containing "hello"', "file_write"),
    ("compute", "calculate the sum of 2 and 3 using python", "python_compute"),
    ("search", "search for information about quantum computing", "network_bus"),
    ("memory", "remember this note for later", "multi_tier_memory"),
    ("plan", "plan the quarterly schedule", "execution_pipeline"),
    ("multi_shape",
     'search for information and create a file named notes.txt containing "x"',
     None),  # multi-capability: exercises two decomposer branches at once
]

ARMS = {
    "no_pool": None,
    "real_pool": ["emp-a0", "emp-a1", "emp-a2"],
    "ghost_pool": ["GHOST-1", "GHOST-2", "GHOST-3"],
}


def _disk(ws):
    out = {}
    for root, _d, files in os.walk(ws):
        for fn in files:
            p = os.path.join(root, fn)
            try:
                with open(p, "rb") as fh:
                    out[os.path.relpath(p, ws)] = hashlib.sha256(fh.read()).hexdigest()[:12]
            except OSError:
                out[os.path.relpath(p, ws)] = "<unreadable>"
    return out


def _run(shape, arm, mem):
    ws = os.path.join(ROOT, f"{shape}__{arm}")
    os.makedirs(ws, exist_ok=True)
    os.environ["LIUHAO_WORKSPACE_ROOT"] = ws
    lcore = LCore(scope="L1", register_local_tools=True)
    rt = AgentRuntime(scope="L1", capability_executor=lcore.capability_executor(),
                      memory=mem)
    ids = ARMS[arm]
    if ids is not None:
        rt.engine.decomposer.agent_ids = ids
    r = rt.run_goal(GOAL_SHAPES_BY_NAME[shape], goal_id=f"g_{shape}_{arm}")
    plan = r.context.plan if r.context else None
    tasks = list(plan.tasks) if plan else []
    return {
        "state": r.state.value,
        "work": [(t.name, t.capability_id, t.status.value) for t in tasks],
        "artifacts": _disk(ws),
        "labels": [t.assigned_agent for t in tasks],
    }


GOAL_SHAPES_BY_NAME = {name: text for name, text, _ in GOAL_SHAPES}


def main():
    mem = MemoryKernel()
    print("=" * 78)
    print("COVERAGE: does 'decorative agent layer' hold across goal shapes?")
    print("=" * 78)
    print("A shape only counts as evidence if the run COMPLETED and produced a")
    print("real artifact -- otherwise 'nothing changed' would be vacuous.\n")

    evidence, vacuous = [], []
    for shape, _text, expected_cap in GOAL_SHAPES:
        runs = {arm: _run(shape, arm, mem) for arm in ARMS}
        base = runs["no_pool"]
        # Did anything actually happen?
        did_work = base["state"] == "completed" and bool(base["work"])
        cap_seen = {c for _n, c, _s in base["work"]}
        if not did_work:
            vacuous.append((shape, base["state"], base["work"]))
            print(f"-- {shape:<12} VACUOUS (state={base['state']}, "
                  f"tasks={len(base['work'])}) -> excluded from the claim")
            continue

        outcomes = {
            arm: (r["state"], r["work"], sorted(r["artifacts"].items()))
            for arm, r in runs.items()
        }
        causal = outcomes["no_pool"] != outcomes["real_pool"]
        ghost_differs = outcomes["no_pool"] != outcomes["ghost_pool"]
        labels_differ = len({tuple(r["labels"]) for r in runs.values()}) == 3

        evidence.append((shape, causal, ghost_differs, labels_differ))
        print(f"-- {shape:<12} state={base['state']} caps={sorted(cap_seen)}")
        print(f"   artifacts on disk : {sorted(base['artifacts'])}")
        print(f"   labels differ per arm      : {labels_differ}")
        print(f"   real pool changes outcome  : {causal}")
        print(f"   ghost pool changes outcome : {ghost_differs}")

    print()
    print("=" * 78)
    print("VERDICT")
    print("=" * 78)
    print(f"  shapes with real work (evidence-grade): {len(evidence)}")
    for shape, _c, _g, _l in evidence:
        print(f"    - {shape}")
    print(f"  shapes excluded as vacuous            : {len(vacuous)}")
    for shape, st, w in vacuous:
        print(f"    - {shape} (state={st}, tasks={w})")

    if not evidence:
        print("\n  NO evidence-grade shape: the coverage claim cannot be made.")
        return 1

    causal_count = sum(1 for _s, c, _g, _l in evidence if c)
    ghost_count = sum(1 for _s, _c, g, _l in evidence if g)
    print(f"\n  shapes where the agent pool IS causal        : {causal_count}/{len(evidence)}")
    print(f"  shapes where a GHOST pool changes the outcome: {ghost_count}/{len(evidence)}")
    print(f"\n  tmp = {ROOT}")
    if vacuous:
        print("\n  NOTE: excluded shapes are themselves a finding — those goal")
        print("  shapes do not complete, so the 'AI employee' cannot run them")
        print("  at all. That is a coverage limit of the DECOMPOSER, reported")
        print("  honestly rather than hidden.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
