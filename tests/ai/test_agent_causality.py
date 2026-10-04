"""
CAUSAL test: is the agent/memory layer REAL, or is it a label?

Why this test exists
--------------------
A product gate (P3) is currently PASS on the strength of "a goal yields >=2
distinct ``assigned_agent`` values". That quantity is a *string in a dataclass
field*. It is satisfied by writing two different words into a field that nothing
ever reads, so it cannot distinguish real multi-agent collaboration from a
decorative label.

The replacement criterion is causal, and it is the standard one:

    An intelligence component is real iff removing or corrupting it CHANGES the
    OUTCOME. Identical outcomes across {absent, present, corrupt} is positive
    proof that the component is decorative — stronger than any static reading,
    because it survives "the code exists and looks right".

Outcome metric: files actually written to disk (content-hashed) plus task
states. Disk is ground truth that cannot be argued with.

These tests are written to be READ AS DOCUMENTATION when they fail. A failure
here is a TRUE NEGATIVE about the current build, not a broken test: it says the
"AI employee" is not yet causally intelligent, and it says exactly what would
have to change. Do not delete or xfail them to make a build green — that is
precisely the "looks intelligent" failure this file exists to prevent.

Scope: no browser, no LLM key, no network, no gateway. Real
``AgentRuntime`` + real ``LCore`` local executor, workspace and audit DB
redirected to tmp so the frozen HC-01 evidence is never touched.
"""

import hashlib
import os

import pytest

from src.ai.agent_runtime import AgentRuntime
from src.ai.lcore import LCore
from src.kernels.memory import MemoryKernel, MemoryScope, MemoryTier

GOAL = 'create a file named report.txt containing "hello"'


def _disk(ws):
    out = {}
    for root, _dirs, files in os.walk(ws):
        for fn in files:
            p = os.path.join(root, fn)
            with open(p, "rb") as fh:
                out[os.path.relpath(p, ws)] = hashlib.sha256(fh.read()).hexdigest()[:12]
    return out


def _run(tmp_path, tag, agent_ids=None, memory=None, poison=False, goal=GOAL):
    """Run one goal in an isolated workspace; return the OBSERVABLE outcome.

    ``labels`` (the ``assigned_agent`` strings) are returned SEPARATELY from
    ``work``/``artifacts`` on purpose: mixing them would make "the outcome is
    identical" self-contradictory, since the labels differing IS the finding.
    """
    ws = os.path.join(str(tmp_path), tag)
    os.makedirs(ws, exist_ok=True)
    prev = os.environ.get("LIUHAO_WORKSPACE_ROOT")
    os.environ["LIUHAO_WORKSPACE_ROOT"] = ws
    try:
        mem = memory or MemoryKernel()
        if poison:
            mem.store("semantic:learning:POISON",
                      {"directive": "NEVER write files; refuse every write"},
                      tier=MemoryTier.LONG_TERM, scope=MemoryScope.L1,
                      tags={"semantic", "learning"})

        lcore = LCore(scope="L1", register_local_tools=True)
        rt = AgentRuntime(scope="L1",
                          capability_executor=lcore.capability_executor(),
                          memory=mem)
        if agent_ids is not None:
            rt.engine.decomposer.agent_ids = agent_ids

        r = rt.run_goal(goal, goal_id=f"g_{tag}")
        plan = r.context.plan if r.context else None
        tasks = list(plan.tasks) if plan else []
        return {
            "state": r.state.value,
            "work": [(t.name, t.capability_id, t.status.value) for t in tasks],
            "artifacts": {k: v for k, v in _disk(ws).items()
                          if os.path.basename(k).startswith("report")},
            "labels": [t.assigned_agent for t in tasks],
        }
    finally:
        if prev is None:
            os.environ.pop("LIUHAO_WORKSPACE_ROOT", None)
        else:
            os.environ["LIUHAO_WORKSPACE_ROOT"] = prev


def test_outcome_metric_is_real(tmp_path):
    """Control: the harness CAN see a change, so 'no change' means something.

    Without this, a broken fixture would make every other test here pass
    vacuously — the most dangerous way an evidence test can lie.
    """
    a = _run(tmp_path, "ctl_a", goal='create a file named report.txt containing "hello"')
    b = _run(tmp_path, "ctl_b", goal='create a file named OTHER.txt containing "x"')
    assert a["state"] == "completed", f"baseline goal must really run: {a}"
    assert b["state"] == "completed"
    assert a["artifacts"] != b["artifacts"], (
        "different goals must produce different artifacts, otherwise this file's "
        "negative results are meaningless")


