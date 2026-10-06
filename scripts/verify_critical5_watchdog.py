"""CRITICAL-5 watchdog verification — dynamic, honest, real evidence.

CRITICAL-5 mandates that a "red" upstream job must turn its downstream gate
jobs RED (explicit FAIL), never silently SKIP. A SKIP hides a broken build
behind a falsely "all green" pipeline.

This verifier proves the watchdog is correctly wired WITHOUT needing `act` /
Docker / a real Ubuntu CI run, by statically scanning the workflow files:

  1. VALIDITY  -- the composite action and every workflow file parse as YAML.
  2. WIRING    -- for EVERY job in EVERY workflow file that declares `needs:`:
                   (a) the job's `if:` must contain `always()` so it still
                       runs when an upstream failed (instead of being skipped);
                   (b) a watchdog step (uses .../ci-gate-watchdog) must exist
                       for EACH upstream, asserting `needs.<up>.result == success`.
                 Any `needs:` job violating (a) or (b) is reported as UNCOVERED.
                 This is a DYNAMIC scan of `.github/workflows/*.yml` -- there is
                 NO hard-coded job whitelist, so a newly added `needs:` job that
                 forgets the watchdog is caught, and a workflow that is not yet
                 covered is flagged honestly rather than hidden behind GREEN.
  3. LOGIC     -- the ACTUAL shell code inside the composite action is executed
                 with injected upstream results: exit 0 only for "success",
                 exit 1 for failure/cancelled/skipped/empty (the latter being
                 fail-closed on a missing input).

Exit 0 = every `needs:` job across all workflows is correctly watchdog-protected.
Exit 1 = at least one `needs:` job is uncovered (or a static/logic check failed).

HONEST LIMITATION: this does NOT run real GitHub Actions. The end-to-end
"red upstream -> gate shows FAILED, not SKIP" orchestration is verified by static
analysis + this decision-logic test, and remains UNVERIFIED by a real Ubuntu CI
run (no `act` / no Docker in this environment). That gap is reported explicitly
rather than papered over.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import yaml

REPO = pathlib.Path(__file__).resolve().parents[1]
ACTION = REPO / ".github/actions/ci-gate-watchdog/action.yml"
WORKFLOWS = sorted(REPO.glob(".github/workflows/*.yml"))

failures: list[str] = []


def _normalize_needs(value) -> list[str]:
    """`needs:` may be a scalar job name or a list; return a list of names."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(v) for v in value]
    # dict form (needs: {job: result}) -- keys are upstream job names
    if isinstance(value, dict):
        return [str(k) for k in value.keys()]
    return [str(value)]


def _job_has_always(job: dict) -> bool:
    return "always()" in str(job.get("if", ""))


# --------------------------------------------------------------------------- #
# 1. YAML VALIDITY
# --------------------------------------------------------------------------- #
print("=" * 72)
print("[1] YAML validity")
print("=" * 72)
yaml_targets = [ACTION] + WORKFLOWS
for path in yaml_targets:
    try:
        with open(path) as fh:
            yaml.safe_load(fh)
        print(f"  OK  {path.relative_to(REPO)}")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"YAML parse failed for {path}: {type(exc).__name__}: {exc}")


# --------------------------------------------------------------------------- #
# 2. WIRING (dynamic, all workflows, every needs: job)
# --------------------------------------------------------------------------- #
print()
print("=" * 72)
print("[2] gate-job wiring across ALL workflows (if: always() + watchdog per upstream)")
print("=" * 72)

total_needs_jobs = 0
covered = 0

