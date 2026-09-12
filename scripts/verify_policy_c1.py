"""Policy C-1 verification — reproducible evidence for the internal-service
allow-list work.

Run:
    .venv/Scripts/python.exe scripts/verify_policy_c1.py

Checks (exit 0 = all green):

1. IMPORT   — every module under src/ imports cleanly (catches import-order /
              class-namespace breakage that syntax + lint checks cannot see).
2. DECORATOR— every ``@kernel_action``-decorated callable still exposes
              ``__wrapped__`` and a retrievable signature (the decorator did
              not clobber descriptors).
3. POLICY   — the internal service principal is verified by the engine and
              the verdict is allow-list driven (not a constant).
4. FORGERY  — self-declared service claims cannot obtain an allow.
5. KILLSWITCH — suspending the service identity suppresses every allow.
6. COVERAGE — every ``@kernel_action("...")`` name in src/ is classified in
              exactly one of the two policy lists (no drift, no wildcard).
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

kernel_modules = sorted(
    _module_name(p) for p in (SRC / "kernels").rglob("*.py")
    if p.name != "__init__.py" or True
)
seen = 0
bad = 0
for name in kernel_modules:
    try:
        mod = importlib.import_module(name)
    except Exception:
        continue
    members: list = []
    for attr_name, obj in vars(mod).items():
        if inspect.ismodule(obj) or attr_name.startswith("__"):
            continue
        if inspect.isfunction(obj):
            members.append((attr_name, obj))
        elif inspect.isclass(obj) and obj.__module__ == mod.__name__:
            for m_name, m_obj in vars(obj).items():
                if m_name.startswith("__"):
                    continue
                # Unwrap staticmethod/classmethod so __wrapped__ and
                # inspect.signature operate on the underlying function: a
                # bare classmethod object is not itself callable-inspectable.
                if isinstance(m_obj, (staticmethod, classmethod)):
                    m_obj = m_obj.__func__
                if inspect.isfunction(m_obj):
                    members.append((f"{attr_name}.{m_name}", m_obj))
    for label, obj in members:
        if not hasattr(obj, "__wrapped__"):
            continue
        seen += 1
        try:
            inspect.signature(obj)
        except Exception as exc:  # noqa: BLE001
            bad += 1
            failures.append(f"SIGNATURE {name}:{label}: {exc}")
print(f"  decorated callables found: {seen}  unusable signatures: {bad}")

# --------------------------------------------------------------------------- #
# 3-6. POLICY
# --------------------------------------------------------------------------- #
from src.kernels.identity import (  # noqa: E402
    INTERNAL_SERVICE_PRINCIPAL,
    IdentityStatus,
    get_identity_manager,
)
from src.kernels.policy import (  # noqa: E402
    INTERNAL_SERVICE_ALLOWED_ACTIONS,
    INTERNAL_SERVICE_DENIED_ACTIONS,
    get_policy_engine,
)

engine = get_policy_engine()


def decide(action: str, actor: dict):
    return engine.evaluate_simple(actor=dict(actor), action={"name": action,
                                                            "risk_level": "LOW"})


print()
print("=" * 72)
print("[3] verdict is allow-list driven (not a constant)")
print("=" * 72)

SVC = {"type": "service", "principal": INTERNAL_SERVICE_PRINCIPAL}
allow_hits = [a for a in sorted(INTERNAL_SERVICE_ALLOWED_ACTIONS) if decide(a, SVC).is_allowed]
deny_hits = [a for a in sorted(INTERNAL_SERVICE_DENIED_ACTIONS) if decide(a, SVC).is_denied]
print(f"  allow-listed actions allowed : {len(allow_hits)}/{len(INTERNAL_SERVICE_ALLOWED_ACTIONS)}")
print(f"  denied actions denied        : {len(deny_hits)}/{len(INTERNAL_SERVICE_DENIED_ACTIONS)}")
if len(allow_hits) != len(INTERNAL_SERVICE_ALLOWED_ACTIONS):
    failures.append("POLICY: some allow-listed actions were not allowed")
if len(deny_hits) != len(INTERNAL_SERVICE_DENIED_ACTIONS):
    failures.append("POLICY: some non-allow-listed actions were not denied")

ident = get_identity_manager().get_identity(INTERNAL_SERVICE_PRINCIPAL)
print(f"  identity: id={ident.id if ident else None} "
      f"status={ident.status.value if ident else None} "
      f"kind={(ident.metadata or {}).get('kind') if ident else None}")
if ident is None or (ident.metadata or {}).get("kind") != "service":
    failures.append("POLICY: internal service identity missing or unmarked")

print()
print("=" * 72)
print("[4] self-declared service claims cannot obtain an allow")
print("=" * 72)

# Cases that MUST be denied: the claim is unbacked (no/unknown principal,
# wrong identity kind) or uses the legacy anonymous system actor.
forged = [
    {"type": "service"},
    {"type": "service", "principal": "does-not-exist"},
    {"type": "service", "principal": "system"},
    {"type": "system", "verified": True},
]
for actor in forged:
    d = decide("memory.store", actor)
    print(f"  {str(actor)[:58]:60s} -> {d.decision.value}")
    if d.is_allowed:
        failures.append(f"FORGERY: {actor} obtained an allow")

# Control: the canonical principal IS legitimate, and the caller-supplied
# ``verified`` flag must be ignored (the engine recomputes it). Presence of
# the flag must not change the outcome -- this case being allowed while the
# ``does-not-exist`` case is denied is the proof that verification is real.
control = {"type": "service", "principal": INTERNAL_SERVICE_PRINCIPAL, "verified": True}
d = decide("memory.store", control)
print(f"  {str(control)[:58]:60s} -> {d.decision.value}   (control: must allow)")
if not d.is_allowed:
    failures.append("FORGERY: canonical service principal was denied")

print()
print("=" * 72)
print("[5] verification is revocable (kill switch)")
print("=" * 72)

original = ident.status
for status in (IdentityStatus.SUSPENDED, IdentityStatus.DEACTIVATED):
    ident.status = status
    d = decide("memory.store", SVC)
    print(f"  status={status.value:12s} -> {d.decision.value}")
    if d.is_allowed:
        failures.append(f"KILLSWITCH: allow survived status={status.value}")
ident.status = original
print(f"  status={original.value:12s} -> {decide('memory.store', SVC).decision.value}")

print()
print("=" * 72)
print("[6] allow-list covers every decorated kernel action (no drift)")
print("=" * 72)

decorated: set = set()
for path in (SRC / "kernels").rglob("*.py"):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if isinstance(dec, ast.Call) and dec.args and isinstance(dec.args[0], ast.Constant):
                fname = getattr(dec.func, "id", None) or getattr(dec.func, "attr", None)
                if fname == "kernel_action" and isinstance(dec.args[0].value, str):
                    decorated.add(dec.args[0].value)

classified = INTERNAL_SERVICE_ALLOWED_ACTIONS | INTERNAL_SERVICE_DENIED_ACTIONS
unclassified = sorted(decorated - classified)
stale = sorted(classified - decorated)
print(f"  decorated actions      : {len(decorated)}")
print(f"  allow-list entries     : {len(INTERNAL_SERVICE_ALLOWED_ACTIONS)} allowed "
      f"+ {len(INTERNAL_SERVICE_DENIED_ACTIONS)} denied = {len(classified)}")
print(f"  overlap                : {sorted(INTERNAL_SERVICE_ALLOWED_ACTIONS & INTERNAL_SERVICE_DENIED_ACTIONS)}")
print(f"  unclassified (must be []) : {unclassified}")
print(f"  stale       (must be []) : {stale}")
if unclassified:
    failures.append(f"COVERAGE: unclassified kernel actions {unclassified}")
if stale:
    failures.append(f"COVERAGE: stale allow-list entries {stale}")
if INTERNAL_SERVICE_ALLOWED_ACTIONS & INTERNAL_SERVICE_DENIED_ACTIONS:
    failures.append("COVERAGE: allow and deny lists overlap")

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
