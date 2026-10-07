# Gate Exemption Ledger

Gates that `tests/test_guardrail_scripts.py` cannot currently verify as
*wired-and-runnable*, recorded **in one visible place** instead of being hidden
in workflow comments or a script docstring.

## Why this file exists

The meta-guardrail (`tests/test_guardrail_scripts.py`) enforces, for every
`scripts/verify_*.py`:

1. `sys.path` is bootstrapped;
2. the script can exit non-zero;
3. some workflow step actually invokes it.

It recognises only two exemption categories, and **neither fits a gate that is a
real gate but whose verdict a bare CI runner cannot produce**:

- `DIAGNOSTIC_REPORTERS` — exempts a script from (2) and (3), but is validated by
  `test_allowlist_entries_are_genuine_reporters`, which **fails** if a listed
  script has any non-zero exit path. All four gates below *can* exit non-zero
  (verified by AST), so registering them here would break that assertion rather
  than satisfy it.
- `SCHEDULED_ONLY_GATES` — a gate already invoked from a timer/manual workflow.

So this ledger is **not** a licence to skip the guard. It is the honest record of
*why* four assertions stay red, who owns each, and what must happen to turn them
green. This file is referenced from `tests/test_guardrail_scripts.py`
(`GATE_EXEMPTIONS`), so the ledger and the test cannot drift apart silently.

## Rules this ledger obeys

- **Honest red beats fake green.** An entry here never makes a test pass. It
  records a red that is understood and owned.
- **No assertion is deleted and no regex loosened** to accommodate an entry.
- Every entry has an owner and an unblock path. An entry with neither is not an
  exemption, it is an abandonment.
- An entry must be **removed** the moment the gate is genuinely wired; the test
  `test_gate_exemption_entries_are_still_needed` fails on a stale entry, so an
  exemption cannot outlive its reason.

## Host capability on the reporting machine

Measured, not assumed (this box):

- docker CLI **present**, daemon **UNREACHABLE** (`dockerDesktopLinuxEngine`
  pipe missing)
- no headless browser on `PATH`
- Prometheus/Alertmanager ports 8010 / 9090 / 9093 **closed**

A GitHub-hosted `ubuntu-latest` runner has Docker but **no** compose stack
running and no Prometheus/Alertmanager, so the three host-dependent gates below
cannot produce a valid verdict there either.

## Entries

### 1. `verify_image_build.py` — `blocked-by-host`

- **Nature:** real gate, correctly unwired from a bare runner.
- **Owner:** `g9-deploy` (image build / CD path).
- **Why exempt:** builds a staging image and drives `/v1/metrics/prometheus`.
  Requires a **reachable Docker daemon**. On this host it self-reports
  `exit 2` + `[NOT_VERIFIED] 1. docker CLI + daemon` — honest, not a false green.
  Wiring it into `guardrails-extended` would make CI permanently red for an
  *environment* reason, i.e. a fake red that trains operators to ignore red.
- **Host capability required:** reachable Docker daemon.
- **Re-run condition:** must turn green as soon as a Docker-daemon-bearing
  workflow exists (e.g. a staging/CD job). Until then it stays honestly
  `NOT_VERIFIED`, never silently green.

### 2. `verify_observability_loop.py` — `blocked-by-host`

- **Nature:** real gate, correctly unwired from a bare runner.
- **Owner:** `obs-loop` (observability loop).
- **Why exempt:** proves the Prometheus → Alertmanager → ack loop is closed.
  Requires a **live Prometheus + Alertmanager stack** on 8010/9090/9093. Here it
  self-reports `exit 1` + `VERDICT: NOT VERIFIED (missing links: L1_EXPORTER)`.
- **Host capability required:** running Prometheus + Alertmanager.
- **Re-run condition:** wire into the observability stack workflow once that
  stack is stood up; must be green from then on.

### 3. `verify_deployed_product_closure.py` — `blocked-by-host`

- **Nature:** real gate, correctly unwired from a bare runner.
- **Owner:** `deploy-real` / `g9-deploy` (product closure).
- **Why exempt:** boots a compose project, drives a **headless browser** against
  the deployed console, and asserts via `docker exec` that the artifact exists.
  Needs **both** a Docker daemon and a browser. Here it self-reports `exit 2`
  with `NOT_VERIFIED` markers.
- **Host capability required:** Docker daemon **and** headless browser.
- **Re-run condition:** wire into the deployed-product-closure workflow; must be
  green from then on.

### 4. `verify_deployment_gate_vars.py` — `pending-owner` (**NOT** host-blocked)

- **Nature:** **real gate that genuinely fails today.** This is the one entry
  in this ledger that is *not* an environment problem, and it must not be filed
  as one.
- **Owner:** `gate-var-evidence`.
- **Why exempt:** it runs cleanly and reports **16 findings** about deployment
  environment variables that no deployable artifact sets — unclassified
  gate-variable candidates (e.g. `LIUHAO_CONSOLE_DIST`,
  `LIUHAO_WORKSPACE_ROOT`) plus untracked deployment artifacts
  (`.env`, `docker-compose.yml`) that would let a gate pass on one machine and
  fail everywhere else. It needs **no Docker and no browser**; the `exit 1` is a
  true verdict about the repository, not the host. The exemption is from being
  *wired before its findings are adjudicated*, not from being wrong.
- **Why still unwired:** wiring it would be correct once the findings are
  adjudicated; wiring it *before* that would turn CI red for findings that are
  still open human decisions (SEED_GATES / ALLOWLIST classification).
- **Re-run condition:** owner classifies the 16 findings, then wires the gate.
  Because the findings are **human decisions**, this entry is deliberately
  `pending-owner`, not a permanent exemption.

## What is NOT in this ledger

The following were found during the same audit and are **wired and passing** —
recording them here would be a stale entry:

- `verify_zero_caller_decision_functions.py` — report-only but has a non-zero
  path, so it was **wired** into `guardrails-extended`, not allowlisted.
- `verify_employee_no_learning.py` — wired (default report-only; `--strict` is
  the gate).
- `verify_agent_causality_coverage.py` — wired.
- `verify_dependency_declaration_surface.py` — wired (≈206 s, placed at the end
  of the job). Introduced by `04c48193`; the `sys.path` bootstrap and CI wiring
  were added in `11c85a23`. It is **not** awaiting any owner.
- `verify_go_readiness.py` — **removed from this ledger** (was entry 5, was
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