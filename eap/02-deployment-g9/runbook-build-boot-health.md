# 02 · Runbook — Real Image Build + Boot + Health (G9b)

**Script of record:** `scripts/verify_image_build.py`
**What it proves:** the repo's Dockerfile actually builds, the resulting image
boots a container that becomes healthy, and the three contract endpoints answer
HTTP 200 with a non-empty body — **on a real host**. It also guards the frozen
HC-01 evidence (`audit_store.db`) with a before/after SHA-256.

> Scope reminder (from the script header): a PASS means *a staging image was
> built locally and a container booted from it answered the endpoints on the
> published host port*. It does **NOT** mean "production deployment verified".

---

## A. Automated gate (recommended)

```bash
# From repo root, with the repo .venv active.
python scripts/verify_image_build.py --host-port 18000
```

Confirmed flags / env (grep of `argparse` + `os.environ` in the script):

| Flag | Env override | Default | Meaning |
|------|--------------|---------|---------|
| `--host-port` | `G9B_HOST_PORT` | `18000` | host port to publish |
| `--container-port` | `G9B_CONTAINER_PORT` | `8000` | container-side port (script passes `-e PORT=<this>` explicitly) |
| `--image` | `G9B_IMAGE` | `liuhao-ai-os:staging` | image tag (local only; never pushed) |
| `--container` | `G9B_CONTAINER` | `liuhao-staging-verify` | container name |
| `--build-timeout` | `G9B_BUILD_TIMEOUT` | `1800` | build timeout (s) |
| `--health-timeout` | `G9B_HEALTH_TIMEOUT` | `180` | health wait (s) |
| `--keep-container` | `G9B_KEEP_CONTAINER=1` | off | keep container after run |
| — | `G9B_REPORT` | temp file | where the JSON report is also written |

**Reserved host ports (the script refuses them):** `8000`, `8010`, `9090`,
`9093` — concurrently used by other services in the authoring env. Always use
`18000` (or another free port) for the host side.

**Exit codes:** `0` = all PASS · `1` = ≥1 real FAIL · `2` = NOT_VERIFIED (no
docker daemon / unbuildable env). See `failure-interpretation.md`.

**Capture the report for the final battery:**
```bash
G9B_REPORT=eap/05-final-readiness-battery/g9b_image_build_report.json \
  python scripts/verify_image_build.py --host-port 18000
```

---

## B. Manual equivalent (so you can see every step)

The Dockerfile sets `ENV PORT=8080` and `EXPOSE 8080`, runs as non-root `app`,
and its `CMD` is `python -m src.gateway`. The healthcheck hits `/v1/health`.

> **Port discipline — read this before you publish.**
> A naive `docker run -p 18000:8000` publishes a **DEAD** port, because the
> image listens on **8080** (the Dockerfile's `ENV PORT=8080`), not 8000.
> **Always pass `-e PORT=<container_port>` explicitly** so the process binds
> exactly the port you publish. The manual command below uses the Dockerfile
> default (`8080`); the automated script defaults the container port to `8000`
> but *also* passes `-e PORT=8000`, so neither path points at a dead port.

### 1) Build
```bash
docker build -t liuhao-ai-os:staging -f Dockerfile .
# Confirm:
docker image inspect liuhao-ai-os:staging --format '{{.Id}}|{{.Size}}'
```

### 2) Run on host port 18000, binding the container's 8080 explicitly
```bash
docker run -d --name liuhao-staging-verify \
  -p 18000:8080 \
  -e PORT=8080 \
  -e APP_ENV=staging -e LIUHAO_ENV=staging -e ENVIRONMENT=staging \
  -e LIUHAO_KERNEL_POLICY_ENFORCE=CRITICAL \
  -e AUDIT_DB_PATH=/app/data/audit_store.db \
  -e DATABASE_URL=sqlite:////app/data/liuhao_ai_os.db \
  -e JWT_SECRET_KEY=staging-dev-only-insecure \
  -e LOG_LEVEL=INFO \
  liuhao-ai-os:staging
```
*(The automated script passes the same env block plus `LIUHAO_ENV`/`ENVIRONMENT`
and a dev-only `JWT_SECRET_KEY`; the env above is the minimal faithful subset.)*

### 3) Wait for healthy
```bash
docker inspect -f '{{.State.Health.Status}}' liuhao-staging-verify
# repeat until it prints "healthy" (give it up to ~60s; start-period 15s)
```

### 4) Probe the three contract endpoints
```bash
for ep in /v1/health /v1/ready /v1/metrics/prometheus; do
  echo "== $ep =="
  curl -s -o /dev/null -w "HTTP %{http_code}\n" http://127.0.0.1:18000$ep
done
```
All three must return **HTTP 200 with a non-empty body**.

### 5) Cleanup
```bash
docker stop -t 10 liuhao-staging-verify && docker rm -f liuhao-staging-verify
```

---

## C. Expected output (automated gate)

A passing run prints a table like:

```
[        PASS] 1. docker CLI + daemon
[        PASS] 2. frozen-evidence guard
[        PASS] 3. docker build
[        PASS] 4. container boot + health
[        PASS] 5. frozen evidence untouched
VERDICT: PASS
JSON report written to: /tmp/g9b_image_build_report.json
```

## D. Evidence to capture

| Item | Where |
|------|-------|
| Image ID | `docker image inspect … --format '{{.Id}}'` / `facts.image_id` |
| Image size | `facts.image_size_mb` |
| Build duration | `facts.duration_s` |
| Health status | `facts.health_status` (`healthy`) |
| Endpoint results | `facts.endpoints` (all `http_status: 200`) |
| Frozen-db digest unchanged | check 5 status `PASS` |
| Exit code | `0` |

> The frozen-evidence guard (check 2 + check 5) **fails the gate** if
> `audit_store.db` is ever `COPY`-ed into the image or its SHA-256 moves during
> the run. That is by design — tampering with HC-01 evidence is treated as an
> incident.
