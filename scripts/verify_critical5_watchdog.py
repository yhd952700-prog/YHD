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
  3. REACHABLE -- for EVERY job that carries an `if:`, its branch/event
                   qualifiers must be satisfiable on the branch where work
                   actually lands. `github.ref_name == github.event.repository
                   .default_branch`, `github.ref == 'refs/heads/<branch>'` or
                   `github.event_name == 'push'` can pin a job to a branch/event
                   that never coincides with the working branch, so the job is
                   declared but NEVER RUNS. Such a gate reports nothing at all --
                   which is strictly worse than reporting red (an invisible gate
                   cannot fail, so it cannot protect anything). Detected jobs are
                   printed as UNREACHABLE and make this script exit non-zero;
                   the finding is NEVER downgraded to a warning.
  4. LOGIC     -- the ACTUAL shell code inside the composite action is executed
                 with injected upstream results: exit 0 only for "success",
                 exit 1 for failure/cancelled/skipped/empty (the latter being
                 fail-closed on a missing input).

Exit 0 = every `needs:` job across all workflows is correctly watchdog-protected
         and every gated job is reachable on the working branch.
Exit 1 = at least one `needs:` job is uncovered, a job is UNREACHABLE, or a
         static/logic check failed.

HONEST LIMITATION: this does NOT run real GitHub Actions. The end-to-end
"red upstream -> gate shows FAILED, not SKIP" orchestration is verified by static
analysis + this decision-logic test, and remains UNVERIFIED by a real Ubuntu CI
run (no `act` / no Docker in this environment). That gap is reported explicitly
rather than papered over.
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys

import yaml

REPO = pathlib.Path(__file__).resolve().parents[1]

# Repo root on sys.path so this gate is runnable exactly like every other
# scripts/verify_*.py: `python scripts/verify_critical5_watchdog.py` from the
# repo root. tests/test_guardrail_scripts.py::test_every_verify_script_bootstraps_sys_path
# enforces that convention uniformly; this script only needs `yaml` today, but
# adopting the house bootstrap keeps it standalone-runnable if it ever imports
# `src...` and keeps the meta-gate green without weakening the check.
sys.path.insert(0, str(REPO))

ACTION = REPO / ".github/actions/ci-gate-watchdog/action.yml"

# CRIT5_WORKFLOWS_DIR is TEST-ONLY: it lets this gate be pointed at a directory
# of fixture workflows so the reachability detector can be proven (before-fix =
# UNREACHABLE / after-fix = reachable) without mutating the real repo. It is
# never set by .github/workflows, so the real run always scans the repo.
_WORKFLOWS_DIR = os.environ.get("CRIT5_WORKFLOWS_DIR")
WORKFLOWS = (
    sorted(pathlib.Path(_WORKFLOWS_DIR).glob("*.yml"))
    if _WORKFLOWS_DIR
    else sorted(REPO.glob(".github/workflows/*.yml"))
)

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
# Reachability helpers — "declared but never runs" detection
# --------------------------------------------------------------------------- #
# Sentinel: the trigger admits every branch (`**`, `*`, or no `branches:` key).
ALL_BRANCHES = "__ALL_BRANCHES__"

# One `==` comparison between either a quoted literal or a dotted expression.
# Only POSITIVE (`==`) qualifiers are collected: a `!=` widens the set and can
# never by itself make a job unreachable.
_CMP_RE = re.compile(
    r"""(['"][^'"]*['"]|[A-Za-z_][A-Za-z0-9_.]*)\s*==\s*"""
    r"""(['"][^'"]*['"]|[A-Za-z_][A-Za-z0-9_.]*)"""
)

_REF_EXPRS = ("github.ref", "github.ref_name")


def _unquote(token: str):
    token = token.strip()
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'":
        return token[1:-1]
    return None


def _display(path) -> str:
    """Repo-relative label when inside the repo, absolute path otherwise.

    Fixture runs (CRIT5_WORKFLOWS_DIR) point at files outside the repo, where
    `relative_to` raises; reporting must not turn that into a fake failure.
    """
    try:
        return str(pathlib.Path(path).relative_to(REPO))
    except ValueError:
        return str(path)


