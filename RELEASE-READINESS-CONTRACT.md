# RELEASE-READINESS-CONTRACT — LIUHAO AI OS

> **Purpose.** This document is the *single machine-checkable contract* for declaring the
> system **RELEASE READY**. It is consumed by `scripts/verify_readiness.py`, which executes
> the checkers described here and emits a verdict + evidence. No other document, percentage,
> or "green pipeline" substitutes for these 12 conditions.
>
> **Honesty rule (non-negotiable).** A condition is `PASS` only when its checker has
> *executed and produced real evidence* in this environment. Absent evidence ⇒ `NOT VERIFIED`.
> A blocked human-sovereignty decision ⇒ `BLOCKED`. A real contradiction of the acceptance
> criterion ⇒ `FAIL`. A checker that could not RUN at all — its tool/harness rejected the
> invocation (e.g. pytest `unrecognized arguments: --timeout` when `pytest-timeout` is absent),
> or the runner module was missing — ⇒ `ERROR`: a verification-process failure, deliberately
> distinct from an honest `NOT VERIFIED` so a missing plugin cannot masquerade as a recorded
> gap. We never mark `PASS` on documentation alone.
>
> Branch: `p36`. Repo: `D:\LiuHao-AI-OS`. Contract version: **1.0** (2026-09-29).

---

## Overall Release Verdict

`RELEASE READY` is awarded **only when all 12 conditions are `PASS`** (no `FAIL`, no
`NOT VERIFIED`, no `BLOCKED`, no `ERROR`). Any `FAIL` or `BLOCKED` is a hard release blocker.
Any `NOT VERIFIED` means the evidence has not been produced yet and the condition is, by
definition, not satisfied for release. Any `ERROR` means a checker could not even run — also
not satisfied, and additionally a harness/declaration defect to fix.

`scripts/verify_readiness.py` computes this verdict and writes `scripts/readiness_report.json`.
That file is a **run artifact, not a committed one**: it embeds `generated_at`, the
interpreter (`environment.python`), and plugin/profile-dependent gate outcomes, so a
checked-in copy would quickly read as current evidence when it is not. Regenerate it
(`python scripts/verify_readiness.py`) rather than trusting a copy; it is gitignored.

---

## The 12 Conditions (machine-checkable)

Each condition names the checker implemented in `scripts/verify_readiness.py` (function
`check_<id>`) and the concrete acceptance test.

### C1 — Build passes
- **Checker:** `check_build`
- **Acceptance:** `python -m compileall -q src` succeeds AND the documented entry modules
  (`src.gateway.main:get_app`, `src.kernels.execution.fence`, `src.distribution.coordination`,
  `src.security.secret_store`) import without error.
- **PASS:** compile + import clean. **FAIL:** syntax/import error.

### C2 — Clean-environment runnable
- **Checker:** `check_clean_env_run`
- **Acceptance:** In a subprocess with a *clean* environment (no `LIUHAO_*` secrets,
  `LIUHAO_ENV` unset), the gateway boots via `TestClient` and `GET /v1/health` returns 200.
- **PASS:** 200 with no unexpected exception. **FAIL:** boot or health fails.
  **NOT VERIFIED:** boot raises due to environment/config gaps that are not product defects.

### C3 — Stable tests
- **Checker:** `check_tests`
- **Acceptance:** `pytest tests/ --co` collects the expected suite AND the recorded baseline
  (`scripts/test_baseline.json`, if present) shows **0 unexplained failures**. A failing test
  that is a known, diagnosed, fixed isolation defect with a recorded regression test is not
  "unexplained".
- **PASS:** 0 unexplained failures. **FAIL:** unexplained failure. **NOT VERIFIED:** full
  clean-env re-run not yet recorded in the baseline.

### C4 — Critical security blocker = 0
- **Checker:** `check_security`
- **Acceptance:** (a) a secret scan over `src/` finds no hardcoded credential patterns;
  (b) `src/security/secret_store.py` contains the fail-closed path (raises when no backend);
  (c) CI config declares Bandit (high, blocking) and Semgrep (`--error`).
