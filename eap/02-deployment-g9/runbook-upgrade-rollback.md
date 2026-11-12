# 02 · Runbook — Upgrade / Rollback (G9 migration + app rollback)

**Script of record:** `scripts/verify_deploy_readiness.py` (checks A/B/C)
**What it proves:**

- **B** — Alembic `upgrade head` then `downgrade base` on a **throw-away TEMP
  sqlite database** (freeze boundary honored: never the live `audit_store.db`
  or `liuhao_ai_os.db`), and that after `downgrade base` only `alembic_version`
  remains. It also runs `verify_orm_vs_db.py` (ORM↔schema sync) and
  `verify_persistence.py` (cross-session persistence) against the migrated db.
- **A** — Docker CLI availability.
- **C** — Staging compose config validity (authoritative `docker compose config`
  if the plugin exists; otherwise a python YAML-schema sanity check).

> **HARD BOUNDARY:** the migration checks run **only** against a temp db under
> the OS temp dir. The script refuses any path containing `audit_store.db` or
> `liuhao_ai_os.db`. Do not point alembic at a live database.

---

## A. Automated gate (recommended)

```bash
# Use the repo .venv — it has alembic + sqlalchemy. The managed interpreter may not.
G9_REPORT=eap/05-final-readiness-battery/g9_deploy_readiness_report.json \
  python scripts/verify_deploy_readiness.py
```

Confirmed invocation facts (read from the script):
- **No argparse flags.** It takes only env vars:
  - `G9_PYTHON` — interpreter used to spawn `alembic` / `verify_*.py`
    (default: `sys.executable`, i.e. the `.venv` you launched with).
  - `G9_REPORT` — path to also write the JSON report.
- Alembic is spawned as `python -m alembic -c alembic.ini …` with
  `DATABASE_URL` + `PYTHONPATH=<repo_root>` in the env.
- **Exit:** `1` if any check is `FAIL`; otherwise `0` (a `NOT_VERIFIED` gap,
  e.g. the compose plugin being absent, is recorded but does **not** fail the
  run).

---

## B. Manual equivalent — migration upgrade → verify → rollback

```bash
# 1) Point alembic at a TEMP database (NEVER the live audit_store.db / liuhao_ai_os.db)
TMPDB="$(mktemp -d)/g9_temp_migration.db"
export DATABASE_URL="sqlite:///${TMPDB}"
export PYTHONPATH="$PWD"

# 2) Upgrade to head  (config file is alembic.ini at repo root)
python -m alembic -c alembic.ini upgrade head
python -m alembic -c alembic.ini current

# 3) Positive evidence: ORM schema in sync + persistence works
python scripts/verify_orm_vs_db.py
python scripts/verify_persistence.py

# 4) ROLLBACK PATH — downgrade base, then prove only alembic_version remains
python -m alembic -c alembic.ini downgrade base

# 5) Inspect: only alembic_version should be left
python - <<'PY'
import sqlite3, sys
con = sqlite3.connect("${TMPDB}")
tables = sorted(r[0] for r in con.execute("select name from sqlite_master where type='table'"))
print("tables after downgrade base:", tables)
sys.exit(0 if tables == ["alembic_version"] else 1)
PY

# 6) Cleanup the temp db
rm -rf "$(dirname "$TMPDB")"
```

`verify_orm_vs_db.py` uses `DATABASE_URL` (default `sqlite:///./liuhao_ai_os.db`
if unset — **always set it to your temp db** so you never touch the live one).
It exits `1` on any divergence, `0` if none.

---

## C. App rollback (re-pin image digest + recreate staging stack)

The staging compose (`infra/staging/docker-compose.yml`) is the rollback
contract. To roll the *application* back to a known-good image:

```bash
# Re-pin the staging image to a previous digest/tag, then recreate:
#   (edit infra/staging/docker-compose.yml `image: liuhao-ai-os:staging`
#    to the prior digest, e.g. liuhao-ai-os@sha256:<prev>, OR re-tag)
docker tag liuhao-ai-os:<prev-digest> liuhao-ai-os:staging

# Recreate the staging stack from the re-pinned image:
docker compose -f infra/staging/docker-compose.yml up -d
```

> This is the **staging/test** rollback path only. Production promotion remains
> a **HUMAN DECISION** (see `00-overview.md`). There is no helm/k8s and the CD
> jobs `exit 1` by design.

---

## D. Expected output (automated gate)

```
[        PASS] A. docker CLI availability
[        PASS] B. alembic upgrade/rollback
[        PASS] C. staging compose config validity
RESULT: OK (no hard failures; NOT_VERIFIED gaps recorded)
```

In the authoring env, **C** degraded to a `NOT_VERIFIED` gap (docker compose
plugin absent → python YAML-schema sanity check ran instead). The script still
exits `0`; the report records the gap. On a plugin-equipped host,
`docker compose -f infra/staging/docker-compose.yml config` succeeds and C is
`PASS`.

## E. Evidence to capture

| Item | Where |
|------|-------|
| `upgrade head` rc | check B detail (`alembic upgrade head: OK`) |
| `downgrade base` leaves only `alembic_version` | check B detail (`after downgrade base, tables left: [...]`) |
| ORM↔DB sync | check B detail (`verify_orm_vs_db: PASS`) |
| Persistence | check B detail (`verify_persistence: PASS`) |
| Staging compose validity | check C status |
| Exit code | `0` (gaps allowed) / `1` (hard FAIL) |
