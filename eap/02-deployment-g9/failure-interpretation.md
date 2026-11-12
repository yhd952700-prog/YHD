# 02 · Failure Interpretation — Deployment (G9)

How to read non-zero exits and non-PASS statuses from the G9 scripts, and why
**exit 2 ≠ product failure**.

## `verify_image_build.py` — exit codes

| Exit | Meaning | Is it a product failure? | Action |
|------|---------|--------------------------|--------|
| `0` | every check PASS | n/a (it passed) | capture `G9B_REPORT`, feed the battery |
| `1` | ≥1 check is a real `FAIL` | **Possibly** — investigate the named check | read the printed `[FAIL]` lines + `detail`; fix root cause (e.g. bad Dockerfile, endpoint 500) |
| `2` | **NOT_VERIFIED** — env cannot execute (no docker CLI, or CLI present but daemon unreachable) | **NO** — this is a *gap*, not a proven defect | This is the exact state on the authoring host (Docker Desktop installed, engine not running / WSL2 backend unavailable). Install/start Docker, re-run. **Do not** infer buildability from CLI presence. |

> The script deliberately returns **non-zero (2)** for "environment missing"
> because a CI job that silently goes green when the daemon vanished is worse
> than one that turns red. A `2` means *"we could not prove this here"*, never
> *"the image is broken"*.

### Reading the JSON report (`g9b_image_build_report.json`)
- `summary.verdict`: `"PASS"` | `"FAIL"` | `"NOT_VERIFIED"`.
- `summary.checks[]`: each with `status` (`PASS`/`FAIL`/`NOT_VERIFIED`),
  `evidence`, `detail[]`.
- `summary.facts`: `image_id`, `image_size_mb`, `duration_s`, `health_status`,
  `endpoints` (per-endpoint `http_status`/`ok`), `log_error_markers`.
- `summary.production_target`: always `"HUMAN_DECISION_REQUIRED"`.
- `summary.image_pushed_to_registry`: always `false` (build is local only).

A `NOT_VERIFIED` in `checks[]` (e.g. check 3/4 when the daemon is absent) is a
recorded gap — the battery maps it to **BLOCKED-on-host**, never to PASS.

## `verify_deploy_readiness.py` — exit codes

| Exit | Meaning | Note |
|------|---------|------|
| `0` | no hard `FAIL` (zero or more `NOT_VERIFIED` gaps recorded with a warning) | **NOT_VERIFIED does not fail the run** here — unlike the image gate |
| `1` | ≥1 check is `FAIL` | real defect: fix it |

Key asymmetry to keep straight:
- Image gate (`verify_image_build.py`): env gap → **exit 2** (loud).
- Deploy gate (`verify_deploy_readiness.py`): env gap (e.g. compose plugin
  absent) → **exit 0** with a `NOT_VERIFIED` warning (quiet-by-design, because
  the migration proof itself does not need the daemon).

The final battery reads both JSONs and applies the **same honest rule**: a gate
is only `PASS` when the evidence file shows the check actually passed; otherwise
it is `BLOCKED-on-host` or `NOT VERIFIED` — never `PASS-by-assumption`.

## Common failure modes and their meaning

| Symptom | Cause | Real failure? |
|---------|-------|--------------|
| check 4 FAIL: "container never became healthy" | app crashed at boot (e.g. missing env, policy-enforce misconfig) | **Yes** — fix the boot |
| check 4 FAIL: "refusing to publish host port 8000" | you used a reserved port | No — re-run with 18000 |
| check 4 FAIL: endpoint returned 200 but empty body | exporter not wired / route missing | **Yes** — investigate `/v1/metrics/prometheus` |
| check 5 FAIL: "DIGEST CHANGED" | `audit_store.db` was modified during the run | **Yes (incident)** — frozen HC-01 evidence was tampered |
| check B FAIL: "downgrade base did not fully revert" | a migration lacks a downgrade | **Yes** — fix the migration |
| check C NOT_VERIFIED: compose plugin absent | authoring env gap | No — run `docker compose config` on a plugin host |
