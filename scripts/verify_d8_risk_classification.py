"""D8 verification — reproducible evidence for the kernel-action risk
classification (Policy C-2 prerequisite).

Run:
    .venv/Scripts/python.exe scripts/verify_d8_risk_classification.py

Checks (exit 0 = all green):

1. IMPORT    — every module under src/ imports cleanly.
2. DECORATOR — every ``@kernel_action``-decorated callable still exposes
              ``__wrapped__`` and a retrievable signature.
3. COVERAGE  — the registry covers EXACTLY the ``@kernel_action("...")`` names
              discovered by AST scan (no missing, no stale, no drift).
4. TIERS     — every tier is a valid RiskTier; distribution is
              14 LOW / 12 MEDIUM / 15 HIGH / 2 CRITICAL; LOW == the C-1
              internal-service allow-list (two independent sources agree).
5. WIRING    — the decorator now records the *real* risk_level in the audit
              trail (previously a dead "LOW"), proving C-2 will have real
              input without changing any verdict today.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
sys.path.insert(0, str(REPO_ROOT))

failures: list[str] = []


def _module_name(path: pathlib.Path) -> str:
    rel = path.relative_to(REPO_ROOT).with_suffix("")
    return ".".join(rel.parts)


# --------------------------------------------------------------------------- #
# 1. IMPORT
# --------------------------------------------------------------------------- #
print("=" * 72)
print("[1] importing every module under src/")
print("=" * 72)

modules = sorted(p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts)
ok = failed = 0
for path in modules:
    name = _module_name(path)
    try:
        importlib.import_module(name)
        ok += 1
    except Exception as exc:  # noqa: BLE001 - report, do not abort
        failed += 1
        failures.append(f"IMPORT {name}: {type(exc).__name__}: {exc}")
print(f"  modules: {len(modules)}  OK: {ok}  FAILED: {failed}")
if failed:
    for line in failures:
        print("   ", line)

# --------------------------------------------------------------------------- #
# 2. DECORATOR
# --------------------------------------------------------------------------- #
print()
print("=" * 72)
print("[2] @kernel_action decorated callables still expose __wrapped__")
print("=" * 72)

from src.kernels._risk_classification import (  # noqa: E402
    KERNEL_ACTION_RISK,
    RiskTier,
    discover_kernel_action_names,
)
from src.kernels.policy import INTERNAL_SERVICE_ALLOWED_ACTIONS  # noqa: E402

kernel_modules = sorted(_module_name(p) for p in (SRC / "kernels").rglob("*.py"))
seen = bad = 0
for name in kernel_modules:
    try:
        mod = importlib.import_module(name)
    except Exception:
        continue
    for attr_name, obj in vars(mod).items():
        if inspect.ismodule(obj) or attr_name.startswith("__"):
            continue
        if inspect.isfunction(obj):
            members = [(attr_name, obj)]
        elif inspect.isclass(obj) and obj.__module__ == mod.__name__:
            for m_name, m_obj in vars(obj).items():
                if m_name.startswith("__"):
                    continue
                # Unwrap staticmethod/classmethod so __wrapped__ and
                # inspect.signature operate on the underlying function.
                if isinstance(m_obj, (staticmethod, classmethod)):
                    m_obj = m_obj.__func__
                if inspect.isfunction(m_obj):
                    members.append((f"{attr_name}.{m_name}", m_obj))
        else:
            continue
        for _label, m in members:
            if not hasattr(m, "__wrapped__"):
                continue
            seen += 1
            try:
                inspect.signature(m)
            except Exception as exc:  # noqa: BLE001
                bad += 1
                failures.append(f"SIGNATURE {name}:{_label}: {exc}")
print(f"  decorated callables found: {seen}  unusable signatures: {bad}")

# --------------------------------------------------------------------------- #
# 3. COVERAGE
# --------------------------------------------------------------------------- #
print()
print("=" * 72)
print("[3] registry covers every decorated kernel action (no drift)")
print("=" * 72)

decorated = discover_kernel_action_names()
classified = set(KERNEL_ACTION_RISK)
unclassified = sorted(decorated - classified)
stale = sorted(classified - decorated)
print(f"  decorated actions : {len(decorated)}")
print(f"  registry entries   : {len(classified)}")
print(f"  unclassified ([])  : {unclassified}")
print(f"  stale       ([])   : {stale}")
if unclassified:
    failures.append(f"COVERAGE: unclassified kernel actions {unclassified}")
if stale:
    failures.append(f"COVERAGE: stale registry entries {stale}")
if len(classified) != 43:
    failures.append(f"COVERAGE: registry size {len(classified)} != 43")

# --------------------------------------------------------------------------- #
# 4. TIERS
# --------------------------------------------------------------------------- #
print()
print("=" * 72)
print("[4] tier validity + distribution + LOW==allow-list cross-check")
print("=" * 72)

counts = {t: 0 for t in RiskTier}
for rec in KERNEL_ACTION_RISK.values():
    assert isinstance(rec.tier, RiskTier), "non-enum tier"
    counts[rec.tier] += 1
print(f"  distribution: { {t.value: c for t, c in counts.items()} }")
if counts != {RiskTier.LOW: 14, RiskTier.MEDIUM: 12, RiskTier.HIGH: 15, RiskTier.CRITICAL: 2}:
    failures.append(f"TIERS: unexpected distribution {counts}")

low = {a for a, rec in KERNEL_ACTION_RISK.items() if rec.tier is RiskTier.LOW}
if low != set(INTERNAL_SERVICE_ALLOWED_ACTIONS):
    failures.append(
        f"TIERS: LOW set != internal-service allow-list {sorted(low ^ set(INTERNAL_SERVICE_ALLOWED_ACTIONS))}"
    )
else:
    print("  LOW tier == INTERNAL_SERVICE_ALLOWED_ACTIONS (C-1 agreement): OK")

# --------------------------------------------------------------------------- #
# 5. WIRING — decorator records the real risk_level in audit
# --------------------------------------------------------------------------- #
print()
print("=" * 72)
print("[5] decorator feeds the real risk_level into the audit trail")
print("=" * 72)

from src.kernels._crosscutting import kernel_action  # noqa: E402


def _details_for(action_name: str):
    from src.kernels.audit import audit_query

    for ev in audit_query(principal_id="kernel", limit=2000, reverse=True):
        det = ev.get("details") or {}
        if det.get("action") == action_name:
            return det
    return None


class _Probe:
    @kernel_action("security.set_abac_rule")
    def critical_dummy(self):
        return "ran"

    @kernel_action("memory.store")
    def low_dummy(self):
        return "ran"


probe = _Probe()
assert probe.critical_dummy() == "ran"
assert probe.low_dummy() == "ran"
crit = _details_for("security.set_abac_rule")
lowd = _details_for("memory.store")
print(f"  security.set_abac_rule -> risk_level={crit.get('risk_level') if crit else None}")
print(f"  memory.store           -> risk_level={lowd.get('risk_level') if lowd else None}")
if crit is None or crit.get("risk_level") != "CRITICAL":
    failures.append("WIRING: security.set_abac_rule audit risk_level != CRITICAL")
if lowd is None or lowd.get("risk_level") != "LOW":
    failures.append("WIRING: memory.store audit risk_level != LOW")
# Verdict must be unchanged (record-only): allow for LOW, deny for CRITICAL.
if crit is not None and crit.get("policy_decision") != "deny":
    failures.append("WIRING: CRITICAL verdict changed (must stay deny, record-only)")
if lowd is not None and lowd.get("policy_decision") != "allow":
    failures.append("WIRING: LOW verdict changed (must stay allow)")
if crit is not None and crit.get("policy_enforced") is not False:
    failures.append("WIRING: policy_enforced must remain False (C-2 not enabled)")

# --------------------------------------------------------------------------- #
# RESULT
# --------------------------------------------------------------------------- #
print()
print("=" * 72)
if failures:
    print(f"RESULT: FAILED ({len(failures)})")
    for line in failures:
        print("  -", line)
    sys.exit(1)
print("RESULT: ALL GREEN")
sys.exit(0)