for wf in WORKFLOWS:
    try:
        with open(wf) as fh:
            doc = yaml.safe_load(fh) or {}
    except Exception as exc:  # noqa: BLE001
        failures.append(f"WIRING: cannot read {wf.name}: {exc}")
        continue
    jobs = doc.get("jobs", {}) or {}
    wf_rel = wf.relative_to(REPO)
    for job_name, job in jobs.items():
        if not isinstance(job, dict):
            continue
        upstreams = _normalize_needs(job.get("needs"))
        if not upstreams:
            continue  # not a dependent job -> nothing to watchdog
        total_needs_jobs += 1
        job_if = str(job.get("if", ""))
        always_ok = _job_has_always(job)
        if not always_ok:
            failures.append(
                f"WIRING: {wf_rel}:{job_name} has needs:{upstreams} but its "
                f"`if:` lacks always() -> would SKIP silently on red upstream"
            )
        watchdog_steps = [
            s for s in job.get("steps", []) if str(s.get("uses", "")).endswith("ci-gate-watchdog")
        ]
        found_names = {str(s.get("with", {}).get("upstream-name")) for s in watchdog_steps}
        missing = [u for u in upstreams if u not in found_names]
        if missing:
            failures.append(
                f"WIRING: {wf_rel}:{job_name} missing watchdog step(s) for upstream(s) {missing}"
            )
        else:
            for upstream in missing:  # (none, but keep structure)
                pass
            for upstream in upstreams:
                matching = [
                    s for s in watchdog_steps
                    if str(s.get("with", {}).get("upstream-name")) == upstream
                ]
                for s in matching:
                    result_ref = str(s.get("with", {}).get("upstream-result", ""))
                    if f"needs.{upstream}.result" not in result_ref:
                        failures.append(
                            f"WIRING: {wf_rel}:{job_name} watchdog for '{upstream}' "
                            f"does not reference needs.{upstream}.result (got {result_ref!r})"
                        )
        if always_ok and not missing:
            covered += 1
            print(f"  OK  {wf_rel}:{job_name} -> watchdog for {upstreams}")

if total_needs_jobs == 0:
    failures.append("WIRING: no `needs:` jobs found in any workflow (unexpected)")
else:
    print(f"  scanned {total_needs_jobs} dependent job(s) across {len(WORKFLOWS)} workflow file(s)")

# Explicit, honest coverage statement (no silent green).
print()
if covered == total_needs_jobs:
    print(f"  COVERAGE: all {covered} `needs:` jobs are watchdog-protected.")
else:
    print(
        f"  COVERAGE: {covered}/{total_needs_jobs} `needs:` jobs protected -- "
        f"{total_needs_jobs - covered} UNCOVERED (see failures above)."
    )


# --------------------------------------------------------------------------- #
# 3. LOGIC — execute the REAL shell from action.yml
# --------------------------------------------------------------------------- #
print()
print("=" * 72)
print("[3] watchdog decision logic (real shell, injected upstream results)")
print("=" * 72)

action = yaml.safe_load(open(ACTION))
watchdog_run = None
for step in action.get("runs", {}).get("steps", []):
    if step.get("shell") == "bash" and "run" in step:
        watchdog_run = step["run"]
        break
if watchdog_run is None:
    failures.append("LOGIC: could not locate the bash step in the composite action")
else:
    # Locate a real POSIX shell that BOTH runs and propagates the caller's
    # environment. Bare "bash" on this Windows/git-bash box starts but does NOT
    # forward env vars (UPSTREAM_RESULT/UPSTREAM_NAME arrive empty), which would
    # make the decision-logic test meaningless; "sh" does propagate them. The
    # action code is pure POSIX sh, so sh is a faithful proxy for the real
    # GitHub `bash` runner. We probe env propagation explicitly rather than
    # trusting a bare `true` check.
    bash = None
    for cand in ("/usr/bin/bash", "/usr/bin/sh", "bash", "sh", "/bin/bash", "/bin/sh"):
        if cand.startswith("/") and not os.path.exists(cand):
            continue
        probe = subprocess.run([cand, "-c", "true"], capture_output=True, text=True)
        if probe.returncode != 0:
            continue
        check = subprocess.run(
            [cand, "-c", 'test "$CRIT5_PROBE" = "ok"'],
            env={**os.environ, "CRIT5_PROBE": "ok"},
            capture_output=True,
            text=True,
        )
        if check.returncode == 0:
            bash = cand
            break
    if bash is None:
        failures.append("LOGIC: no usable POSIX shell (env-propagating) found")
        bash = "sh"
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
    print(f"RESULT: FAILED ({len(failures)}) -- watchdog coverage gaps exist:")
    for line in failures:
        print("  -", line)
    print("  (real GitHub Ubuntu run remains UNVERIFIED: no act/Docker here)")
    sys.exit(1)
print("RESULT: PASS -- every `needs:` job across all workflows is watchdog-protected")
print("  (real GitHub Ubuntu run remains UNVERIFIED: no act/Docker here)")
sys.exit(0)
