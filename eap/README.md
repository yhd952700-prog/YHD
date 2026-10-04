# LIUHAO AI OS — External Acceptance Package (EAP)

> Branch: `p36` · Authoring environment: Windows + Git-Bash, **no reachable Docker
> daemon, no headless browser, no prometheus/alertmanager binaries**.
> This package is **self-prepared and host-ready**: it authors every runbook and
> automation, but **does not execute** the gates here. The moment a capable host
> exists, run the scripts and the gates flip.

---

## 1. Purpose

The EAP is a **self-contained, host-ready acceptance package** for the LIUHAO
AI OS. It collects the orchestration/runbook layer that proves the product can
be:

1. **Built and booted** as a real container image (G9 image gate),
2. **Upgraded and rolled back** safely (G9 deploy-readiness gate),
3. **Observed end-to-end** with a *real* firing alert pipeline (not just
   "metrics answer 200"),
4. **Regressed fully** against the pytest suite (fail-closed), and
5. **Consolidated** into a single, honest evidence report + recommendation.

It is deliberately **decoupled from product source** (`src/`) and from the
browser click-through work owned by another worker under
`eap/01-browser-click-through/`.

**Nothing here modifies `src/`, pushes an image, or makes a production
decision.** Production promotion (real target host, secrets, scale) remains a
**SOVEREIGN HUMAN DECISION** — see `02-deployment-g9/00-overview.md`.

---

## 2. Scope

The EAP covers exactly these acceptance surfaces:

| # | Surface | Owner (in this repo) | Evidence artifact |
|---|---------|----------------------|-------------------|
| P1, P5, P9, P10 | Browser click-through (4 gates) | `eap/01-browser-click-through/` (another worker) | Playwright JSON report |
| G9 | Image build + boot + health | `02-deployment-g9/` (this package) | `g9b_image_build_report.json` |
| G9 | Upgrade / rollback (alembic) + staging compose | `02-deployment-g9/` | `g9_deploy_readiness_report.json` |
| G9 | Observability closed-loop firing | `03-observability-alerting/` | `obs_loop_report.json` |
| — | Alert-rule dead-rule proof (static) | `03-observability-alerting/` | stdout (CI guard, exit 0/1) |
| — | Full regression (pytest) | `04-full-regression/` | `regression-report.json` |
| — | Final readiness battery | `05-final-readiness-battery/` | `EAP-EVIDENCE-REPORT.md` + `.json` |

> The four browser gates (P1/P5/P9/P10) are **authored by a separate worker**.
> This package references their expected output (`eap/01-browser-click-through/`
> Playwright JSON) but never creates or edits anything under that directory.

---

## 3. Prerequisites (what a "capable host" must have)

| Capability | Why | Check |
|------------|-----|-------|
| **Reachable Docker daemon** | G9 image build/boot + staging compose (`docker compose config`) | `docker info` returns server version |
| **`docker compose` v2 plugin** | Authoritative staging-compose validation (`docker compose config`). *Absent in the authoring env — only a python YAML-sanity fallback ran there.* | `docker compose version` |
| **Repo `.venv` with alembic + sqlalchemy + pyyaml + pytest** | `verify_deploy_readiness.py` (check B) and `verify_alert_rules.py` need them; the managed interpreter may lack alembic. The repo `.venv` has them. | `.venv/Scripts/python -m alembic --version` |
| **Headless browser (Playwright)** | The 4 browser click-through gates (P1/P5/P9/P10) | `eap/01-browser-click-through/` owns this |
| **prometheus + alertmanager native binaries** (`.exe` on Windows) **OR** a running stack | `verify_observability_loop.py --mode local` needs `prometheus.exe`/`alertmanager.exe`; `--mode external` needs a running stack | presence of binaries / reachable `:9090` `:9093` |

All commands below are written for **Linux/macOS with Docker + the repo
checked out**, using forward slashes. On Windows Git-Bash the same commands
work; only the Python interpreter path differs (`.venv/Scripts/python` vs
`.venv/bin/python`).

---

## 4. STATUS MATRIX (authoring state — NOT an execution result)

Legend (the three classification buckets required by the EAP contract):

- **READY** — the runbook/script is authored and ready to run.
- **BLOCKED-on-host** — the environment required to *execute* it is missing in
  the authoring host (e.g. no Docker daemon, no binaries).
