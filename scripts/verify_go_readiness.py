#!/usr/bin/env python
"""UU-01 -- GO-readiness gate. SEPARATE from the P0-8 status matrix.

Why a separate gate
====================
verify_p08_final_status_matrix.py honestly reports the P0-8 chain statuses
(HC-01..HC-11). That report must NEVER be weakened or changed. But "the matrix
is green" is not the same as "we are GO to release". This script is the GO
gate: it separates "P0-8 matrix green" from "GO-ready", and it FAILS GO when
HC-01 is not COMPLIANT.

What GO requires (all must hold)
================================
1. HC-01 -- the authoritative audit-chain control -- must be COMPLIANT, not
   merely "not obviously broken". We reuse the matrix's OWN classification
   logic (probe_hc01 + classify) so we never re-implement the chain check and
   never drift from the matrix's verdict. HC-01 != COMPLIANT => GO fails.

2. DEPLOYMENT TRUTH -- the production deployment must actually arm kernel
   enforcement at exactly the CRITICAL tier. We parse the arming sites
   (docker-compose.prod.yml and, if present, scripts/build_cloud_bundle.py)
   for LIUHAO_KERNEL_POLICY_ENFORCE and require the resolved value to be
   exactly "CRITICAL" (NOT "HIGH,CRITICAL", not empty / OFF). This is a check
   of what the deployment *does*, independent of the test matrix. A scanned
   arming site that is not CRITICAL => GO fails.

Output
======
Always prints a GO_STATUS line that is exactly one of:
  GO_STATUS: COMPLIANT   (exit 0  -- GO)
  GO_STATUS: UNVERIFIED  (exit 1  -- HC-01 not COMPLIANT, specifically UNVERIFIED)
  GO_STATUS: FAILED      (exit 1  -- any other GO blocker)
plus a "HC-01: <status>" line and any deployment-truth findings.

Honest current state
====================
HC-01's persisted chain in audit_store.db is forked (the working tree ships a
broken join), so the real status is UNVERIFIED. This gate will therefore FAIL
today -- that is the correct, honest result, and it MUST NOT be forced green.
The unit tests inject the status via monkeypatch; they never touch the live
audit_store.db and never change the matrix's real classification.

Dependency surface: standard library plus the repository's own matrix module
(scripts/verify_p08_final_status_matrix.py) and, for the deployment-truth
cross-check, src/kernels/_enforcement.parse_spec when available.

Usage
=====
  python scripts/verify_go_readiness.py
"""
from __future__ import annotations

import importlib.util
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
MATRIX_SCRIPT = REPO_ROOT / "scripts" / "verify_p08_final_status_matrix.py"
ENV_VAR = "LIUHAO_KERNEL_POLICY_ENFORCE"
PROD_COMPOSE = REPO_ROOT / "docker-compose.prod.yml"
BUNDLE_BUILDER = REPO_ROOT / "scripts" / "build_cloud_bundle.py"

EXIT_FAIL = 1


