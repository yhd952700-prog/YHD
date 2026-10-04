# EAP-05 — Final readiness battery

One script answers one question:

> Given the evidence actually produced on a real host, may this build be called
> `PRODUCT ACCEPTANCE = PASS` and `RELEASE READINESS = RELEASE READY`?

Script: `consolidate_readiness.py` (standard library only — no third-party deps).

---

## The one rule it enforces

**Absent evidence can never become a PASS.**

There is deliberately no "default to green" branch. If an evidence file is
missing, unreadable, or its shape is unrecognised, the check is reported as
`NOT VERIFIED` / `BLOCKED-on-host`. The only thing that flips a gate to `PASS`
is an evidence file showing that the check passed on a real host.

This exists because the tempting failure mode in this project is real: treating
"the runbook is written" or "the spec was authored" as if the product had been
proven. It has not.

---

## Usage

```bash
python eap/05-final-readiness-battery/consolidate_readiness.py \
  --browser-report   eap/01-browser-click-through/playwright-report.json \
  --g9-report        eap/05-final-readiness-battery/g9b_image_build_report.json \
  --deploy-report    eap/05-final-readiness-battery/g9_deploy_readiness_report.json \
  --obs-up-report    eap/05-final-readiness-battery/obs_loop_up_report.json \
  --obs-down-report  eap/05-final-readiness-battery/obs_loop_down_report.json \
  --regression-report eap/05-final-readiness-battery/regression-report.json \
  --product-readiness docs/product/PRODUCT-READINESS.md \
  --out-dir          eap/05-final-readiness-battery
```

Every argument is optional — supplying nothing yields the honest "we ran
nothing, so we know nothing" report.

Outputs: `EAP-EVIDENCE-REPORT.md` + `EAP-EVIDENCE-REPORT.json`.

---

## Verdict logic

```
PRODUCT ACCEPTANCE
  FAIL        if ANY of P1/P5/P9/P10 reports FAIL
  PASS        if ALL of P1/P5/P9/P10 report PASS
  NOT READY   otherwise (any BLOCKED-on-host / NOT VERIFIED)

RELEASE READINESS
  RELEASE READY   if PRODUCT ACCEPTANCE == PASS
                     AND G9-image == PASS
                     AND G9-upgrade-rollback == PASS
                     AND OBS-firing-up == PASS
  NOT RELEASE READY otherwise
```

Exit codes match the repo's gate convention:

| Code | Meaning |
|------|---------|
| `0` | every EAP gate evidence-backed PASS → `PRODUCT ACCEPTANCE = PASS` |
| `1` | at least one real FAIL |
| `2` | no FAIL, but something is BLOCKED-on-host / NOT VERIFIED |

---

## Per-gate evidence mapping

| Gate | Source of truth | Pass requires |
|------|-----------------|---------------|
| P1  | `p1_console.spec.ts` in the Playwright JSON | ≥1 test `expected`, none `unexpected` |
| P5  | `p5_sovereignty.spec.ts` | same |
| P9  | `p9_identity.spec.ts` | same |
| P10 | `p10_workflow.spec.ts` | same |
| G9-image | `verify_image_build.py` JSON | all named checks PASS |
| G9-upgrade-rollback | `verify_deploy_readiness.py` JSON | all checks PASS |
| OBS-firing-up / down | `verify_observability_loop.py --json` | L5 + L6 proven |
| REGRESSION | `run-regression.py --json` | top-level `status == PASS` |

Deliberate decision on **skips**: an intentionally skipped Playwright test
(e.g. the P5 machine-principal negative case, which needs seed data the UI
cannot create) does **not** block a PASS — provided at least one test really
executed. But a gate whose tests are **all** skipped is downgraded to
`NOT VERIFIED`, because nothing was actually exercised.

---

## This script was verified in both directions (not merely written)

While authoring this package the following three behaviours were executed and
observed — a consolidator stuck permanently at "NOT READY" would be as useless
as one that always says PASS:

| Scenario | Observed result | Exit |
|----------|-----------------|------|
| **No evidence supplied** | all gates `BLOCKED-on-host` / `NOT VERIFIED`; `PRODUCT ACCEPTANCE: NOT READY`; no gate invented as passing | `2` |
| **Synthetic all-green evidence** | all 9 checks `PASS`; `PRODUCT ACCEPTANCE: PASS`; `RELEASE READINESS: RELEASE READY` | `0` |
| **One failing Playwright test** | `P1: FAIL`; `PRODUCT ACCEPTANCE: FAIL`; `NOT RELEASE READY (a gate FAILED)` | `1` |

P5 also correctly reported PASS with one passed + one skipped test, confirming
the deliberate skip handling.

---

## What this report does NOT decide

- **Where** the image is promoted (registry, target host, cluster).
- Real secrets and their source.
- Database backend for non-staging.
- Production scale / SLO.
- Production Alertmanager sink routing (see the disclosed gap in `03-*`).

Those remain **SOVEREIGN HUMAN DECISIONS**. This battery only reports whether
the engineering gates have been *proven*, never whether the human has approved
promotion.
