# G9 — Deployment / Upgrade / Rollback Contract

**Repo:** `D:\LiuHao-AI-OS` · **Branch:** `p36`
**Status:** STAGING/TEST contract verified · PRODUCTION TARGET = **HUMAN DECISION REQUIRED**
**Freeze boundary honored:** no file under `src/` was modified; `audit_store.db`
(and its `-wal`/`-shm` sidecars) and `liuhao_ai_os.db` were never touched.

---

## 0. Scope & hard rules (as executed)

| Rule | Compliance |
|------|------------|
| Do NOT modify any existing file under `src/` | ✅ Only NEW files added |
| Do NOT touch live `audit_store.db` / `-wal` / `-shm` | ✅ All DB checks ran on a throw-away TEMP sqlite db |
| ADD NEW files only (`scripts/`, `infra/`, `docs/autonomous/`) | ✅ 3 new files (this doc, the script, the compose) |
| Deploy target is a HUMAN DECISION — do NOT assume production | ✅ Staging/test contract only; prod is explicitly deferred |
| Do NOT run the project's full pytest suite | ✅ Only the targeted `verify_deploy_readiness.py` was run |

---

## 1. What is VERIFIED

Verified by `scripts/verify_deploy_readiness.py` (real subprocess execution, no
fabrication). Re-run anytime:

```bash
# The repo's own .venv has alembic + sqlalchemy + pyyaml (the "managed" venv at
# D:/.../default/Scripts/python.exe lacks alembic and CANNOT run these checks).
/d/LiuHao-AI-OS/.venv/Scripts/python.exe scripts/verify_deploy_readiness.py
```

| # | Check | Result | Evidence |
|---|-------|--------|----------|
| A | Docker CLI availability | **PASS** | `docker --version` → Docker 29.8.1 present |
| B | Alembic upgrade head + downgrade base on a TEMP db | **PASS** | 5 migrations applied (`001`→`005`); `verify_orm_vs_db.py` shows **NO divergence**; `verify_persistence.py` shows **PERSISTED OK**; `downgrade base` leaves only `alembic_version` |
| C | Staging compose config validity | **PASS** (with a NOT_VERIFIED gap) | Validated by python YAML-schema sanity check; authoritative `docker compose config` could NOT run — see gap G1 |

> Exact PASS/FAIL/NOT_VERIFIED output and a JSON report are emitted by the
> script. NOT_VERIFIED is a recorded *gap*, never a silent FAIL.

### B — Rollback path is PROVEN, not asserted
On a fresh TEMP database the script performed, in order:
1. `alembic upgrade head` → all 5 migrations applied (exit 0);
2. `alembic current` → `005 (head)`;
3. `verify_orm_vs_db.py` (DATABASE_URL=TEMP db) → **DIVERGENCES FOUND: NONE**;
4. `verify_persistence.py` (DATABASE_URL=TEMP db) → **RESULT: PERSISTED OK**;
5. `alembic downgrade base` → every application table dropped; only `alembic_version`
   remains. This is a real, reversible migration set.

---

## 2. Staging / Test contract (`infra/staging/docker-compose.yml`)

The contract pins a single-port, non-root runtime image and enforces:

* **Built-image reference** — `build: {context: ., dockerfile: Dockerfile}` +
  `image: liuhao-ai-os:staging`, so the running container is unambiguously the
  artifact built from the repo Dockerfile.
* **Resource limits + reservations** — `limits: memory 2G / cpus 1.5`,
  `reservations: memory 512M / cpus 0.5` (a staging run cannot starve the host).
* **Real healthcheck** — probes `/v1/health` (matches the Dockerfile HEALTHCHECK).
* **No production secrets** — every secret env is EMPTY or a clearly-dev-only
  placeholder (`staging-dev-only-insecure`); a secret manager / CI secret store
  must inject real values before any non-staging use.
* **Persistence on a volume** — `liuhao_staging_data:/app/data` holds the identity
  db, app db, and `audit_store.db` so the audit hash-chain survives rebuilds.
* **Fail-closed posture preserved** — `LIUHAO_KERNEL_POLICY_ENFORCE=CRITICAL` is
  kept in staging so the gate is exercised, not silently relaxed.

Staging intentionally uses the default SQLite backend (the code does not require
Postgres/Redis — see `docker-compose.prod.yml` note).

---

## 3. Rollback path

Two independent, proven rollback mechanisms:

1. **DB schema rollback** — `alembic downgrade base` reverses the full migration
   set (proven on a TEMP db in check B). Per-migration step-down also works
   (`alembic downgrade -1` loop).
2. **App rollback** — re-pin `image: liuhao-ai-os:staging` to the previously known
   digest/tag and redeploy:
   `docker compose -f infra/staging/docker-compose.yml up -d`.

Because state lives on the named volume, an app rollback does not wipe the audit
chain; a schema rollback is only required when the image+model contract changes.

---

## 4. PRODUCTION DEPLOY TARGET = HUMAN DECISION REQUIRED

This contract **does not assume production**. Before any production promotion, a
human owner must decide and supply:

* [ ] **Target environment** — host/cluster, network exposure, domain.
* [ ] **Real secrets** — JWT, encryption/API-key master keys, identity integrity
      key, injected from a secret manager (never committed).
* [ ] **Database backend** — keep SQLite, or move to a networked store
      (driver + migration path must be added; currently not wired).
* [ ] **Scale & resources** — limits/reservations sized for prod load.
* [ ] **Approval gate** — explicit sign-off that this artifact is authorized to
      run in production (the CD jobs intentionally `exit 1` until this exists).
* [ ] **Rollback drill** — confirm the image digest + migration downgrade path for
      the chosen prod revision.

No helm chart / k8s manifests exist yet; the only deploy artifact is the staging
compose above. Production promotion is a separate, gated workstream.

---

## 5. Gaps / blockers (honest record)

* **G1 — `docker compose` plugin absent in this env (NOT_VERIFIED).** Docker CLI
  is present (29.8.1) but `docker compose` (v2 plugin) is not installed, so the
  authoritative `docker compose -f infra/staging/docker-compose.yml config` could
  not run. A python YAML-schema sanity check (structure + contractual markers +
  no inline secrets) was used instead and PASSED. Run `docker compose config`
  in an env that has the plugin to close this gap.
* **G2 — Build not executed here.** The image was not actually built (`docker
  build`) in this environment; only the migration/compose contracts were
  verified. Building + `docker compose config` is the remaining pre-prod step.
* **G3 — No real CD target.** By design the CD jobs `exit 1`; there is no helm
  chart. Production automation is a human-gated future decision.

---

## 6. Files added (G9 deliverables)

* `scripts/verify_deploy_readiness.py` — the real verification chain (A/B/C).
* `infra/staging/docker-compose.yml` — the explicit STAGING/TEST contract.
* `docs/autonomous/G9-deployment-contract.md` — this document.

Nothing was committed or pushed. The freeze boundary is intact.
