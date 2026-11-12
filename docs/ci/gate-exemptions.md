# Gate Exemption Ledger

Gates whose verdict a **per-push** CI job cannot produce, recorded **in one
visible place** instead of being hidden in workflow comments or a script
docstring.

## Why this file exists

The meta-guardrail (`tests/test_guardrail_scripts.py`) enforces, for every
`scripts/verify_*.py`:

1. `sys.path` is bootstrapped;
2. the script can exit non-zero;
3. some workflow step actually invokes it.

It recognises only two exemption categories:

- `DIAGNOSTIC_REPORTERS` — exempts a script from (2) and (3), but is validated by
  `test_allowlist_entries_are_genuine_reporters`, which **fails** if a listed
  script has any non-zero exit path. Both gates below *can* exit non-zero
  (verified by AST), so registering them there would break that assertion rather
  than satisfy it.
- `SCHEDULED_ONLY_GATES` — a gate already invoked from a timer/manual workflow.

The two gates below are wired into
`.github/workflows/deployed-stack-verification.yml` (nightly + manual) exactly
as `SCHEDULED_ONLY_GATES` declares. They stay listed here because the ledger is
about *protection*, not invocation: a nightly-only gate runs zero times for the
next commit, and `test_gate_exemption_entries_are_still_needed` treats an entry
as stale only once some invoking job is reachable on a target branch.

## Rules this ledger obeys

- **Honest red beats fake green.** An entry here never makes a test pass. It
  records a weakness that is understood and owned.
- **No assertion is deleted and no regex loosened** to accommodate an entry.
- Every entry has an owner and an unblock path. An entry with neither is not an
  exemption, it is an abandonment.
- An entry is **removed** the moment the gate protects a target branch; the test
  `test_gate_exemption_entries_are_still_needed` fails on such a stale entry.
  Two earlier entries (`verify_image_build.py`,
  `verify_deployment_gate_vars.py`) were removed that way — see below.
- No `pending-owner` entry may stay: `test_pending_owner_exemptions_are_visible_to_ci`
  fails while one exists. The last one (`verify_deployment_gate_vars.py`) was
  wired into `ci.yml:guardrails-extended` and deleted from this ledger.

## Host capability on the reporting machine

Measured, not assumed (this box):

- docker CLI **present**, daemon **UNREACHABLE** (`dockerDesktopLinuxEngine`
  pipe missing)
- no headless browser on `PATH`
- Prometheus/Alertmanager ports 8010 / 9090 / 9093 **closed**

A GitHub-hosted `ubuntu-latest` runner has Docker but **no** compose stack
running and no Prometheus/Alertmanager, so the two host-dependent gates below
cannot produce a valid verdict on a per-push job either.

## Entries

### 1. `verify_observability_loop.py` — `blocked-by-host`

- **Nature:** real gate, intentionally scheduled-only rather than per-push.
- **Owner:** `obs-loop` (observability loop).
- **Why exempt:** proves the Prometheus → Alertmanager → ack loop is closed.
  Requires a **live Prometheus + Alertmanager stack** on 8010/9090/9093 plus a
  gateway exporting `/v1/metrics/prometheus`. Here it self-reports `exit 1` +
  `VERDICT: NOT VERIFIED (missing links: L1_EXPORTER)`. It now runs for real in
  `deployed-stack-verification.yml` (`--mode docker`, nightly + manual), but that
  invocation protects nothing on the next commit, which is what this entry
  records.
- **Host capability required:** running Prometheus + Alertmanager.
- **Re-run condition:** turn green in `deployed-stack-verification.yml`; it stays
  recorded until it is invoked from a job that runs on every push.

### 2. `verify_deployed_product_closure.py` — `blocked-by-host`

- **Nature:** real gate, intentionally scheduled-only rather than per-push.
- **Owner:** `deploy-real` / `g9-deploy` (product closure).
- **Why exempt:** boots a compose project, drives a **headless browser** against
  the deployed console, and asserts via `docker exec` that the artifact exists.
  Needs **both** a Docker daemon and a browser, plus several minutes of image
  build. Here it self-reports `exit 2` with `NOT_VERIFIED` markers. It now runs
  for real in `deployed-stack-verification.yml` (nightly + manual), but that
  invocation protects nothing on the next commit, which is what this entry
  records.
- **Host capability required:** Docker daemon **and** headless browser.
- **Re-run condition:** turn green in `deployed-stack-verification.yml`; it stays
  recorded until it is invoked from a job that runs on every push.

## What is NOT in this ledger

The following were found during the same audit and are **wired and passing** —
recording them here would be a stale entry:

- `verify_image_build.py` — **removed from this ledger** (was entry 1, was
  `blocked-by-host`). Wired into `ci.yml:build`, which runs on every push on a
  runner with a real Docker daemon; the gate builds the staging image and boots
  it. It self-reports exit 2 (`NOT_VERIFIED`) if the daemon disappears, so the
  wiring cannot turn into a silent pass.
- `verify_deployment_gate_vars.py` — **removed from this ledger** (was entry 4,
  was `pending-owner`). Wired into `ci.yml:guardrails-extended`, which runs on
  every push; it needs no Docker and no browser. Its own verdict is still
  non-zero while the 11 findings (gate-variable candidates such as
  `LIUHAO_CONSOLE_DIST`, plus unarmed seed gates) are unadjudicated — that is an
  honest repository verdict, not a wiring gap, so it does not belong here.
- `verify_zero_caller_decision_functions.py` — report-only but has a non-zero
  path, so it was **wired** into `guardrails-extended`, not allowlisted.
- `verify_employee_no_learning.py` — wired (default report-only; `--strict` is
  the gate).
- `verify_agent_causality_coverage.py` — wired.
- `verify_dependency_declaration_surface.py` — wired (≈206 s, placed at the end
  of the job). Introduced by `04c48193`; the `sys.path` bootstrap and CI wiring
  were added in `11c85a23`. It is **not** awaiting any owner.
- `verify_go_readiness.py` — removed from this ledger (was entry 5, was
  `pending-owner`). Its owner (`g9-deploy`) made `ci-cd.yml:build` reachable: the
  stale `push: [main, develop]` / `pull_request: [main]` triggers were replaced
  by `workflow_dispatch` + `push: tags: ['v*']`, and the
  `github.ref == 'refs/heads/main'` job pin was dropped — the job keeps the
  literal `if: always()` that the CRITICAL-5 watchdog mandates (it rejects
  `!cancelled()`), so the gate is now wired into a reachable job. The gate's OWN
  verdict is still
  `GO_STATUS: UNVERIFIED` / exit 1 while HC-01 is forked — that is an honest gate
  verdict, **not** a wiring gap, so it does not belong in this ledger. (The HOLD
  control runs before it and is default-armed, so on a normal release attempt the
  job stops at HOLD; the GO gate executes once the freeze is lifted.)
