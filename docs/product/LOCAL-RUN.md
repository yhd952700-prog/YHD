# Local Run / Deployment — Honest, Non-Docker Path

> **Scope of this document.** This describes how to boot the LiuHao AI OS
> backend and build the console **on a host that does NOT have Docker** (no
> Docker daemon, no compose plugin, no browser for E2E). It proves the system
> *truly boots and serves real data* at the process level.
>
> ⚠️ **The full G9 closure is intentionally NOT claimed here.** The G9
> deployment closure (`docker compose ... up` + nginx + a real browser E2E that
> exercises the single-port cockpit) **cannot run on this host** because Docker
> and a browser are unavailable. That closure must be executed on a host that
> has Docker / compose / a browser. See [§6 Honest limitations](#6-honest-limitations).

---

## 1. What the backend actually needs to boot

A clean `uvicorn src.gateway.main:app` boot requires **no external services**.

| Concern | Source of truth | Mandatory at boot? |
|---|---|---|
| Redis | `docker-compose.prod.yml` notes: redis references in `src/distribution/*` are comments; moved to optional `infra` profile | **No** |
| Postgres / Qdrant | `docker-compose.prod.yml`: SQLAlchemy defaults to sqlite (`src/integrations/orm_models.py::_DEFAULT_DB_URL`); Qdrant only used by optional RAG | **No** |
| Message broker (Kafka/RabbitMQ) | `src/distribution/*` broker backends are optional, lazily imported | **No** |
| API key / JWT / RBAC / Encryption managers | `src/security/*`, wired in `src/gateway/main.py` lifespan | **Yes — but internal** (no network service) |
| Executor fence | `src/kernels/execution/fence.py`, default backend = `sqlite` (`LIUHAO_DISTRIBUTED_LEASE_BACKEND`) | **Yes — but internal** (sqlite) |
| Identity store | `src/kernels/identity`, file or sqlite | **Yes — but internal** (file/sqlite) |
| Audit store | `src/kernels/audit`, sqlite | **Yes — but internal** (sqlite) |

`/v1/ready` (`src/gateway/health.py`) checks exactly these internal managers:
`api_key_manager`, `jwt_handler`, `rbac_manager`, `encryption_manager`,
`rate_limiter`, `audit_store`, `human_identity_registry`, `executor_fence`,
`ai_provider`. **None of them require a running Redis/Postgres/Qdrant/broker.**
The `/v1/ready/subsystems` endpoint (`src/gateway/observability.py` →
`check_subsystems()`) additionally reports the 14+ kernels as real, importable,
instantiable objects.

### Secrets (honest note)
In `APP_ENV=staging` (the shipped `.env` default) the code **tolerates missing
secrets**: the JWT handler falls back to a per-process ephemeral key (with a
loud warning), and the encryption / API-key managers generate their own keys.
For a **durable, reproducible** local run, set real secrets (not the `replace-me`
/ `***` placeholders the repo ships with — those are explicitly *rejected* and
treated as "unset"):

```
LIUHAO_JWT_SECRET          a real HS256 signing secret (>=32 bytes)
SECRET_KEY                any real value
ENCRYPTION_MASTER_KEY     optional; auto-generated if omitted
API_KEY_MASTER_KEY        optional; auto-generated if omitted
```

For human login (gated endpoints), additionally point at a real identity store:

```
LIUHAO_HUMAN_IDENTITIES_FILE=config/human_identities.json
LIUHAO_AUTH_SECRETS_FILE=config/auth_secrets.json
LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY=<hmac key>   # required to ADMIT stored rows
```

Without `LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY` the registry is **fail-closed**:
stored rows are refused and zero humans are admitted (by design — see
`src/kernels/identity`). This is correct security behaviour, not a bug.

---

## 2. Boot the backend (exact command)

From the repo root, using the project virtualenv (`.venv/Scripts/python.exe` on
Windows, `.venv/bin/python` on Linux). Pass real secrets via the environment
(highest precedence in `src/config_manager.py`):

```bash
cd /LiuHao-AI-OS
export NO_PROXY=localhost,127.0.0.1          # localhost probes must bypass any HTTP proxy
export LIUHAO_JWT_SECRET="$(python -c 'import secrets;print(secrets.token_urlsafe(48))')"
export SECRET_KEY="$LIUHAO_JWT_SECRET"
# ENCRYPTION_MASTER_KEY / API_KEY_MASTER_KEY optional; auto-generated if omitted

.venv/Scripts/python.exe -m uvicorn src.gateway.main:app \
    --host 127.0.0.1 --port 8080 --log-level info
```

You should see:

```
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8080 (Press CTRL+C to quit)
```

**No Redis/Postgres/Qdrant connection errors appear** — confirming none are
mandatory.

### Alternative: the bundled native launcher (no Docker)
`scripts/start_liuhao.py` already provides a non-Docker path (it `exec`s
`uvicorn src.gateway.main:app` and waits for `/v1/health` without going through
any proxy). It also serves the console (vite dev, or single-port from a built
`dist`).

```bash
# full stack (gateway + vite console)
.venv/Scripts/python.exe scripts/start_liuhao.py
# backend only
.venv/Scripts/python.exe scripts/start_liuhao.py --backend-only
# single port (gateway serves the built cockpit dist/)
.venv/Scripts/python.exe scripts/start_liuhao.py --single-port
```

A convenience `make run` / `make run-backend` target is provided in the
`Makefile` (see §5).

---

## 3. Prove it serves real data (verification)

All probes below bypass any proxy (`--noproxy '*'`).

```bash
curl -s --noproxy '*' http://127.0.0.1:8080/v1/health
curl -s --noproxy '*' http://127.0.0.1:8080/v1/ready
curl -s --noproxy '*' http://127.0.0.1:8080/v1/metrics
curl -s --noproxy '*' http://127.0.0.1:8080/v1/ready/subsystems
```

Expected: all return HTTP **200** with **non-empty, real** payloads
(`/v1/ready` reports the live auth-manager/audit/executor-fence state;
`/v1/ready/subsystems` reports each of the 14+ kernels as `healthy`).

Gated endpoints require a bearer token. Register a human, then log in:

```bash
export LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY="<hmac key>"
echo "a-password" | .venv/Scripts/python.exe scripts/register_human_identity.py \
    --file /tmp/hid.json --secrets-file /tmp/auth.json \
    --principal demo --password-stdin

# restart uvicorn with LIUHAO_HUMAN_IDENTITIES_FILE=/tmp/hid.json \
#   + LIUHAO_AUTH_SECRETS_FILE=/tmp/auth.json + the same integrity key

TOKEN=$(curl -s --noproxy '*' -X POST http://127.0.0.1:8080/v1/auth/login \
    -H 'Content-Type: application/json' \
    -d '{"principal":"demo","secret":"a-password"}' | python -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')

curl -s --noproxy '*' -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8080/v1/kernels
curl -s --noproxy '*' -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8080/v1/dashboard/roster
```

`/v1/kernels` returns real per-kernel lifecycle state; `/v1/dashboard/roster`
returns `declared_totals: {capabilities:14, layers:14}` and the full kernel
list. **Unauthenticated** access to `/v1/kernels` returns **401** — the
human-sovereignty gate is real, not cosmetic.

---

## 4. Build & serve the console (frontend)

The console (`apps/console/console`) builds **standalone** — the build does
**not** need a running backend and does **not** require any API-URL env var.
The Vite dev proxy (`apps/console/console/vite.config.ts`) only forwards `/v1`
to `http://127.0.0.1:8080` in `vite dev`; `vite build` just compiles.

Use the managed Node (already on PATH on the build host):

```bash
cd apps/console/console
NODE=/c/Users/Administrator/.workbuddy/binaries/node/versions/22.22.2-3/node.exe
"$NODE" node_modules/typescript/bin/tsc -b     # type-check (EXIT 0)
"$NODE" node_modules/vite/bin/vite.js build    # => dist/ (index.html + assets)
```

Verified output (`vite v8`):
```
dist/index.html                  1.62 kB │ gzip:  0.87 kB
dist/assets/index-*.css         42.88 kB │ gzip:  8.79 kB
dist/assets/index-*.js         322.88 kB │ gzip: 95.48 kB
✓ built in 10.02s
```

Serve the built cockpit from the **same port** as the API (single-port mode,
recommended for non-Docker local runs and for container deploys):

```bash
.venv/Scripts/python.exe scripts/start_liuhao.py --single-port
# requires apps/console/console/dist/index.html to exist (build above)
```

Or serve the console separately with `vite preview`/`vite dev` (dev proxy
forwards `/v1` to the gateway).

---

## 5. `make` targets

Added to the `Makefile` (additive; `make` is optional, the raw commands in
§2/§4 work without it):

```makefile
.PHONY: run run-backend

run:            # gateway + console (full local stack, no Docker)
	@ROOT=$$(git rev-parse --show-toplevel); \
	  "$$ROOT/.venv/Scripts/python.exe" "$$ROOT/scripts/start_liuhao.py"

run-backend:    # gateway only
	@ROOT=$$(git rev-parse --show-toplevel); \
	  "$$ROOT/.venv/Scripts/python.exe" "$$ROOT/scripts/start_liuhao.py" --backend-only
```

---

## 6. Honest limitations

- **Docker / compose / nginx / browser E2E is host-blocked here.** This host has
  no Docker daemon, no compose plugin, and no browser, so the G9 full closure
  (`docker compose -f docker-compose.prod.yml up -d` + nginx + a real browser
  driving the cockpit) **cannot be executed or verified here**. It must be run on
  a Docker-enabled host. This document proves the *process-level* boot + real
  data, which is the part that does not require Docker.
- **`/v1/ready` reports `audit_store` with `coverage_ratio: 0.0` / "Audit chain
  verification incomplete"** when the audit chain has not been re-derived. This
  is surfaced honestly in the `errors` array but does **not** flip readiness to
  503 (per `src/gateway/health.py` design — coverage incompleteness must be
  visible, not self-terminating). It is reported, not hidden.
- **AI provider** is configured for `ollama` (`AI_PROVIDER_TYPE=ollama`) in
  `real` mode. If no Ollama server is running on `OLLAMA_BASE_URL`, chat
  completions will fail at request time — that is expected; the gateway still
  boots and serves all non-LLM endpoints. Swap in `openai`/`anthropic`/etc. with
  the matching `AI_PROVIDER_KEY` to exercise chat.
- **No faking.** Every endpoint above was hit against the real running process.
  No service was stubbed; the executor fence, audit store, and human-sovereignty
  gate are all real and observed to behave (gate → 401 unauthenticated).
