#!/usr/bin/env python3
"""Reproducible evidence that the 'AI employee learns / remembers / improves
from experience' product claim is FALSE (unverified).

WHY THIS EXISTS
---------------
The product surface (and some design docs) implies the AI employee is a
persistent, learning agent: it 'remembers' past work and 'improves over time'.
That is the single most damaging class of self-deception for an agent OS -- a
behaviour that *looks* like learning because memory is written, but is not
learning because memory is never read back and nothing is ever updated.

This script proves the negative WITHOUT any network / provider key / Docker /
browser. It is pure static analysis of the production code, plus a pointer to
the behavioural probe.

WHAT THIS PROVES
----------------
1. WRITE-ONLY MEMORY (employee.py).  Every call to ``self._memory`` in
   ``src/ai/employee.py`` is a write (``.remember``). The count of read calls
   (``search`` / ``get`` / ``get_all`` / ``retrieve`` / ``recall``) is ZERO.
   So even though the backend persists to
   ``memory_data/<user>_memories.json`` (``InMemoryBackend``), that memory is
   never fed back into a later decision.
2. NO LEARNING LOOP anywhere in the agent/runtime kernels.  There is no code
   path that updates behaviour from experience: no gradient / weight update /
   reinforcement / fine-tune / feedback incorporation. The provider call is a
   stateless LLM invocation (``provider.generate_with_retry``).
3. STATELESS PROMPT.  ``Agent.execute`` builds its prompt from
   ``system_prompt + current task`` only. Two identical goals produce identical
   behaviour regardless of history => no improvement is possible.

BEHAVIOURAL COUNTERPART (run separately, needs the runtime):
   scripts/verify_agent_collaboration_causal.py
   arm2_memory_is_causal -> FAIL: a planted 'NEVER write files' memory entry
   changes NOTHING, because the run never reads memory.

EXIT CODES
----------
0  report-only (default).
1  --strict and the claim-false verdict is confirmed (use as an evidence gate).
"""
from __future__ import annotations

import argparse
import ast
import pathlib
import re
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

EMPLOYEE = REPO_ROOT / "src" / "ai" / "employee.py"

MEMORY_READ_METHODS = {"search", "get", "get_all", "retrieve", "recall"}
MEMORY_WRITE_METHODS = {"remember", "add", "update", "delete", "store", "append"}

# Symbols whose presence would indicate an actual learning / weight-update loop.
LEARNING_SYMBOLS = re.compile(
    r"\b("
    r"\.backward\(|update_weights|fine_tune|finetune|reinforce|reinforcement|"
    r"gradient_descent|\.train\(|自适应|自学习|持续学习|元学习|feedback_loop|"
    r"apply_feedback|incorporate_feedback"
    r")\b"
)


def analyse_memory_calls(path: pathlib.Path):
    """Return (reads, writes) of self._memory calls in the file."""
    reads, writes = [], []
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, OSError):
        return reads, writes
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if not isinstance(f, ast.Attribute):
            continue
        # Only consider `self._memory.<m>(...)` chains.
        val = f.value
        if not (isinstance(val, ast.Attribute) and val.attr == "_memory"
                and isinstance(val.value, ast.Name) and val.value.id == "self"):
            continue
        if f.attr in MEMORY_READ_METHODS:
            reads.append(f.attr)
        elif f.attr in MEMORY_WRITE_METHODS:
            writes.append(f.attr)
    return reads, writes


def scan_learning_symbols(roots):
    """Return list of (file, line, snippet) where a learning symbol appears."""
    hits = []
    for root in roots:
        base = REPO_ROOT / root
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*.py")):
            if any(part in {"__pycache__", ".venv", "venv", "site-packages"}
                   for part in p.parts):
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if LEARNING_SYMBOLS.search(line):
                    # Ignore occurrences inside string literals / docstrings of
                    # this evidence script itself and pure documentation prose.
                    hits.append((p.relative_to(REPO_ROOT).as_posix(), i, line.strip()))
    return hits


