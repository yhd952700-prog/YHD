"""CRITICAL-5 watchdog verification — real evidence, not a reimplementation.

Proves three things WITHOUT needing `act` / Docker / a real Ubuntu CI run:

  1. VALIDITY  — both `ci.yml` and the composite action parse as valid YAML.
  2. WIRING    — the 5 gate jobs that can be silently skipped
                 (security, guardrails, guardrails-extended, lint — all
                 `needs: test`; and gate-check — `needs: [test, security,
                 observability]`) now carry `if: always()` and a watchdog step
                 that references the correct `needs.<job>.result`.
  3. LOGIC     — the ACTUAL shell code inside the composite action fails
                 (exit 1) for every upstream result other than "success", and
                 passes (exit 0) only for "success". The shell is extracted
                 from action.yml at runtime and executed, so this tests the
                 real code, not a copy.

Exit 0 = watchdog is correctly implemented. Exit 1 = a check failed.

HONEST LIMITATION: this does NOT run real GitHub Actions. The end-to-end
"red `test` -> gate shows FAILED, not SKIP" orchestration is verified by static
reasoning + this decision-logic test, and remains UNVERIFIED by a real Ubuntu
CI run (no `act` / no Docker in this environment). That gap is reported
explicitly rather than papered over.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import yaml

REPO = pathlib.Path(__file__).resolve().parents[1]
ACTION = REPO / ".github/actions/ci-gate-watchdog/action.yml"
CI = REPO / ".github/workflows/ci.yml"

# job_name -> list of upstream jobs whose result the watchdog must assert
EXPECTED_WIRING = {
    "security": ["test"],
    "guardrails": ["test"],
    "guardrails-extended": ["test"],
    "lint": ["test"],
    "gate-check": ["test", "security", "observability"],
}

failures: list[str] = []


# --------------------------------------------------------------------------- #
# 1. YAML VALIDITY
# --------------------------------------------------------------------------- #
print("=" * 72)
print("[1] YAML validity")
print("=" * 72)
for path in (ACTION, CI):
    try:
        with open(path) as fh:
            yaml.safe_load(fh)
        print(f"  OK  {path.relative_to(REPO)}")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"YAML parse failed for {path}: {type(exc).__name__}: {exc}")


# --------------------------------------------------------------------------- #
# 2. WIRING (static structural check)
# --------------------------------------------------------------------------- #
print()
print("=" * 72)
print("[2] gate-job wiring (if: always() + watchdog step per upstream)")
print("=" * 72)

action = yaml.safe_load(open(ACTION))
ci = yaml.safe_load(open(CI))
jobs = ci.get("jobs", {})

for job_name, upstreams in EXPECTED_WIRING.items():
    if job_name not in jobs:
        failures.append(f"WIRING: job '{job_name}' not found in ci.yml")
        continue
    job = jobs[job_name]
    job_if = str(job.get("if", ""))
    if "always()" not in job_if:
        failures.append(f"WIRING: job '{job_name}' missing always() in if: ({job_if!r})")

    watchdog_steps = [
        s for s in job.get("steps", []) if str(s.get("uses", "")).endswith("ci-gate-watchdog")
    ]
    found_names = {str(s.get("with", {}).get("upstream-name")) for s in watchdog_steps}
    for upstream in upstreams:
        if upstream not in found_names:
            failures.append(f"WIRING: job '{job_name}' missing watchdog for upstream '{upstream}'")
            continue
        matching = [
            s for s in watchdog_steps
            if str(s.get("with", {}).get("upstream-name")) == upstream
        ]
        for s in matching:
            result_ref = str(s.get("with", {}).get("upstream-result", ""))
            if f"needs.{upstream}.result" not in result_ref:
                failures.append(
                    f"WIRING: job '{job_name}' watchdog for '{upstream}' does not "
                    f"reference needs.{upstream}.result (got {result_ref!r})"
                )
    if job_name not in [f.split("'")[1] for f in failures if "WIRING" in f and job_name in f]:
        print(f"  OK  {job_name}: always() + watchdog for {upstreams}")


# --------------------------------------------------------------------------- #
# 3. LOGIC — execute the REAL shell from action.yml
# --------------------------------------------------------------------------- #
print()
print("=" * 72)
print("[3] watchdog decision logic (real shell, injected upstream results)")
print("=" * 72)

watchdog_run = None
for step in action.get("runs", {}).get("steps", []):
    if step.get("shell") == "bash" and "run" in step:
        watchdog_run = step["run"]
        break
if watchdog_run is None:
    failures.append("LOGIC: could not locate the bash step in the composite action")
else:
    # Locate a real POSIX shell. Bare "bash" can resolve to the WSL shim
    # (wsl.exe) on Windows, which is sandbox-blocked; prefer the absolute
    # git-bash / Ubuntu path, which is correct in both local and CI envs.
    bash = None
    for cand in ("/usr/bin/bash", "/usr/bin/sh", "bash", "sh"):
        if cand.startswith("/") and os.path.exists(cand):
            bash = cand
            break
        # bare name: let PATH resolve, but only accept if it actually runs.
        if not cand.startswith("/"):
            probe = subprocess.run(
                [cand, "-c", "true"], capture_output=True, text=True
            )
            if probe.returncode == 0:
                bash = cand
                break
    if bash is None:
        failures.append("LOGIC: no usable POSIX shell found for the subprocess")
        bash = "bash"
    print(f"  using shell: {bash}")
    cases = {
        "success": 0,
        "failure": 1,
        "cancelled": 1,
        "skipped": 1,
        "": 1,  # missing input must fail-closed
    }
    for result, expected_code in cases.items():
        env = dict(os.environ)
        env["UPSTREAM_NAME"] = "test"
        env["UPSTREAM_RESULT"] = result
        proc = subprocess.run(
            [bash, "-c", watchdog_run],
            env=env,
            capture_output=True,
            text=True,
        )
        status = "OK " if proc.returncode == expected_code else "BAD"
        print(
            f"  [{status}] result={result!r:12} -> exit {proc.returncode} "
            f"(expected {expected_code})"
        )
        if proc.returncode != expected_code:
            failures.append(
                f"LOGIC: watchdog with result={result!r} exited {proc.returncode} "
                f"(expected {expected_code}); stdout={proc.stdout!r}; "
                f"stderr={proc.stderr!r}"
            )


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
print("RESULT: ALL GREEN — CRITICAL-5 watchdog correctly implemented")
print("  (real GitHub Ubuntu run remains UNVERIFIED: no act/Docker here)")
sys.exit(0)