- **NOT VERIFIED** — authored, executable in principle, but **not yet executed**
  in this package (no evidence file produced yet).

| EAP check | Artifact (authored?) | Execution status | Notes |
|----------|----------------------|------------------|-------|
| P1 browser click-through | owned by `eap/01-*` (another worker) | NOT VERIFIED | delegated; do not author here |
| P5 browser click-through | owned by `eap/01-*` | NOT VERIFIED | delegated |
| P9 browser click-through | owned by `eap/01-*` | NOT VERIFIED | delegated |
| P10 browser click-through | owned by `eap/01-*` | NOT VERIFIED | delegated |
| G9 — image build + boot + health | `scripts/verify_image_build.py` + `02-deployment-g9/runbook-build-boot-health.md` | BLOCKED-on-host | no Docker daemon here |
| G9 — alembic upgrade/rollback + staging compose | `scripts/verify_deploy_readiness.py` + `02-deployment-g9/runbook-upgrade-rollback.md` | BLOCKED-on-host | needs daemon + `.venv` alembic; `docker compose` plugin absent here |
| G9 — observability closed-loop firing | `scripts/verify_observability_loop.py` + `03-observability-alerting/runbook-alert-firing.md` | BLOCKED-on-host | needs prometheus/alertmanager binaries |
| Alert-rule dead-rule proof (static AST scan) | `scripts/verify_alert_rules.py` + CI guard | NOT VERIFIED | **can** run without a daemon (needs pyyaml); not executed yet in this package |
| Full regression (pytest) | `04-full-regression/run-regression.py` | NOT VERIFIED | collector can run; full suite not executed here |
| Final readiness battery | `05-final-readiness-battery/consolidate_readiness.py` | NOT VERIFIED | runs offline; needs the above JSONs to flip gates |

> **All four G9 execution rows are `BLOCKED-on-host` because the authoring host
> has no reachable Docker daemon (and the `docker compose` plugin is absent).**
> The alert-rule static check and the regression/battery scripts are `NOT
> VERIFIED` only because they have not been *executed* in this package — the
> environment to run them exists (the `.venv` has pytest/pyyaml).

### ⚠️ The honesty rule (read this before quoting any status)

> **NOT VERIFIED ≠ FAIL. Environment missing ≠ RELEASE READY.**

A gate that is `NOT VERIFIED` or `BLOCKED-on-host` is a **gap that was recorded
honestly**, never a proven failure and never a silent pass. The `05-final-
readiness-battery` consolidate script is hard-wired so that **absent execution
can never be flipped to PASS** — only a real evidence file showing the check
passed on a real host flips a gate. See `05-final-readiness-battery/README.md`.

---

## 5. How to run end-to-end (on a capable host)

Work top-to-bottom. Each step emits a JSON the final battery consumes.

```bash
# 0) Activate the repo venv (has alembic/sqlalchemy/pyyaml/pytest)
source .venv/bin/activate            # Windows Git-Bash: .venv/Scripts/activate

# 1) G9 — real image build + boot + health  (writes G9B_REPORT json)
python scripts/verify_image_build.py --host-port 18000
#   -> on success exit 0 and writes the JSON to $G9B_REPORT (or a temp path).
#   -> capture it: export G9B_REPORT=eap/05-final-readiness-battery/g9b_image_build_report.json

# 2) G9 — alembic upgrade/rollback + staging compose  (writes G9_REPORT json)
G9_REPORT=eap/05-final-readiness-battery/g9_deploy_readiness_report.json \
  python scripts/verify_deploy_readiness.py

# 3) G9 — observability closed-loop firing  (writes --json report)
#    a) app UP: force a 404 burst so LoopProbeHttp404Burst fires
python scripts/verify_observability_loop.py --mode local \
  --bin-dir /path/to/prom_am_bin --app-url http://127.0.0.1:8010 \
  --force 404 --json eap/05-final-readiness-battery/obs_loop_up_report.json
#    b) app DOWN: prove ServiceDown / LiuhaoServiceDown fire (L1/L2/L4 SKIPPED)
python scripts/verify_observability_loop.py --mode local \
  --bin-dir /path/to/prom_am_bin --app-url http://127.0.0.1:8010 \
  --force down --json eap/05-final-readiness-battery/obs_loop_down_report.json

# 4) Alert-rule dead-rule proof (static; no daemon needed)
python scripts/verify_alert_rules.py

# 5) Full regression (fail-closed; emits --json)
python eap/04-full-regression/run-regression.py \
  --json eap/05-final-readiness-battery/regression-report.json

# 6) Browser click-through (P1/P5/P9/P10) — owned by eap/01-* worker.
#    Its Playwright JSON lands in eap/01-browser-click-through/; point the
#    battery at it (see 05 README).

# 7) Final battery — consolidates everything into the honest verdict
python eap/05-final-readiness-battery/consolidate_readiness.py \
  --g9-report   eap/05-final-readiness-battery/g9b_image_build_report.json \
  --deploy-report eap/05-final-readiness-battery/g9_deploy_readiness_report.json \
  --obs-up-report eap/05-final-readiness-battery/obs_loop_up_report.json \
  --obs-down-report eap/05-final-readiness-battery/obs_loop_down_report.json \
  --regression-report eap/05-final-readiness-battery/regression-report.json \
  --browser-report eap/01-browser-click-through/playwright-report.json \
  --product-readiness docs/product/PRODUCT-READINESS.md \
  --out-dir eap/05-final-readiness-battery
```