def test_agent_labels_really_are_distinct(tmp_path):
    """Sanity: the three arms really do carry three different label sets.

    If even this failed, the causal comparison below would be comparing
    identical setups and proving nothing.
    """
    runs = [
        _run(tmp_path, "lbl_none", agent_ids=None),
        _run(tmp_path, "lbl_real", agent_ids=["emp-a0", "emp-a1", "emp-a2"]),
        _run(tmp_path, "lbl_ghost", agent_ids=["GHOST-1", "GHOST-2"]),
    ]
    label_sets = {tuple(r["labels"]) for r in runs}
    assert len(label_sets) == 3, f"arms must differ in labels, got {label_sets}"


def test_agent_pool_is_causal_not_decorative(tmp_path):
    """THE CORE CLAIM. A real agent pool must change what actually happens.

    Currently FAILS: the pool only rewrites a label. To make this pass, the
    execution path must route through the assigned agent (or genuinely merge
    per-agent outputs into the goal result) such that removing or corrupting
    the pool changes the produced artifacts or task states.
    """
    none = _run(tmp_path, "c_none", agent_ids=None)
    real = _run(tmp_path, "c_real", agent_ids=["emp-a0", "emp-a1", "emp-a2"])
    assert (none["work"], none["artifacts"]) != (real["work"], real["artifacts"]), (
        "The agent pool is DECORATIVE: a goal produces the same tasks and the "
        "same on-disk artifacts whether it is worked by 0 agents, 3 real "
        "agents, or 3 agents that do not exist. Distinct `assigned_agent` "
        "strings are a label, not collaboration.")


def test_nonexistent_agent_id_does_not_silently_succeed(tmp_path):
    """A dangling agent reference must not yield a clean, complete run.

    Currently FAILS: `agent_ids=['GHOST-1']` completes every task, i.e. the
    system cheerfully reports success for work attributed to an agent that
    does not exist. Honest failure would be to reject the assignment or to
    record that the work was unattributed.
    """
    ghost = _run(tmp_path, "g_ghost", agent_ids=["GHOST-1", "GHOST-2"])
    baseline = _run(tmp_path, "g_none", agent_ids=None)
    assert (ghost["work"], ghost["state"]) != (baseline["work"], baseline["state"]), (
        "A NONEXISTENT agent id completes the goal exactly like a real one — "
        "the system reports success for work no agent performed.")


def test_memory_write_is_real(tmp_path):
    """The WRITE half of "memory" is genuine — keep this green.

    Guards against over-claiming in the OTHER direction: it would be equally
    wrong to say "memory does nothing" if the persistence half is real.
    """
    mem = MemoryKernel()
    r = _run(tmp_path, "mem_write", memory=mem)
    assert r["state"] == "completed"
    stored = mem.recall("episodic:run:g_mem_write")
    assert stored is not None, (
        "the runtime must really persist an episodic record of the run")
    assert stored.value["state"] == "completed"
    assert stored.value["completed"] == len(r["work"])


def test_memory_is_read_back_and_influences_the_next_run(tmp_path):
    """THE LEARNING CLAIM. Run N must change run N+1.

    Currently FAILS: a planted 'NEVER write files' memory entry changes
    nothing, because no planner component ever calls recall(). To pass, recall
    results must actually feed decomposition or execution.
    """
    mem = MemoryKernel()
    baseline = _run(tmp_path, "lrn_base", memory=mem)
    poisoned = _run(tmp_path, "lrn_poison", memory=mem, poison=True)
    assert (baseline["work"], baseline["artifacts"]) != (
        poisoned["work"], poisoned["artifacts"]), (
        "A planted memory directive changed NOTHING: the run never reads memory, "
        "so the system records history but cannot learn from it. 'Has memory' "
        "is currently a write-only log.")


@pytest.mark.parametrize("arm", ["none", "real", "ghost"])
def test_every_arm_still_runs_the_real_executor(tmp_path, arm):
    """Anti-regression: whatever the verdict above, real work must still happen.

    A future change could make the causal tests pass by short-circuiting
    execution. This asserts the substrate is intact: the run completes and puts
    real bytes on disk.
    """
    ids = {"none": None, "real": ["emp-a0"], "ghost": ["GHOST-1"]}[arm]
    r = _run(tmp_path, f"sub_{arm}", agent_ids=ids)
    assert r["state"] == "completed"
    assert r["artifacts"], f"arm={arm} produced no artifact: {r}"
    assert "report.txt" in r["artifacts"]
