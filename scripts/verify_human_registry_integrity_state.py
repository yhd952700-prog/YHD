#!/usr/bin/env python
"""Deployment verification: human-identity registry integrity posture.

PHASE 3.6 / P0-3 (boss decision 2026-09-22, ids 9c1k2m / 9r0n4s / 6t0p1h): when a
human registry is in use but ``LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY`` is unset,
the registry is DEGRADED / UNVERIFIED and must be observable, auditable and
deployment-gate aware. The previous warn-only behaviour (missing key -> warning
-> system appears fully sovereign) is forbidden.

This script surfaces that posture. It exits 0 with a machine-readable report so a
deployment gate can read it; it only exits non-zero when an operator has opted
into a hard fail via ``LIUHAO_ENFORCE_HUMAN_REGISTRY_INTEGRITY=1`` -- that keeps
existing deployments (which legitimately have no key yet) from being broken by
the honesty requirement while still letting a strict gate enforce it.

Escalated 2026-09-25 (HC-11 / U6): verification without a key is no longer
"degraded but rows still admitted" -- it **cannot be performed**, so every row is
refused and the registry holds zero humans. The report therefore carries
``refused_rows`` as well as ``admitted_humans``, because those two states look
identical in the ``integrity_state`` summary (``not_applicable``) and must not be
confused: an empty registry is a configuration choice, a fully-refused registry
is an outage.

Exit codes:
  0  posture reported (healthy / not_applicable), or rows were refused /
     integrity degraded but hard-fail was not requested -- the report itself is
     the deployment-gate signal.
  1  integrity degraded OR rows refused while
     LIUHAO_ENFORCE_HUMAN_REGISTRY_INTEGRITY=1 (operator opted into hard-fail).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.kernels.identity._persistence import (  # noqa: E402
    HUMAN_IDENTITIES_INTEGRITY_KEY_ENV,
    registry_integrity_state,
    resolve_human_identity_store,
)


def main() -> int:
    store = resolve_human_identity_store()
    # Populates ``last_load_report["admitted"]`` so the posture is honest about
    # whether there are humans whose integrity could be attacked.
    store.load_all(include_extended=True)
    last = getattr(store, "last_load_report", {}) or {}
    admitted = last.get("admitted", 0)
    rejected = list(last.get("rejected") or [])
    state = registry_integrity_state(
        admitted=admitted, configured=bool(store.location)
    )
    report = {
        "backend": store.backend_name,
        "location": store.location,
        "configured": bool(store.location),
        "admitted_humans": admitted,
        #: Rows the store refused. Non-empty here means the registry is NOT
        #: merely empty -- it holds rows that could not be authenticated, i.e.
        #: "nobody can log in or approve" rather than "no humans registered".
        #: Reporting this is what keeps the fail-closed escalation from hiding
        #: behind ``integrity_state: not_applicable``.
        "refused_rows": rejected,
        "integrity_enforced": bool(
            os.environ.get(HUMAN_IDENTITIES_INTEGRITY_KEY_ENV)
        ),
        "integrity_state": state,
    }
    print(json.dumps(report, indent=2))

    if rejected:
        print(
            "\n[FAIL-CLOSED] %d registry row(s) in %r were REFUSED: %s"
            % (len(rejected), store.location, ", ".join(map(repr, rejected)))
        )
        print(
            "  The system therefore recognises no registered human -- nobody "
            "can log in and nobody can approve."
        )
        print(
            "  Remedy: set %s, then re-register each human with "
            "scripts/register_human_identity.py so every row carries a valid "
            "tag. A row cannot be authenticated retroactively."
            % HUMAN_IDENTITIES_INTEGRITY_KEY_ENV
        )
        if os.environ.get("LIUHAO_ENFORCE_HUMAN_REGISTRY_INTEGRITY") == "1":
            print("  LIUHAO_ENFORCE_HUMAN_REGISTRY_INTEGRITY=1 -> hard-fail.")
            return 1

    if state == "degraded_unverified":
        print(
            "\n[DEGRADED] A human registry is in use but %s is unset."
            % HUMAN_IDENTITIES_INTEGRITY_KEY_ENV
        )
        print(
            "  Rows that decide who holds sovereignty are attacker-writable."
        )
        print(
            "  Set the key (HMAC-SHA256, fail-closed) or accept the risk "
            "explicitly."
        )
        if os.environ.get("LIUHAO_ENFORCE_HUMAN_REGISTRY_INTEGRITY") == "1":
            print(
                "  LIUHAO_ENFORCE_HUMAN_REGISTRY_INTEGRITY=1 -> hard-fail."
            )
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