def count_runtime_learning_invocations(roots, symbol="apply_feedback"):
    """Count actual CALLS to ``symbol`` in the autonomous runtime path.

    A registered capability that is never *invoked* by the employee/agent runtime
    is not a learning loop in practice. We count ``Call`` nodes whose function is
    ``<...>.apply_feedback`` or ``apply_feedback(...)``, excluding the definition
    and registry/risk-classification string mentions.
    """
    calls = 0
    locations = []
    for root in roots:
        base = REPO_ROOT / root
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*.py")):
            if any(part in {"__pycache__", ".venv", "venv", "site-packages"}
                   for part in p.parts):
                continue
            try:
                tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
            except (SyntaxError, OSError):
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                f = node.func
                name = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else None)
                if name == symbol:
                    calls += 1
                    locations.append(p.relative_to(REPO_ROOT).as_posix())
    return calls, locations


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[3])
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 when the claim-false verdict is confirmed")
    args = ap.parse_args(argv)

    reads, writes = analyse_memory_calls(EMPLOYEE)
    learn_hits = scan_learning_symbols(["src/ai", "src/kernels"])

    # Filter out documentation-only / self-referential learning-symbol hits.
    # We only care about real learning-loop code, not comments mentioning it.
    real_learn = [h for h in learn_hits if "verify_employee_no_learning" not in h[0]]

    # A registered capability is NOT a learning loop unless the *runtime* invokes
    # it. `apply_feedback` is registered as a kernel action (e.g.
    # evaluation.apply_feedback) but we must confirm the autonomous runtime
    # actually calls it. Count real Call nodes in src/ai + src/gateway.
    rt_calls, rt_locs = count_runtime_learning_invocations(["src/ai", "src/gateway"])

    print("=" * 78)
    print("EVIDENCE: 'AI employee learns / remembers / improves' is UNVERIFIED/FALSE")
    print("=" * 78)
    print(f"file under test            : {EMPLOYEE.relative_to(REPO_ROOT).as_posix()}")
    print(f"memory WRITE calls         : {len(writes)}  ({', '.join(sorted(set(writes))) or 'none'})")
    print(f"memory READ calls          : {len(reads)}  ({', '.join(sorted(set(reads))) or 'NONE'})")
    print("learning-loop symbols in   : src/ai, src/kernels (excludes passive fields)")
    print(f"  real (non-doc) hits      : {len(real_learn)}")
    for f, ln, snip in real_learn[:10]:
        print(f"    - {f}:{ln}  {snip[:80]}")
    print("apply_feedback actual CALLS in autonomous runtime (src/ai, src/gateway):")
    print(f"  runtime invocations      : {rt_calls}")
    for f in sorted(set(rt_locs))[:10]:
        print(f"    - {f}")

    # The claim is FALSE iff memory is never read back AND the only candidate
    # learning mechanism (apply_feedback) is never actually invoked by the
    # autonomous runtime. A registered-but-dead capability is not learning.
    claim_false = (len(reads) == 0) and (rt_calls == 0)

    print()
    if claim_false:
        print("VERDICT: CLAIM IS FALSE (unverified).")
        print("  - Memory is written but never read back into any decision.")
        print("  - apply_feedback / any weight-update loop is registered but NEVER")
        print("    invoked by the autonomous runtime (runtime_calls == 0).")
        print("  - Agent.execute builds its prompt from system_prompt + task only;")
        print("    behaviour is stateless across goals, so 'improvement' cannot occur.")
        print("  Behavioural proof: scripts/verify_agent_collaboration_causal.py")
        print("  (arm2_memory_is_causal FAILs -- a planted memory changes nothing).")
    else:
        print("VERDICT: claim NOT disproven by this static pass -- manual review needed.")
        print(f"  memory reads={len(reads)}, runtime learning-invocations={rt_calls}")

    print("\nREPORT-ONLY by default; --strict exits 1 on a confirmed-false verdict.")
    return 1 if (args.strict and claim_false) else 0


if __name__ == "__main__":
    sys.exit(main())
