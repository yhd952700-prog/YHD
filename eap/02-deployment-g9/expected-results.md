# 02 · Expected Results — Deployment (G9)

Per-check PASS thresholds and the evidence each must produce. A gate is only
flipped to PASS by the final battery when the corresponding evidence file shows
the check actually passed on a real host.

## G9b — Image build + boot + health (`verify_image_build.py`)

| Check | PASS threshold | Evidence field |
|-------|---------------|---------------|
| 1. docker CLI + daemon | `docker version` returns a Server version (daemon reachable) | check status `PASS` |
| 2. frozen-evidence guard | Dockerfile does **not** `COPY audit_store.db`; `.dockerignore` excludes `*.db` (hygiene) | check status `PASS` |
| 3. docker build | `docker build` exits `0`; `docker image inspect` succeeds | `facts.image_id`, `facts.image_size_mb`, `facts.duration_s` |
| 4. container boot + health | container `State.Status == running` **and** `Health.Status == healthy`; all 3 endpoints return HTTP 200 with non-empty body | `facts.health_status`, `facts.endpoints` |
| 5. frozen evidence untouched | `audit_store.db` SHA-256 identical before/after | check status `PASS` |

**Overall G9b verdict:** `PASS` only when no `FAIL` and no `NOT_VERIFIED`.
**Exit code:** `0` = all PASS · `1` = a real FAIL · `2` = NOT_VERIFIED (no
daemon).

## G9 — Deploy readiness (`verify_deploy_readiness.py`)

| Check | PASS threshold | Evidence field |
|-------|---------------|---------------|
| A. docker CLI availability | `docker --version` succeeds | check status `PASS` |
| B. alembic upgrade/rollback | `upgrade head` rc=0; `downgrade base` rc=0; after rollback only `alembic_version` remains; `verify_orm_vs_db.py` and `verify_persistence.py` PASS on the temp db | check status `PASS` + detail |
| C. staging compose validity | `docker compose -f infra/staging/docker-compose.yml config` succeeds **OR** the python YAML-schema sanity check passes (with the honest gap noted) | check status `PASS` / `NOT_VERIFIED` |

**Overall G9 deploy verdict:** `FAIL` if any check is `FAIL`. A `NOT_VERIFIED`
gap (e.g. compose plugin absent) is recorded but does **not** hard-fail (exit
`0`). The final battery treats an unreplaced `NOT_VERIFIED` as **not proven**.

## G9 — Observability loop (`verify_observability_loop.py`)

See `../03-observability-alerting/expected-results.md`. The links L1…L6 must all
be `PASS` in a single app-UP run for the closed loop to be verified; the
app-DOWN run separately proves `ServiceDown`/`LiuhaoServiceDown` (L1/L2/L4 are
SKIPPED there by design).

## What "G9 PASS" does and does not mean

- **DOES mean:** a staging image built locally, booted healthy, answered the
  contract endpoints; the migration set is reversible on a temp db; the staging
  compose is structurally valid.
- **DOES NOT mean:** production deployment verified — no registry push, no real
  target host, no secrets, no live rollback, no traffic. Production promotion is
  a **human decision** (`00-overview.md`).
