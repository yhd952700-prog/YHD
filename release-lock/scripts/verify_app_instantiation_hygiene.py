#!/usr/bin/env python
"""SA-2 -- gateway-app instantiation hygiene gate.

What this guards
================
Read-only verification scripts must be able to boot the gateway app to
exercise its routes, but three mistakes keep recurring, and each is silent
until someone notices a leaked process or a mutated audit database:

  * **Frozen-evidence write.** ``get_app()`` enters the gateway lifespan,
    which initialises the audit subsystem. If ``AUDIT_DB_PATH`` is left at
    its default the lifecycle can write to the repository-root
    ``audit_store.db`` / ``-wal`` / ``-shm`` -- the frozen HC-01 evidence the
    project has explicitly forbidden anyone to touch. So every script that
    builds the app must redirect that path first (or delegate to a helper
    that does).
  * **Leaked app instance (bare openapi).** ``get_app().openapi()`` builds a
    FastAPI object and throws it away without tearing it down. Several
    "read-only" scripts did exactly this (e.g. ``verify_openapi_contract.py``
    historically), and the unclosed object holds the route table, middleware
    and any resources the lifespan opened.
  * **TestClient without ``with``.** ``client = TestClient(get_app())`` never
    runs the lifespan's shutdown, so the gateway's audit/logging lifecycle
    keeps running after the probe returns.

The single sanctioned pattern
=============================
``scripts/_gateway_app.py`` centralises both fixes:

  * ``gateway_openapi_spec()`` -- read the schema through a
    ``TestClient`` context manager (lifespan starts and stops) with
    ``AUDIT_DB_PATH`` redirected to a throwaway db.
  * ``gateway_client()`` -- a context manager yielding a ``TestClient``.

This gate checks, for every script that mentions ``get_app()`` /
``create_app()``:

  A. it redirects ``AUDIT_DB_PATH`` to a throwaway path (or relies on the
     helper, which redirects internally);
  B. every ``get_app()`` / ``create_app()`` call is either inside a
     ``with TestClient(...)`` block, used only through the helper, or fed to
     a uvicorn server (which is process-bound and stopped by ``shutdown()``).
     Bare ``get_app().openapi()`` and ``TestClient(get_app())`` assigned
     (not ``with``) are reported.

Honesty rules
=============
* Read-only: no writes, no app construction at import time, never touches
  ``audit_store.db``.
* Standard library only; runs in anything that can run Python.
* Reports what it *proves* (AST facts), not guesses.

Exit codes
==========
0  all scripts clean
1  at least one script violates a rule
2  the gate could not obtain the evidence it needs
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

# Repo root on sys.path so this gate runs exactly like every other
# scripts/verify_*.py: `python scripts/verify_app_instantiation_hygiene.py` from
# the repo root. Uniform convention enforced by
# tests/test_guardrail_scripts.py::test_every_verify_script_bootstraps_sys_path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"

# A script is allowed to boot the app via a uvicorn server (process-bound,
# stopped by its own shutdown), or through the shared helper, or through a
# `with TestClient(...)` block. These are the "acceptable" teardown signals.
UVICORN_SIGNALS = ("uvicorn.run", "Server(", "manager.shutdown", "server.stop")
HELPER_SIGNALS = ("gateway_openapi_spec", "gateway_client", "from _gateway_app",
                  "import _gateway_app")


def _parent_index(tree: ast.Module) -> dict:
    """Map every node to its parent, so a call can be tested for `with`."""
    parent = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent[child] = node
    return parent


def _is_within_with_testclient(call: ast.AST, parent: dict) -> bool:
    """Is ``call`` nested inside a ``with TestClient(...) as ...:`` block?"""
    node: ast.AST | None = call
    while node is not None:
        if isinstance(node, ast.With):
            for item in node.items:
                ctx = item.context_expr
                if (isinstance(ctx, ast.Call)
                        and _is_call_to(ctx, "TestClient")):
                    return True
        node = parent.get(node)
    return False


def _is_call_to(node: ast.AST, name: str) -> bool:
    if not isinstance(node, ast.Call):
        return False
    fn = node.func
    if isinstance(fn, ast.Name):
        return fn.id == name
    if isinstance(fn, ast.Attribute):
        return fn.attr == name
    return False


def scan_script(path: Path) -> list[str]:
    problems: list[str] = []
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        return [f"cannot parse {path.name}: {exc}"]

    source = path.read_text(encoding="utf-8")
    has_app_call = bool(re.search(r"\b(get_app|create_app)\s*\(", source))

    # Rule A: build-the-app scripts must isolate audit persistence.
    # Recognise both ``AUDIT_DB_PATH = ...`` and the dict form
    # ``os.environ["AUDIT_DB_PATH"] = ...`` (the common one in this repo).
    if has_app_call:
        redirects = bool(re.search(r'AUDIT_DB_PATH\\?["\']?\s*\]?\s*=', source))
        uses_helper = any(sig in source for sig in HELPER_SIGNALS)
        if not (redirects or uses_helper):
            problems.append(
                f"{path.name}: builds the app but never sets AUDIT_DB_PATH "
                f"and does not use the _gateway_app helper -- the gateway "
                f"lifespan may write to the frozen repo audit_store.db"
            )

    # Rule B: every get_app()/create_app() must be torn down.
    parent = _parent_index(tree)
    for node in ast.walk(tree):
        if not _is_call_to(node, "get_app") and not _is_call_to(node, "create_app"):
            continue

        # Fed to a uvicorn server: process-bound, accept.
        # (heuristic: the file as a whole uses a server/shutdown signal)
        if any(sig in source for sig in UVICORN_SIGNALS):
            continue

        # Inside `with TestClient(...)`: lifespan starts + stops. OK.
        if _is_within_with_testclient(node, parent):
            continue

        # The rest are leaks. Distinguish the two common shapes for a
        # precise, non-vacuous message.
        # Bare openapi read: get_app().openapi()
        parent_call = parent.get(node)
        if isinstance(parent_call, ast.Attribute) and parent_call.attr == "openapi":
            problems.append(
                f"{path.name}:{node.lineno}: bare `get_app().openapi()` leaks "
                f"the app instance and skips lifecycle teardown -- use "
                f"`gateway_openapi_spec()` from scripts/_gateway_app.py"
            )
        # TestClient(...) assigned outside a `with` block
        elif (isinstance(parent_call, ast.Call)
              and _is_call_to(parent_call, "TestClient")):
            problems.append(
                f"{path.name}:{node.lineno}: `TestClient(get_app())` is not in "
                f"a `with` block -- lifespan never shuts down; use "
                f"`with TestClient(get_app()) as client:`"
            )
        else:
            problems.append(
                f"{path.name}:{node.lineno}: `get_app()/create_app()` result is "
                f"not torn down (no `with TestClient`, no helper, no uvicorn "
                f"server) -- it leaks the app instance"
            )
    return problems


def main() -> int:
    print("=" * 78)
    print("SA-2  gateway-app instantiation hygiene gate")
    print("=" * 78)
    if not SCRIPTS.is_dir():
        print("  [BLOCKED] scripts/ not found")
        return 2

    print("\n=== A/B. every app-building script isolates audit + tears down ===")
    problems: list[str] = []
    for path in sorted(SCRIPTS.glob("*.py")):
        if path.name == Path(__file__).name:
            continue
        if not re.search(r"\b(get_app|create_app)\s*\(",
                         path.read_text(encoding="utf-8", errors="replace")):
            continue
        for p in scan_script(path):
            problems.append(p)

    if not problems:
        print("  [ok]   no script leaks an app instance or risks the frozen audit db")
    else:
        for p in problems:
            print(f"  [FAIL] {p}")

    print("\n" + "=" * 78)
    if problems:
        print(f"RESULT: FAIL -- {len(problems)} problem(s)")
        print("=" * 78)
        return 1
    print("RESULT: PASS -- all app-building scripts are lifecycle-clean")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