def _trigger_map(doc: dict) -> dict:
    """Map workflow trigger event -> admitted branch set (or ALL_BRANCHES)."""
    on = doc.get("on")
    if on is None:
        on = doc.get(True)  # YAML 1.1 parses a bare `on:` key as boolean True
    if on is None:
        return {}
    if isinstance(on, str):
        return {on: ALL_BRANCHES}
    if isinstance(on, list):
        return {str(e): ALL_BRANCHES for e in on}
    if not isinstance(on, dict):
        return {}
    out = {}
    for event, cfg in on.items():
        if not isinstance(cfg, dict):
            out[str(event)] = ALL_BRANCHES
            continue
        branches = cfg.get("branches")
        if branches is None:
            out[str(event)] = ALL_BRANCHES
            continue
        if isinstance(branches, str):
            branches = [branches]
        if any(str(b) in ("**", "*") for b in branches):
            out[str(event)] = ALL_BRANCHES
        else:
            out[str(event)] = {str(b) for b in branches}
    return out


def _extract_constraints(if_expr: str):
    """Return (events, branches, uses_default_branch) from a job `if:` string."""
    events: set[str] = set()
    branches: set[str] = set()
    uses_default_branch = False
    for match in _CMP_RE.finditer(str(if_expr)):
        left, right = match.group(1), match.group(2)
        # Support both operand orders: `github.ref == 'x'` and `'x' == github.ref`
        for expr, other in ((left, right), (right, left)):
            if expr == "github.event_name":
                literal = _unquote(other)
                if literal is not None:
                    events.add(literal)
            elif expr in _REF_EXPRS:
                if "github.event.repository.default_branch" in other:
                    uses_default_branch = True
                    continue
                literal = _unquote(other)
                if literal is None:
                    continue
                if literal.startswith("refs/heads/"):
                    literal = literal[len("refs/heads/"):]
                branches.add(literal)
    return events, branches, uses_default_branch


def _git(*args):
    try:
        proc = subprocess.run(
            ["git", *args], cwd=str(REPO), capture_output=True, text=True
        )
    except Exception:  # noqa: BLE001  (no git on host -> resolved as UNRESOLVED)
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def _resolve_working_branch():
    """The branch work actually lands on (that is the branch a gate must guard)."""
    for name in ("LIUHAO_WORKING_BRANCH", "GITHUB_REF_NAME"):
        value = os.environ.get(name)
        if value:
            return value
    return _git("rev-parse", "--abbrev-ref", "HEAD")


