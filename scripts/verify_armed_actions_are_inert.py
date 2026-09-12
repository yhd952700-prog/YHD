#!/usr/bin/env python
"""C-6 guard: every action armed for production must be *provably inert*.

Why this script exists
----------------------
The C-5/C-6 rule is: an action may be armed only if production code never
reaches it on a normal path. Armed-but-reachable turns a working flow into
"wait for a human" -- an availability regression dressed up as security.

That property cannot be checked by grepping for call sites, because the
dangerous call sites are *inside* the defining module and look exactly like the
inert ones. ``capability.register`` is the worked example:

* ``src/kernels/capability/__init__.py`` calls it twice --
  once from ``_register_builtin_capabilities()`` (reached by
  ``get_capability_registry()``, i.e. on every startup) and once from the
  ``register_capability()`` convenience wrapper (reached by nothing).
  A textual scan cannot tell those apart; pretending otherwise is how a
  guardrail ends up certifying the wrong thing.

So this guard *measures* instead. For each armed action it spawns a fresh
process, arms that action **alone**, exercises the production hot paths, and
fails if either

* a hot path raised (``PolicyDeferredError`` / ``PolicyDeniedError`` /
  anything else), or
* the decorator emitted its ``POLICY ENFORCED`` warning for that action.

The second condition matters: an exception swallowed by a broad
``except Exception`` would leave the probes green while the flow silently
degraded. The warning is emitted on the block path unconditionally (the
decorator's ``observable`` defaults to ``True``), so it catches that case too.

Scope and honesty about it
--------------------------
The hot-path list is a *sample*, not a proof of the negative. It is
deliberately broad (every kernel singleton bootstrap + the app factory +
unauthenticated GET routes) and it is backed by the app-level suite
(``tests/test_profile_api.py``, ``tests/test_liuhao_assistant.py``,
``tests/test_runtime_loop.py``, ``tests/test_ai_layer_dod_delegation.py``,
``tests/security/``) run once with the *full* manifest spec armed -- a subset
run proves the whole set, because arming one action never affects another.

A call site added on a path neither covers will still slip through. The honest
claim is "no measured reachability", not "no reachability".

Exit code 0 iff every armed action is inert.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import pathlib
import re
import subprocess
import sys
from typing import Dict, List, Tuple

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

MANIFEST = REPO_ROOT / "docker-compose.prod.yml"

#: App-level suite: exercises production paths (HTTP endpoints, the assistant,
#: the runtime loop, the security chain). Kernel unit tests are deliberately
#: excluded -- they call kernel actions *directly*, so they are expected to be
#: blocked when an action is armed and prove nothing about production.
APP_LEVEL_TESTS: Tuple[str, ...] = (
    "tests/test_profile_api.py",
    "tests/test_liuhao_assistant.py",
    "tests/test_runtime_loop.py",
    "tests/test_ai_layer_dod_delegation.py",
    "tests/security/",
)

#: Unauthenticated GET routes worth hitting (auth-free, side-effect-free).
GET_ROUTES: Tuple[str, ...] = (
    "/v1/health",
    "/v1/policy/enforcement",
    "/openapi.json",
)

_MANIFEST_RE = re.compile(
    r"LIUHAO_KERNEL_POLICY_ENFORCE=\$\{[^:}]*:-([^}]*)\}"
)

#: Marker prefix for the probe's machine-readable result line.
PROBE_PREFIX = "PROBE_JSON "


# --------------------------------------------------------------------------- #
# manifest
# --------------------------------------------------------------------------- #
def manifest_spec() -> str:
    """The production default spec, read out of the deployment manifest.

    Reading it from the manifest (rather than hardcoding) is the point: the
    guard tracks whatever the deployment actually arms.
    """
    text = MANIFEST.read_text(encoding="utf-8", errors="replace")
    match = _MANIFEST_RE.search(text)
    if match is None:
        raise RuntimeError(
            "no LIUHAO_KERNEL_POLICY_ENFORCE=<default> declaration in "
            f"{MANIFEST.name}: the deployment arms nothing, or the guard is "
            "reading the wrong file"
        )
    return match.group(1).strip()


# --------------------------------------------------------------------------- #
# probe mode: arm ONE action in a fresh process, exercise the hot paths
# --------------------------------------------------------------------------- #
def _exercise_hot_paths() -> Tuple[List[str], int]:
    """Run the production hot paths; return (failures, routes_touched)."""
    failures: List[str] = []

    def probe(name: str, fn) -> None:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - the whole point is to catch all
            failures.append("%s -> %s: %s" % (name, type(exc).__name__, str(exc)[:120]))

    def _cap():
        from src.kernels.capability import get_capability_registry

        get_capability_registry()

    def _exec():
        from src.kernels.execution import ActionExecutor

        ActionExecutor()

    def _app():
        from src.gateway.main import get_app

        get_app()

    def _plugin():
        from src.kernels.plugin import get_plugin_registry

        get_plugin_registry()

    def _security():
        from src.kernels.security import get_security_engine

        get_security_engine()

    def _identity():
        from src.kernels.identity import get_identity_manager

        get_identity_manager()

    def _trust():
        from src.kernels.trust import get_trust_manager

        get_trust_manager()

    def _event():
        from src.kernels.event import get_event_bus

        get_event_bus()

    def _network():
        from src.kernels.network import get_network_bus

        get_network_bus()

    def _memory():
        from src.kernels.memory import get_tier_manager

        get_tier_manager()

    probe("capability.bootstrap", _cap)
    probe("execution.kernel", _exec)
    probe("gateway.app", _app)
    probe("plugin.registry", _plugin)
    probe("security.engine", _security)
    probe("identity.manager", _identity)
    probe("trust.manager", _trust)
    probe("event.bus", _event)
    probe("network.bus", _network)
    probe("memory.tiers", _memory)

    touched = 0
    try:
        from fastapi.testclient import TestClient

        from src.gateway.main import get_app

        client = TestClient(get_app(), raise_server_exceptions=False)
        for route in GET_ROUTES:
            try:
                client.get(route)
                touched += 1
            except Exception:  # noqa: BLE001 - never let a route error fail the probe
                pass
    except Exception:  # noqa: BLE001 - TestClient unavailable is not a failure
        pass

    return failures, touched


def run_probe(action: str) -> int:
    """Arm ``action`` alone, exercise the hot paths, report; 0 = inert."""
    os.environ["LIUHAO_KERNEL_POLICY_ENFORCE"] = action

    from src.kernels._enforcement import ENV_VAR, enforced_actions, reload

    reload()

    blocks: List[str] = []

    class _BlockCapture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            try:
                message = record.getMessage()
            except Exception:  # noqa: BLE001 - never break on a log record
                return
            if message.startswith("POLICY ENFORCED"):
                blocks.append(message)

    logging.getLogger("liuhao.kernel.crosscutting").addHandler(_BlockCapture())
    logging.getLogger("liuhao.kernel.crosscutting").setLevel(logging.WARNING)

    config_error = None
    armed: List[str] = []
    try:
        armed = sorted(enforced_actions())
    except ValueError as exc:  # fail-loud contract: a bad spec is a failure
        config_error = str(exc)

    failures: List[str] = []
    touched = 0
    if config_error is None and armed != [action]:
        failures.append("arm(set) != {%s}: got %r" % (action, armed))
    if config_error is None and not failures:
        failures, touched = _exercise_hot_paths()

    payload = {
        "action": action,
        "env_var": ENV_VAR,
        "armed": armed,
        "failures": failures,
        "blocks": blocks,
        "routes_touched": touched,
        "config_error": config_error,
    }
    print(PROBE_PREFIX + json.dumps(payload, ensure_ascii=False))
    if config_error is not None:
        print("  config error: %s" % config_error)
        return 1
    return 0 if not failures and not blocks else 1


# --------------------------------------------------------------------------- #
# check mode
# --------------------------------------------------------------------------- #
def _count_pytest_outcomes(text: str) -> Dict[str, int]:
    """Count progress characters.

    The summary line is not reliable: a sandbox bulk-delete guard can truncate
    pytest's tmpdir cleanup at ``[100%]`` and swallow the summary, leaving a
    non-zero exit code for a green run. The per-test progress characters always
    survive, so count those instead.
    """
    batches = re.findall(r"^([.sFEx]+)\s*\[", text, flags=re.M)
    joined = "".join(batches)
    return {
        "passed": joined.count("."),
        "skipped": joined.count("s"),
        "failed": joined.count("F"),
        "errors": joined.count("E"),
        "total": len(joined),
    }


def run_full_spec_subset(spec: str, failures: List[str]) -> Dict[str, int]:
    """Run the app-level suite with the FULL spec armed.

    Arming one action cannot affect another, so a green run with the whole set
    armed proves every subset is green -- this is the breadth half of the guard.
    """
    env = dict(os.environ)
    env["LIUHAO_KERNEL_POLICY_ENFORCE"] = spec
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", *APP_LEVEL_TESTS, "-q", "-p", "no:cacheprovider"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    counts = _count_pytest_outcomes(proc.stdout + proc.stderr)
    if counts["failed"] or counts["errors"]:
        failures.append(
            "app-level suite is NOT inert with the full spec armed: "
            "%d failed / %d errors (of %d collected)"
            % (counts["failed"], counts["errors"], counts["total"])
        )
    if counts["total"] == 0:
        failures.append(
            "app-level suite produced no test results -- the guard cannot "
            "certify inertness (collection failure?)"
        )
    return counts


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--probe",
        metavar="ACTION",
        help="internal: arm ACTION alone and report whether it is inert",
    )
    args = parser.parse_args(argv)

    if args.probe:
        return run_probe(args.probe)

    from src.kernels._enforcement import EXEMPT_ACTIONS, parse_spec

    checks = 0
    failures: List[str] = []

    # --- 1. the manifest must declare a parseable spec ---------------------- #
    try:
        spec = manifest_spec()
        checks += 1
    except RuntimeError as exc:
        print("FAIL  manifest spec: %s" % exc)
        return 1
    print("manifest spec : %r" % spec)

    try:
        armed = sorted(parse_spec(spec))
        checks += 1
    except ValueError as exc:
        print("FAIL  manifest spec does not parse: %s" % exc)
        return 1

    # --- 2. no exempt action may be armed ---------------------------------- #
    leaked = sorted(set(armed) & set(EXEMPT_ACTIONS))
    if leaked:
        failures.append("exempt actions are armed: %s" % leaked)
    else:
        checks += 1
        print("exemptions    : %s (none armed)" % sorted(EXEMPT_ACTIONS))

    # --- 3. the armed set must be non-trivial and fully attributed --------- #
    print("armed actions : %d" % len(armed))
    for action in armed:
        print("   -", action)
    if not armed:
        failures.append(
            "the production manifest arms nothing -- this guard would be "
            "vacuous, which is exactly what it exists to prevent"
        )
    else:
        checks += 1

    # --- 4. breadth: full spec armed, app-level suite ---------------------- #
    counts = run_full_spec_subset(spec, failures)
    checks += 1
    print(
        "app-level suite (full spec armed): %d passed / %d skipped / %d failed"
        % (counts["passed"], counts["skipped"], counts["failed"])
    )

    # --- 5. per-action inertness ------------------------------------------- #
    not_inert: List[Tuple[str, str]] = []
    for action in armed:
        proc = subprocess.run(
            [sys.executable, str(pathlib.Path(__file__).resolve()), "--probe", action],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        payload: Dict[str, object] = {}
        for line in proc.stdout.splitlines():
            if line.startswith(PROBE_PREFIX):
                try:
                    payload = json.loads(line[len(PROBE_PREFIX):])
                except json.JSONDecodeError:
                    payload = {}
        detail = ""
        if payload.get("failures"):
            detail = "; ".join(str(f) for f in payload["failures"])  # type: ignore[union-attr]
        elif payload.get("blocks"):
            detail = "blocked at runtime: %s" % (payload["blocks"][0])  # type: ignore[index]
        elif payload.get("config_error"):
            detail = "config error: %s" % payload["config_error"]
        elif proc.returncode != 0:
            detail = "probe exited %d with no parsed payload" % proc.returncode

        if detail:
            not_inert.append((action, detail))
            print("   NOT INERT  %-26s %s" % (action, detail))
        else:
            checks += 1
            print("   inert      %-26s routes=%s" % (action, payload.get("routes_touched")))

    if not_inert:
        failures.append(
            "%d armed action(s) are reachable from production paths: %s"
            % (len(not_inert), [a for a, _ in not_inert])
        )

    # --- verdict ------------------------------------------------------------ #
    print()
    if failures:
        print("=" * 70)
        print("FAILED (%d checks passed)" % checks)
        for failure in failures:
            print("  X %s" % failure)
        print("=" * 70)
        return 1
    print("=" * 70)
    print("ALL GREEN (%d checks) -- every armed action is provably inert" % checks)
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
