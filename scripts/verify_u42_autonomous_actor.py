#!/usr/bin/env python3
"""verify_u42_autonomous_actor.py -- static guardrail for the U42 invariant.

U42 (authorization integrity): the autonomous actor must inherit the
default-deny kernel, and every ``WorldInterface`` construction must set an
explicit ``actor=`` so it can never silently fall back to the human
default-allow posture (human sovereignty is intentional at the human boundary,
but the autonomous surface must default-deny).

This is the *static* half of U42. The behavioural half is covered by
``tests/test_world_interface_autonomous.py`` (proves actor="autonomous" is
default-deny) and ``tests/test_world_interface_actor_wiring.py`` (fails CI if
any production ``WorldInterface(`` omits ``actor=``). This script adds the
check those tests do NOT perform:

  (i)  the default-deny kernel anchors must exist -- if the central autonomous
       execution fence or the ``WorldInterface`` deny line is removed, the gate
       is gone and we must fail loudly, not assume it still holds;
  (ii) every production ``WorldInterface(`` construction must carry an explicit
       ``actor=`` (a static CI gate that survives even if the behavioural pytest
       is skipped/disabled);
  (iii) every ``WorldInterface(actor="autonomous")`` site must route through the
        gated ``.execute()`` / ``.observe()`` path.

Exit 0 = invariant holds. Exit 1 = violation. Honest: it only checks what it
claims; it does not re-implement the runtime deny decision.
"""
from __future__ import annotations

import re
import sys
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))  # required by tests/test_guardrail_scripts.py

SRC = REPO / "src"

# A real WorldInterface *construction* call (not the class def, not a subclass
# name like SomeWorldInterface, not a method definition).
WI_CONSTRUCT_RE = re.compile(r"(?<!class )(?<![A-Za-z_])WorldInterface\s*\(")
ACTOR_RE = re.compile(r"actor\s*=")

# Autonomous sites must route through the gated path.
AUTON_RE = re.compile(
    r'(?<!class )(?<![A-Za-z_])WorldInterface\s*\([^)]*actor\s*=\s*["\']autonomous["\']'
)
GATED_METHOD_RE = re.compile(r"\.execute\s*\(|\.observe\s*\(")

# The default-deny kernel anchors (must exist; if gone, the gate is gone).
WI_FILE = SRC / "ai" / "world_interface.py"
EXEC_GATE_RE = re.compile(r"executor_session\s*\(")
DENY_LINE_RE = re.compile(r'return self\._actor != "autonomous"')
FENCE_FILE = SRC / "kernels" / "_crosscutting.py"
FENCE_GATE_RE = re.compile(r"Default-DENY autonomous execution gate")
FENCE_ENFORCE_RE = re.compile(r"_enforce_executor_fence_at_gate")


def _src_py_files():
    for p in SRC.rglob("*.py"):
        # Skip any test file or directory (we only guard production code).
        if "test" in p.parts or p.parent.name.startswith("test"):
            continue
        yield p


def _call_span(text: str, open_pos: int) -> str:
    """Return the substring from ``text[open_pos]`` (a ``(``) to its matching
    ``)``, respecting nested parentheses and spanning newlines. If the call is
    unterminated, return everything to end of text (so a missing close is not
    silently ignored)."""
    depth = 0
    i = open_pos
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return text[open_pos:i + 1]
        i += 1
    return text[open_pos:]


def main() -> int:
    failures = []

    # (i) default-deny kernel anchors must exist.
    if WI_FILE.is_file():
        wit = WI_FILE.read_text(encoding="utf-8", errors="replace")
        if not (EXEC_GATE_RE.search(wit) and DENY_LINE_RE.search(wit)):
            failures.append(
                "src/ai/world_interface.py: autonomous default-deny kernel anchors "
                "missing (executor_session gate or `return self._actor != \"autonomous\"`)"
            )
    else:
        failures.append("src/ai/world_interface.py missing")
    if FENCE_FILE.is_file():
        ft = FENCE_FILE.read_text(encoding="utf-8", errors="replace")
        if not (FENCE_GATE_RE.search(ft) and FENCE_ENFORCE_RE.search(ft)):
            failures.append(
                "src/kernels/_crosscutting.py: central autonomous execution fence gate "
                "missing (Default-DENY autonomous execution gate / "
                "_enforce_executor_fence_at_gate)"
            )
    else:
        failures.append("src/kernels/_crosscutting.py missing")

    # (ii) every production WorldInterface( construction must carry explicit actor=.
    # We scan the *entire call span* (balanced parens, across newlines) so a
    # multiline construction like ``WorldInterface(adapters=[...], actor="...")``
    # is not falsely flagged for lacking actor= on the first line.
    for p in _src_py_files():
        text = p.read_text(encoding="utf-8", errors="replace")
        for m in WI_CONSTRUCT_RE.finditer(text):
            line_no = text[: m.start()].count("\n") + 1
            span = _call_span(text, m.end() - 1)  # m.end()-1 is the "("
            if not ACTOR_RE.search(span):
                failures.append(
                    "%s:%d: WorldInterface( construction without an explicit actor= "
                    "(U42 -- autonomous surface must default-deny)"
                    % (p.relative_to(REPO), line_no)
                )

    # (iii) autonomous sites must use the gated execute/observe path.
    for p in _src_py_files():
        text = p.read_text(encoding="utf-8", errors="replace")
        if AUTON_RE.search(text) and not GATED_METHOD_RE.search(text):
            failures.append(
                "%s: WorldInterface(actor='autonomous') site does not use the gated "
                ".execute()/.observe() path (U42)" % p.relative_to(REPO)
            )

    if failures:
        print("U42 VIOLATIONS:")
        for f in failures:
            print(" - " + f)
        return 1
    print(
        "U42 OK: autonomous default-deny kernel anchors present; all production "
        "WorldInterface( constructions set an explicit actor=."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