def _resolve_default_branch():
    value = os.environ.get("LIUHAO_DEFAULT_BRANCH")
    if value:
        return value
    ref = _git("symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if ref is None:
        return None
    return ref.split("/", 1)[1] if ref.startswith("origin/") else ref


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
        print(f"  OK  {_display(path)}")
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
    wf_rel = _display(wf)
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
    if _WORKFLOWS_DIR:
        # Fixture mode: a synthetic workflow dir legitimately has no `needs:`.
        print("  (fixture mode: no `needs:` jobs in the overridden workflow dir)")
    else:
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
# 3. REACHABILITY — a gate that never runs reports nothing at all
# --------------------------------------------------------------------------- #
print()
print("=" * 72)
print("[3] job reachability on the working branch (declared-but-never-runs gates)")
print("=" * 72)

working_branch = _resolve_working_branch()
default_branch = _resolve_default_branch()
print(f"  working branch : {working_branch or 'UNRESOLVED'}")
print(f"  default branch : {default_branch or 'UNRESOLVED'}")

if working_branch is None:
    # Fail-closed: without a reference branch we cannot prove ANY gate runs, and
    # an unprovable reachability claim must not be reported as green.
    failures.append(
        "REACHABILITY: working branch UNRESOLVED (set LIUHAO_WORKING_BRANCH or "
        "GITHUB_REF_NAME, or run inside a git checkout); refusing to assert that "
        "any gated job is reachable"
    )

reachable_jobs = 0
gated_jobs = 0

for wf in WORKFLOWS:
    try:
        with open(wf) as fh:
            doc = yaml.safe_load(fh) or {}
    except Exception:  # noqa: BLE001  (unparseable -> already failed in step 1)
        continue
    jobs = doc.get("jobs", {}) or {}
    wf_rel = _display(wf)
    triggers = _trigger_map(doc)

    # Informational (NOT failing): a workflow whose triggers do not admit the
    # working branch cannot run on a direct push to it at all. Scope of this
    # gate is the job-level `if:`; PR paths may still reach it, so this stays a
    # note rather than a failure.
    if working_branch is not None and triggers:
        admits_wb = any(
            t == ALL_BRANCHES or working_branch in t for t in triggers.values()
        )
        if not admits_wb:
            print(
                f"  NOTE (informational, not failing): {wf_rel} never triggers on "
                f"'{working_branch}' (triggers: {sorted(triggers)})"
            )

    for job_name, job in jobs.items():
        if not isinstance(job, dict):
            continue
        job_if = str(job.get("if", "")).strip()
        if not job_if:
            continue  # unconditional job -> always runs when the workflow runs
        gated_jobs += 1
        events, branches, uses_default_branch = _extract_constraints(job_if)
        if not events and not branches and not uses_default_branch:
            reachable_jobs += 1
            print(f"  OK  {wf_rel}:{job_name} -> no branch/event pin (runs on every trigger)")
            continue

        reasons: list[str] = []

        # (a) EVENT LAYER: the pinned event must be one this workflow declares.
        for event in sorted(events):
            if event not in triggers:
                reasons.append(
                    f"pins github.event_name == '{event}' but this workflow's "
                    f"triggers are {sorted(triggers)}"
                )

        # (b) BRANCH LAYER (vs. this workflow's own triggers).
        admitted: object = set()
        for event, branch_set in triggers.items():
            if events and event not in events:
                continue
            if branch_set == ALL_BRANCHES:
                admitted = ALL_BRANCHES
                break
            admitted = admitted | branch_set  # type: ignore[operator]

        effective_branches = set(branches)
        if uses_default_branch:
            if default_branch is None:
                reasons.append(
                    "compares against github.event.repository.default_branch but "
                    "the default branch is UNRESOLVED (set LIUHAO_DEFAULT_BRANCH "
                    "or set origin/HEAD)"
                )
            else:
                effective_branches.add(default_branch)

        for branch in sorted(effective_branches):
            if admitted != ALL_BRANCHES and branch not in admitted:  # type: ignore[operator]
                reasons.append(
                    f"pins branch '{branch}' but this workflow only triggers on "
                    f"{sorted(admitted)}"  # type: ignore[arg-type]
                )
            # (c) WORKING-BRANCH LAYER: the class actually hit on this repo --
            # the default branch is wip/liuhao-x-evolve while work lands on p36,
            # so `ref_name == default_branch` was permanently false (the four
            # ci.yml jobs were declared but had never executed).
            if working_branch is not None and branch != working_branch:
                reasons.append(
                    f"pins branch '{branch}' but work lands on '{working_branch}' "
                    f"-> the condition is never true on the working branch"
                )

        if reasons:
            print(f"  UNREACHABLE  {wf_rel}:{job_name}")
            print(f"      if: {job_if}")
            for reason in reasons:
                print(f"      - {reason}")
            failures.append(
                f"REACHABILITY: {wf_rel}:{job_name} UNREACHABLE -- " + "; ".join(reasons)
            )
        else:
            reachable_jobs += 1
            print(f"  OK  {wf_rel}:{job_name} -> reachable on '{working_branch}'")

print()
if gated_jobs == 0:
    print("  REACHABILITY: no job-level `if:` found (nothing to prove)")
else:
    print(
        f"  REACHABILITY: {reachable_jobs}/{gated_jobs} gated job(s) reachable on "
        f"'{working_branch}' -- {gated_jobs - reachable_jobs} UNREACHABLE"
    )


# --------------------------------------------------------------------------- #
# 4. LOGIC — execute the REAL shell from action.yml
# --------------------------------------------------------------------------- #
print()
print("=" * 72)
print("[4] watchdog decision logic (real shell, injected upstream results)")
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
print(
    "RESULT: PASS -- every `needs:` job across all workflows is watchdog-protected "
    f"and all {gated_jobs} gated job(s) are reachable on '{working_branch}'"
)
print("  (real GitHub Ubuntu run remains UNVERIFIED: no act/Docker here)")
sys.exit(0)
