#!/usr/bin/env python
"""C1 -- machine-verifiable HOLD control for CI/CD release gating.

Background
==========
LIUHAO_HOLD used to be a human-discipline note (see
docs/adr/ADR-HOLD-SCOPE.md, section 2.4): the env gate was "recommended, not
mandatory". That is not good enough for a release / promotion freeze -- a human
forgetting to check a note is exactly how a bad build reaches production. This
script turns the note into a MACHINE-VERIFIABLE control: CI calls it as the
first step of any release / deploy job, and a truthy LIUHAO_HOLD hard-aborts
the job (no registry push, no deploy, no promotion) with a non-zero exit and an
explicit, auditable message.

Semantics (closed; no silent default)
=====================================
The variable is read via os.environ.get("LIUHAO_HOLD").

  * UNSET                 -> HOLD inactive. Release MAY proceed. Exit 0.
  * ""  (empty)           -> HOLD inactive. Exit 0.
  * "0", "false", "off"   -> HOLD inactive (explicit opt-out). Exit 0.
  * any other value       -> HOLD ACTIVE. Release BLOCKED. Exit 2.
    ("1", "true", "yes",
     "on", or any
     non-empty string)

The default (unset) is explicit and auditable: the script ALWAYS prints what
was decided and from which value. There is no hidden "maybe" state.

This is an OPERATIONAL safeguard, not a security boundary (per ADR-HOLD-SCOPE
section 2.4). It complements -- never replaces -- the code-level freeze and the
human_sovereignty_override channel.

Dependency surface: standard library only (os, sys).

Usage
=====
  python scripts/check_hold_gate.py
  LIUHAO_HOLD=1 python scripts/check_hold_gate.py   # exit 2, release blocked
"""
from __future__ import annotations

import os
import sys

ENV_VAR = "LIUHAO_HOLD"

# Explicitly-falsy values: an operator who set the var *meant* to disable HOLD.
_FALSEY = {"", "0", "false", "off"}

# A non-zero exit code distinct from a generic crash, so CI logs make the
# reason obvious ("release blocked by HOLD") instead of a mystery failure.
EXIT_BLOCKED = 2


def _shown(raw: str | None) -> str:
    """Human-readable rendering of the raw env value for the auditable line."""
    if raw is None:
        return "unset"
    if raw == "":
        return "''"
    return raw


def evaluate(raw_value: str | None) -> tuple[bool, str]:
    """Decide HOLD state from the raw env value.

    Returns (is_active, auditable_message). Pure and importable so the decision
    logic can be unit-tested without spawning a subprocess.
    """
    if raw_value is None:
        return False, (
            "HOLD inactive (LIUHAO_HOLD=%s): release may proceed (default)"
            % _shown(raw_value)
        )
    value = raw_value.strip()
    if value.lower() in _FALSEY:
        return False, (
            "HOLD inactive (LIUHAO_HOLD=%s): release may proceed" % _shown(raw_value)
        )
    return True, (
        "HOLD active (LIUHAO_HOLD=%s): release blocked" % _shown(raw_value)
    )


def main(argv: list[str] | None = None) -> int:
    raw = os.environ.get(ENV_VAR)
    active, message = evaluate(raw)
    print(message)
    return EXIT_BLOCKED if active else 0


if __name__ == "__main__":
    raise SystemExit(main())
