#!/usr/bin/env python3
"""Roster "real employees" verification (LIUHAO AI-OS).

Proves (NO network call) that the dashboard roster endpoint:

  A. Surfaces the REAL persisted employees from ``EmployeeStore`` as
     ``real_employees`` + ``totals.employees`` — NOT kernel/layer modules.
  B. Kernel/能力层 modules are kept as registry info only (``kernels`` /
     ``layers`` preserved), and are NOT counted as employees.
  C. Modules are NOT smuggled into ``real_employees``.

The pure builder ``build_roster_payload`` lives in
``src/gateway/roster_payload.py`` (fastapi-free) so this verifier can import
it under the system python (fastapi is NOT installed there). The employee
store + workspace are REDIRECTED to a temp dir so nothing writes to the real
workspace and the frozen HC-01 live store is NEVER touched.

Environment note: the registry portion (``kernels``/``layers`` 14/14) requires
``PyYAML``, which is only present in the project venv, not the bare system
python. When ``yaml`` is importable we assert the exact 14/14 counts; when it
is not, we still verify the core contract (real employees surfaced, modules not
counted as employees) and report the registry check as skipped. Either way the
verifier exits 0 = PASS / 1 = FAIL.

Exit code 0 = PASS, 1 = FAIL.
"""

import os
import sys
import tempfile

# --- make the repo root importable regardless of which python runs this ------
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# --- isolate from the real workspace: REDIRECT employee store + workspace -----
_TMP = tempfile.mkdtemp(prefix="liuhao_roster_verify_")
os.environ["LIUHAO_WORKSPACE_ROOT"] = _TMP

from src.ai.roster_payload import build_roster_payload
from src.ai.employee_store import EmployeeStore


def _check(label, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {label}{(' -- ' + detail) if detail else ''}")
    return cond


def _has_yaml() -> bool:
    try:
        import yaml  # noqa: F401
        return True
    except Exception:
        return False


def main():
    failures = 0

    print("=== A. seed a real persisted employee ===")
    store = EmployeeStore()
    emp = store.create_employee("roster-verify-emp", agent_count=2,
                                enable_observability=False)
    failures += 0 if _check("employee seeded",
                             emp is not None and emp.name == "roster-verify-emp") else 1
    failures += 0 if _check("store file written on seed",
                             os.path.exists(store.path), store.path) else 1

    print("\n=== B. build roster payload (real code) ===")
    payload = build_roster_payload()

    real_employees = payload.get("real_employees", [])
    totals = payload.get("totals", {})
    kernels = payload.get("kernels", [])
    layers = payload.get("layers", [])

    seeded = store.list_employees()
    failures += 0 if _check("real_employees present in payload",
                             isinstance(real_employees, list)) else 1
    failures += 0 if _check("real_employees non-empty",
                             len(real_employees) > 0,
                             f"count={len(real_employees)}") else 1
    failures += 0 if _check(
        "totals.employees == number of seeded employees",
        totals.get("employees") == len(seeded),
        f"totals.employees={totals.get('employees')}, seeded={len(seeded)}") else 1
    failures += 0 if _check(
        "real_employees count == seeded count",
        len(real_employees) == len(seeded),
        f"real={len(real_employees)}, seeded={len(seeded)}") else 1

    print("\n=== C. seeded employee correctly represented ===")
    if real_employees:
        re = real_employees[0]
        failures += 0 if _check("name matches seeded employee",
                                 re.get("name") == "roster-verify-emp",
                                 f"name={re.get('name')}") else 1
        failures += 0 if _check("agent_count == 2",
                                 re.get("agent_count") == 2,
                                 f"agent_count={re.get('agent_count')}") else 1
        failures += 0 if _check("agents list has 2 entries with status",
                                 len(re.get("agents") or []) == 2 and all(
                                     "status" in a for a in re.get("agents") or []),
                                 f"agents={len(re.get('agents') or [])}") else 1
        failures += 0 if _check(
            "task counters present (submitted/completed/failed)",
            {"total_tasks_submitted", "total_tasks_completed",
             "total_tasks_failed"} <= set(re.keys())) else 1

    print("\n=== D. modules NOT counted as employees ===")
    failures += 0 if _check(
        "totals.employees != kernels+layers (modules not miscounted)",
        totals.get("employees") != (totals.get("kernels", 0) + totals.get("layers", 0)),
        f"employees={totals.get('employees')}, "
        f"kernels+layers={totals.get('kernels',0)+totals.get('layers',0)}") else 1
    # Stronger: no real_employee name may collide with a kernel/layer id.
    kernel_ids = {k.get("id") for k in kernels}
    layer_ids = {l.get("id") for l in layers}
    collisions = [re.get("name") for re in real_employees
                  if re.get("name") in kernel_ids or re.get("name") in layer_ids]
    failures += 0 if _check("no real_employee is a kernel/layer module",
                             not collisions, f"collisions={collisions}") else 1

    print("\n=== E. registry info preserved (kernels/layers) ===")
    failures += 0 if _check("kernels key present", "kernels" in payload) else 1
    failures += 0 if _check("layers key present", "layers" in payload) else 1
    failures += 0 if _check("totals.registry_entries preserved",
                             totals.get("registry_entries") ==
                             totals.get("kernels", 0) + totals.get("layers", 0),
                             f"registry_entries={totals.get('registry_entries')}") else 1
    failures += 0 if _check("other UI fields present",
                             all(k in payload for k in
                                 ("available", "version", "domains", "bands",
                                  "providers"))) else 1

    if _has_yaml():
        failures += 0 if _check("registry available (yaml present)",
                                 bool(payload.get("available")),
                                 f"available={payload.get('available')}") else 1
        failures += 0 if _check("kernels == 14",
                                 len(kernels) == 14, f"kernels={len(kernels)}") else 1
        failures += 0 if _check("layers == 14",
                                 len(layers) == 14, f"layers={len(layers)}") else 1
        failures += 0 if _check("totals.kernels == 14",
                                 totals.get("kernels") == 14,
                                 f"kernels={totals.get('kernels')}") else 1
        failures += 0 if _check("totals.layers == 14",
                                 totals.get("layers") == 14,
                                 f"layers={totals.get('layers')}") else 1
    else:
        print("  [WARN] PyYAML not importable in this interpreter — "
              "registry 14/14 counts NOT asserted here.")
        print("         (run under the project venv, which has PyYAML, "
              "for the full registry check.)")
        print("  [PASS] core contract (real employees surfaced, "
              "modules not counted) verified without registry file.")

    # Cleanup the redirect store so nothing lingers.
    try:
        if os.path.exists(store.path):
            os.remove(store.path)
    except OSError:
        pass

    print()
    if failures:
        print(f"RESULT: FAIL ({failures} check(s) failed)")
        return 1
    print("RESULT: PASS -- roster surfaces real employees; "
          "modules no longer miscounted as employees")
    print(f"  (employee store isolated at {store.path}; "
          "HC-01 frozen store untouched)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
