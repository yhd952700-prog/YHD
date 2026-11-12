# EAP-04 — Full regression: expected results

Owner script: `eap/04-full-regression/run-regression.py` (standard library only).

---

## Exit codes

Inherited deliberately from the repository's established gate convention
(same semantics as `scripts/verify_image_build.py` and
`scripts/verify_observability_loop.py`):

| Code | Meaning | Downstream effect |
|------|---------|-------------------|
| `0`  | PASS — every executed test passed | `run-regression.py` → `REGRESSION: PASS` in the battery |
| `1`  | FAIL — a real failure/error, **or** a chunk collected zero tests | hard fail, never promoted |
| `2`  | NOT VERIFIED — pytest cannot be invoked / nothing was executed | recorded gap, never a pass |

A chunk that collects **zero** tests is treated as FAIL, not as silence. Tests
that vanish are exactly the kind of regression this suite exists to catch.

---

## What a clean run looks like

```
REGRESSION PASS: 3287 tests, 3250 passed, 0 failed, 0 error, 37 skipped
JSON report written to: eap/05-final-readiness-battery/regression-report.json
```

Interpretation of the counters:

- **`tests`** — count discovered from pytest's own JUnit XML, summed per chunk.
  This is pytest's accounting, not our scraping of console text.
- **`failed` / `error`** — any non-zero value ⇒ exit `1`. No threshold, no
  tolerance. This suite has no "known failures" exemption list by design.
- **`skipped`** — reported for visibility. Skips are **not** treated as failure,
  but they are also **not** counted as proof. A gate whose tests are all skipped
  is reported NOT VERIFIED by the battery.

---

## The baseline you are comparing against

Measured facts carried over from prior cycles (do not re-derive, but DO re-measure):

- The **sovereignty / policy-approval** subset is **107 passed** across
  `tests/kernels/test_sovereignty_c3.py`, `test_sovereignty_grants.py`,
  `test_sovereignty_claim_integrity.py`, `tests/test_policy_approval_http.py`,
  `tests/test_policy_approval_api.py`.
- Runner discovery found **27 top-level test chunks** under `tests/`
  (verified live by `--collect-only` while authoring this package).
- The whole suite is large; expect hundreds of seconds. This is precisely why
  the runner chunks by top-level directory rather than one mega-invocation.

### The order-dependence lesson (why you can trust a green run)

`tests/test_policy_approval_http.py::test_enforcement_reports_the_real_arming`
previously assigned `os.environ[LIUHAO_KERNEL_POLICY_ENFORCE] = "CRITICAL"`
directly **without restoring it**. The value leaked into
`tests/test_policy_approval_api.py::test_default_is_off`, which asserted that
enforcement is **off by default**. Under whole-suite ordering that module could
therefore pass even with a broken default — because an earlier armed test had
already flipped the switch.

That was fixed with `monkeypatch.setenv` (auto-restoring). Combined run after the
fix: **107 passed**.

Consequence for this runner: **a green whole-suite result is only trustworthy
because that leak was removed.** If you ever relax a default and this suite
still goes green, suspect ordering before believing the result.

---

## Evidence produced

`regression-report.json` contains, per chunk:

- `chunk`, `target`, `executed`, `status`
- `returncode`, `timed_out`
- `counts` → `tests` / `passed` / `failed` / `error` / `skipped`
- `failing_examples` (up to 40 node ids per chunk)
- `tail` (last 25 console lines, for human triage)

plus aggregate `totals` and `status`.

The battery reads the top-level `status` field. It does not re-derive it.

---

## Recommended invocation on a capable host

```bash
python eap/04-full-regression/run-regression.py \
  --json eap/05-final-readiness-battery/regression-report.json \
  --timeout 1800
```

Use `--whole` **only** when you are certain no other pytest process is running:
concurrent pytest invocations are known to corrupt results in this repository.