- **PASS:** scan clean + fail-closed present + static gates declared. **FAIL:** any
  contradiction. **NOT VERIFIED:** static gates not yet executed in this environment.

### C5 — Data integrity passes
- **Checker:** `check_data_integrity`
- **Acceptance:** `scripts/verify_p08_hash_chain_sig_alg.py` returns COMPLIANT (32/32) and
  `scripts/verify_p08b_chain_matrix.py` (HC-01) returns its recorded status. HC-02..HC-08 and
  HC-11 must be COMPLIANT; HC-09/HC-10 are VOLATILE by design (not a failure).
- **PASS:** required chains COMPLIANT. **FAIL:** any required chain UNVERIFIED/contradicted.

### C6 — Crash / recovery / failover passes
- **Checker:** `check_reliability`
- **Acceptance:** the chaos/soak suite (`tests/kernels/audit/test_audit_soak_concurrency.py`,
  `test_storage_faults.py`, `tests/distribution/test_fence_coordination_chaos.py`) runs and the
  fail-closed assertions hold (replay denied on backend outage, no double-execution on crash).
- **PASS:** scenarios pass. **FAIL:** a fail-closed guarantee is broken. **NOT VERIFIED:**
  scenarios require live infrastructure not present here.

### C7 — Performance meets scale target
- **Checker:** `check_performance`
- **Acceptance:** `scripts/bench_baseline.json` records a profile that matches this machine AND
  `scripts/bench_audit_append.py --quick --gate` passes on that profile; the scale target
  (1M/10M build + verify within ceiling) is met.
- **PASS:** gate passes on matching profile. **NOT VERIFIED:** profile mismatch (CI skips) or
  100M target undecided.

### C8 — Observability + operations complete
- **Checker:** `check_observability`
- **Acceptance:** `/v1/metrics/prometheus` and `/v1/ready` endpoints exist and a runtime scrape
  in CI returns populated metrics; alert rules are wired.
- **PASS:** scrape proves live population. **NOT VERIFIED:** endpoints exist but no runtime
  scrape performed here.

### C9 — Deployment / upgrade / rollback verifiable
- **Checker:** `check_deployment`
- **Acceptance:** Docker image builds; `alembic upgrade head` + `verify_orm_vs_db.py` pass; a
  rollback (`alembic downgrade`) path exists. A real CD (helm/k8s or staging) is *not* required
  for this condition, but must be honestly recorded as absent.
- **PASS:** build + migration up/down verifiable. **NOT VERIFIED:** real CD absent (honest).

### C10 — Independent verification done
- **Checker:** `check_independent_verification`
- **Acceptance:** a standing, builder-decoupled verification harness consumes the runtime
  evidence scripts and emits a signed verification record.
- **PASS:** harness exists and ran. **NOT VERIFIED:** only self-attested guardrail scripts exist.

