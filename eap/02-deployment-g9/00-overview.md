# 02 · Deployment (G9) — Overview

G9 is the **deployment / upgrade / rollback / observability** acceptance layer
for LIUHAO AI OS. It proves the product can be turned into a real, bootable,
observable, reversible deployment artifact — **without** claiming a production
deployment happened.

G9 is split across two scripts and this package's runbooks:

| Script | What it proves | Runbook |
|--------|---------------|---------|
| `scripts/verify_image_build.py` | Real image **build → boot → health → HTTP** on the three contract endpoints, plus a frozen-evidence (audit_store.db) digest guard. | `runbook-build-boot-health.md` |
| `scripts/verify_deploy_readiness.py` | **Alembic upgrade head + downgrade base** on a TEMP db (reversible migration set) + staging compose config validity. | `runbook-upgrade-rollback.md` |
| `scripts/verify_observability_loop.py` | The **real alert pipeline FIRES** (not just metrics 200). | `../03-observability-alerting/runbook-alert-firing.md` |

---

## What G9 needs (environment)

- A **reachable Docker daemon** — `docker info` returns a server version.
  In the authoring host, the daemon is absent, so the image-build gate is
  `NOT_VERIFIED` (exit 2), not `FAIL`.
- The repo **`.venv`** with `alembic`, `sqlalchemy`, `pyyaml` for the
  migration checks. The managed interpreter may lack `alembic` — use the repo
  `.venv`.
- A `docker compose` **v2 plugin** for the *authoritative* staging-compose
  validation. In the authoring env the plugin was absent, so only a python
  YAML-schema sanity check ran (`NOT_VERIFIED` gap, exit 0 with a warning). On a
  plugin-equipped host, run `docker compose -f infra/staging/docker-compose.yml
  config` to close it.

---

## Production vs Staging contract

- `docker-compose.prod.yml` describes a **production-shaped** stack: `api`
  (single-port image `liuhao-ai-os:latest`, `8080:8080`, volume
  `liuhao_data:/app/data`, `LIUHAO_KERNEL_POLICY_ENFORCE=CRITICAL` by default) +
  `nginx` (80→api) + optional `postgres`/`redis` behind the `infra` profile.
- `infra/staging/docker-compose.yml` is a **STAGING/TEST contract ONLY**. It
  builds the image from the repo Dockerfile, pins `liuhao-ai-os:staging`, sets
  hard resource limits, a real `/v1/health` healthcheck, and contains **no real
  production secrets** (dev-only placeholders only).

> ### ⚠️ HUMAN DECISION REQUIRED — production promotion
>
> There is **no helm/k8s**, and the project's CD jobs `exit 1` **by design**.
> Promoting `infra/staging/docker-compose.yml` (or `docker-compose.prod.yml`)
> to a *real* production target — choice of host, real secrets, scale, approval
> gate — is a **sovereign human decision** and is intentionally **not**
> automated here. A PASS from `verify_image_build.py` means "a staging image
> booted in a container and answered the contract endpoints locally" — it does
> **not** mean "production deployment verified". See the script's own header
> comment for the exact scope statement.

---

## How the two G9 scripts differ on exit codes (important)

| Script | exit 0 | exit 1 | exit 2 |
|--------|--------|--------|--------|
| `verify_image_build.py` | every check PASS | ≥1 real FAIL | **NOT_VERIFIED** (no daemon / unbuildable env) |
| `verify_deploy_readiness.py` | no hard FAIL (NOT_VERIFIED gaps allowed) | ≥1 real FAIL | *n/a* — NOT_VERIFIED exits **0** with a warning |

This asymmetry is deliberate: the image gate treats "environment cannot execute"
as non-zero (you should notice a missing daemon), while the deploy-readiness
gate treats "couldn't prove the compose plugin" as a recorded gap, not a hard
blocker. `failure-interpretation.md` explains both.

---

## Evidence produced by G9

- `g9b_image_build_report.json` (path via `G9B_REPORT`) — 5 checks + facts
  (image id, size, duration, endpoint results, frozen-db digest).
- `g9_deploy_readiness_report.json` (path via `G9_REPORT`) — checks A/B/C.
- `obs_loop_*.json` (via `verify_observability_loop.py --json`) — see `03-*`.

All of these are consumed by `../05-final-readiness-battery/consolidate_readiness.py`.