# ---------------------------------------------------------------------------
# (1) HC-01 status -- reuse the matrix's own probe + classify; do NOT
#     re-implement the chain check.
# ---------------------------------------------------------------------------
def _load_matrix_module():
    """Load the P0-8 matrix module WITHOUT executing its __main__ (which would
    run every probe). Returns the module object so its functions can be called
    directly."""
    spec = importlib.util.spec_from_file_location(
        "liuhao_p08_matrix", str(MATRIX_SCRIPT)
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def determine_hc01_status() -> str:
    """Return HC-01's real status by reusing the matrix's probe + classify.

    This is the single source of truth for HC-01's status; the gate does not
    re-derive it. Monkeypatch this in tests to inject a status without touching
    the live audit_store.db.
    """
    mod = _load_matrix_module()
    mod.probe_hc01()
    status, _why = mod.classify("HC-01")
    return status


# ---------------------------------------------------------------------------
# (2) DEPLOYMENT TRUTH -- parse the arming sites, require exactly CRITICAL.
# ---------------------------------------------------------------------------
def _resolve_compose_spec(line: str) -> Optional[str]:
    """Resolve the effective default of ENV_VAR on a compose line.

    Handles:
      - LIUHAO_KERNEL_POLICY_ENFORCE=${VAR:-CRITICAL}   -> "CRITICAL"
      - LIUHAO_KERNEL_POLICY_ENFORCE=CRITICAL           -> "CRITICAL"
      - LIUHAO_KERNEL_POLICY_ENFORCE=${VAR}             -> "" (inherits env;
                                                              not deterministic)
    Returns the resolved spec string, or None if the line only *mentions* the
    variable without assigning it.
    """
    m = re.search(re.escape(ENV_VAR) + r"\s*[:=]\s*(\S+)", line)
    if not m:
        return None
    token = m.group(1).strip().strip('"').strip("'")
    # ${VAR:-default} -> the default is what applies when the env is unset.
    dm = re.search(r"\{\s*[^:}]+:-([^}]*)\s*\}", token)
    if dm:
        return dm.group(1).strip()
    # ${VAR} with no default -> inherits the caller's environment; not a
    # deterministic CRITICAL, so surface it as empty (handled as "not CRITICAL").
    if token.startswith("${") and ":-" not in token:
        return ""
    return token


def _resolve_python_spec(line: str) -> Optional[str]:
    """Resolve the effective spec from a Python arming line.

    Handles:
      os.environ.setdefault("LIUHAO_KERNEL_POLICY_ENFORCE", "HIGH,CRITICAL")
      os.environ["LIUHAO_KERNEL_POLICY_ENFORCE"] = "CRITICAL"
    Returns the spec (the value applied / defaulted), or None if the line only
    references the variable without assigning it.
    """
    m = re.search(
        r"""os\.environ\.setdefault\(\s*['"]"""
        + re.escape(ENV_VAR)
        + r"""['"]\s*,\s*['"]([^'"]*)['"]""",
        line,
    )
    if m:
        return m.group(1)
    m = re.search(
        r"""os\.environ\[\s*['"]""" + re.escape(ENV_VAR)
        + r"""['"]\s*\]\s*=\s*['"]([^'"]*)['"]""",
        line,
    )
    if m:
        return m.group(1)
    return None


def scan_arming_specs() -> Dict[str, Optional[str]]:
    """Map each arming site of ENV_VAR to its resolved default spec.

    Sites scanned:
      * docker-compose.prod.yml            (the production deployment manifest)
      * scripts/build_cloud_bundle.py      (if present -- the published bundle
                                             renders the same variable)
    """
    specs: Dict[str, Optional[str]] = {}
    if PROD_COMPOSE.is_file():
        for line in PROD_COMPOSE.read_text(encoding="utf-8").splitlines():
            if ENV_VAR in line:
                spec = _resolve_compose_spec(line)
                if spec is not None:
                    specs[PROD_COMPOSE.name] = spec
                    break
    if BUNDLE_BUILDER.is_file():
        for line in BUNDLE_BUILDER.read_text(encoding="utf-8").splitlines():
            if ENV_VAR in line:
                spec = _resolve_python_spec(line)
                if spec is not None:
                    specs[BUNDLE_BUILDER.name] = spec
                    break
    return specs


def deployment_arming_ok() -> Tuple[bool, List[str]]:
    """Return (ok, problems). GO requires every scanned arming site to resolve
    to exactly 'CRITICAL'."""
    problems: List[str] = []
    if not PROD_COMPOSE.is_file():
        problems.append(
            "docker-compose.prod.yml is missing -- no production deployment "
            "manifest defines the enforcement arming; GO cannot be verified"
        )
        return False, problems
    specs = scan_arming_specs()
    if not specs:
        problems.append(
            "no arming site for %s found in docker-compose.prod.yml or "
            "scripts/build_cloud_bundle.py" % ENV_VAR
        )
        return False, problems
    for site, spec in specs.items():
        if spec is None:
            problems.append(
                "%s: %s is assigned but its spec could not be parsed" % (site, ENV_VAR)
            )
            continue
        if spec == "":
            problems.append(
                "%s: %s resolves to OFF (empty / inherits runtime env) -- "
                "enforcement is not deterministically CRITICAL" % (site, ENV_VAR)
            )
            continue
        if spec != "CRITICAL":
            problems.append(
                "%s: %s resolves to %r, not 'CRITICAL' -- the GO gate requires "
                "the deployment to arm exactly the CRITICAL tier (not %r)"
                % (site, ENV_VAR, spec, spec)
            )
            continue
    return (len(problems) == 0, problems)


# ---------------------------------------------------------------------------
# Gate entry point
# ---------------------------------------------------------------------------
def main(argv: List[str] | None = None) -> int:
    print("UU-01 GO-READINESS GATE")
    print("Separates 'P0-8 matrix green' from 'GO-ready'. HC-01 must be")
    print("COMPLIANT and the deployment must arm enforcement at CRITICAL.")
    print("")

    # (1) HC-01 status -- reuse the matrix's own classification (fail-closed).
    try:
        hc01 = determine_hc01_status()
    except Exception as exc:  # never guess GO on an error
        print("GO_STATUS: FAILED")
        print("HC-01: ERROR (%s)" % exc)
        print("!! could not determine HC-01 status; cannot declare GO")
        print("   (missing dependency, or unreadable audit_store.db)")
        return EXIT_FAIL
    print("HC-01: %s" % hc01)

    # (2) deployment truth
    deploy_ok, deploy_problems = deployment_arming_ok()
    if not deploy_ok:
        print("DEPLOYMENT TRUTH: FAIL")
        for p in deploy_problems:
            print("  - %s" % p)

    # Decide GO_STATUS -- always exactly one of COMPLIANT / UNVERIFIED / FAILED.
    if hc01 != "COMPLIANT":
        go_status = "UNVERIFIED" if hc01 == "UNVERIFIED" else "FAILED"
        blocked = True
    elif not deploy_ok:
        go_status = "FAILED"
        blocked = True
    else:
        go_status = "COMPLIANT"
        blocked = False

    print("GO_STATUS: %s" % go_status)
    if blocked:
        print("")
        print("GO BLOCKED: do NOT promote / push / deploy.")
        return EXIT_FAIL
    print("")
    print("GO: release may proceed (HC-01 COMPLIANT, deployment arms CRITICAL).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