Each step's detailed commands, expected output, and exit-code meaning live in
the per-surface runbooks under `02-*` … `05-*`.

---

## 6. Where each evidence file lands

All collected evidence is written under `eap/05-final-readiness-battery/`:

| Evidence | Path (relative to repo root) |
|----------|------------------------------|
| G9 image build/boot/health | `eap/05-final-readiness-battery/g9b_image_build_report.json` (via `G9B_REPORT`) |
| G9 deploy readiness | `eap/05-final-readiness-battery/g9_deploy_readiness_report.json` (via `G9_REPORT`) |
| Observability loop (app up) | `eap/05-final-readiness-battery/obs_loop_up_report.json` (via `--json`) |
| Observability loop (app down) | `eap/05-final-readiness-battery/obs_loop_down_report.json` (via `--json`) |
| Full regression | `eap/05-final-readiness-battery/regression-report.json` (via `--json`) |
| Browser click-through | `eap/01-browser-click-through/playwright-report.json` (another worker) |
| **Consolidated verdict** | `eap/05-final-readiness-battery/EAP-EVIDENCE-REPORT.md` + `.json` |

---

## 7. Known honest gaps disclosed by this package

These are documented explicitly so nobody mistakes an authored runbook for a
passing gate:

1. **Production Alertmanager has no deliverable sink.** `configs/alertmanager/
   alertmanager.yml` only emails fake `.local` domains and carries an **invalid
   top-level `alerts:` block** that Alertmanager silently ignores. No real
   webhook sink. The closed-loop *proof* uses the `.local.yml` sink at
   `127.0.0.1:18010` — that is a verification harness, **not** production.
2. **App-engine webhook is default off.** The application does not push alerts;
   Alertmanager pull-scrapes Prometheus.
3. **Closed-loop docker stack never actually run** in the authoring env (no
   daemon). `--mode local`/`--mode external` were authored but not executed.
4. **G8 evidence doc is missing** (referenced elsewhere; not part of EAP scope).
5. **Production runbook doesn't document alerting** — this package's
   `03-*` closes that gap for the verification flow only.
6. **`docker compose` plugin absent** in the authoring env; only a python
   YAML-schema sanity check ran for staging compose. Re-run `docker compose
   -f infra/staging/docker-compose.yml config` on a plugin-equipped host to
   close it.
7. **The image was never `docker build`-ed** in the authoring env. The Dockerfile
   was read and the build command authored, but no image exists yet.

---

## 8. Current product verdict (carry-over from `docs/product/PRODUCT-READINESS.md`)

As of the latest cycle referenced in that doc (cycle 8):

- **PASS = 6** (P2, P3, P4, P6, P7, P8)
- **BLOCKED = 4** (P1, P5, P9, P10 — solely on browser click-through)
- **FAIL = 0**
- **PRODUCT ACCEPTANCE = NOT READY**
- **RELEASE READINESS = NOT RELEASE READY** (G9 on WSL2 host restriction)

The term "EAP" appears exactly once in that document (a passing mention of the
"EAP full-regression runner"). This package is the first to formalize it.