### C11 — HC-01 real verification
- **Checker:** `check_hc01`
- **Acceptance:** the audit master chain HC-01 is verified against the immutable Phase-3.6
  evidence under `D:\WorkBuddyFiles\LIUHAO-Phase3.6-Evidence\`.
- **Status:** **BLOCKED / GO=BLOCKED** — HUMAN DECISION PENDING. This is a frozen
  human-sovereignty decision and, by Decision Isolation, does **not** block the other 11
  conditions or engineering work. Until the human decision lands, C11 = `BLOCKED` and the
  system cannot be declared RELEASE READY on HC-01 grounds alone.

### C12 — No unexplained major failures
- **Checker:** `check_no_major_failures`
- **Acceptance:** cross-references the test baseline, the gate scripts, and the reliability
  suite; there is no major failure (data loss, security bypass, crash on core flow) that is
  unexplained or unmitigated.
- **PASS:** none. **FAIL:** an unexplained major failure exists. **NOT VERIFIED:** full
  cross-reference not yet performed in a clean environment.

---

## Machine-checkable gate contract (for CI)

`scripts/verify_readiness.py`:
- Runs every `check_<id>` above.
- Emits a markdown table (status + evidence) to stdout and a JSON report to
  `scripts/readiness_report.json` (a **run artifact** — gitignored, regenerated per
  run, never a checked-in verdict).
- Exit code **0** when there are **no `FAIL` and no `ERROR`** results; exit code **2** when
  one or more conditions `FAIL` **or** `ERROR`. `NOT VERIFIED` / `BLOCKED` do **not** cause a
  non-zero exit today (they are pending verification, not contradictions). `ERROR` **does**: a
  check that could not be evaluated is a failure of the verification process, not a pending
  item. The JSON report is the authoritative RELEASE READY verdict and CI should treat
  `RELEASE READY != true` as a soft gate until the contract is tightened.
- Supports `--quick` (skip the 600s full suite; rely on recorded baseline) and `--full`
  (attempt the full `pytest tests/` run).

---

## Recorded status — 2026-09-29 baseline snapshot (SUPERSEDED — NOT current)

> **Do not read this table as the current verdict.** It is the historical
> 2026-09-29 baseline, preserved verbatim. Its "Recorded status" column was
> accurate *when written* and has since been superseded as gates were verified
> (see the *Current verdict* line below and `MASTER_PROJECT_READINESS.md`). The
> authoritative current verdict is always the regenerated
> `scripts/readiness_report.json` — a run artifact; regenerate it, never trust a
> copy. Labeling this snapshot "current" would itself be a stale-as-current
> false claim (the exact defect class tracked in
> `docs/governance/CONTROL-CLAIM-LEDGER.md`).

| ID | Condition | Recorded status (2026-09-29) |
|----|-----------|----------------|
| C1 | Build passes | NOT VERIFIED |
| C2 | Clean-env runnable | NOT VERIFIED |
| C3 | Stable tests | NOT VERIFIED (3202 passed / 1 failed / 22 skipped pre-fix; that 1 was an isolation defect now fixed) |
| C4 | Critical security blocker = 0 | NOT VERIFIED (mechanisms real + fail-closed; static gates not executed here) |
| C5 | Data integrity passes | NOT VERIFIED (HC-02..08 + HC-11 COMPLIANT runtime; HC-09/10 VOLATILE) |
| C6 | Crash/recovery/failover | PASS (chaos/soak/storage-fault suites execute + pass; missing power-loss/partition scenarios) |
| C7 | Performance meets scale | NOT VERIFIED (profile-gated; 1M/10M met; 100M undecided) |
| C8 | Observability + ops | NOT VERIFIED (endpoints exist; no runtime scrape) |
| C9 | Deployment/upgrade/rollback | NOT VERIFIED (build+migration verifiable; no real CD) |
| C10 | Independent verification | NOT VERIFIED (no standing harness) |
| C11 | HC-01 real verification | BLOCKED (human decision pending; isolated) |
| C12 | No unexplained major failures | NOT VERIFIED |

**Overall (2026-09-29 baseline): NOT RELEASE READY** — at that date the verdict was gated by
`NOT VERIFIED` on C1–C10 and `BLOCKED` on C11. No `FAIL` was recorded.

**Current verdict (supersedes the 2026-09-29 table above):** `release_ready = false`, unchanged.
The authoritative machine verdict recorded in `MASTER_PROJECT_READINESS.md` (run **2026-10-01**) is
**PASS = 9, FAIL = 0, BLOCKED = 1 (C11 / HC-01), NOT VERIFIED = 2 (C3 / C12)** — i.e. most gates
that read `NOT VERIFIED` in the historical table have since been verified, and the only release
gates not at `PASS` are C11 (HC-01: a frozen human-sovereignty decision, `GO = BLOCKED`) plus
C3/C12. Engineering-critical blockers: none (HC-01 is frozen & isolated, not an engineering
failure). This contract does not restate a "current" per-gate table on purpose — regenerate
`scripts/readiness_report.json` for the machine verdict.
