# G9b — Real Image Build + Boot Runbook

`REAL DEPLOYMENT = NOT VERIFIED`

**Blocker:** no docker daemon in this environment.

This runbook records exactly what was attempted, exactly what happened, and what
remains to be done on a machine that does have a daemon. Nothing here is
simulated, mocked, or inferred.

---

## 0. Verdict up front

| Question | Answer |
| --- | --- |
| Was an image built? | **NO** |
| Was a container booted? | **NO** |
| Did `/v1/health` answer? | **NO — never reached** |
| Is the image buildable? | **UNKNOWN** (untested, not disproven) |
| Is this a production deployment? | **NO — and nothing here may be written up as one** |

> The honest label is **NOT VERIFIED**. That is a *gap*, not a *pass* and not a
> *failure*. A missing daemon tells us nothing about whether the Dockerfile is
> correct — it only tells us nobody has run it.

---

## 1. Environment evidence (real, captured)

Machine: `MINGW64_NT-10.0-19045` (Windows 10 build 19045), host `XNG-20260709YFZ`.
Python used: `D:/LiuHao-Data/UserRoot/.workbuddy/binaries/python/envs/default/Scripts/python.exe` → `Python 3.13.14`.

### 1.1 `docker --version`

```
Docker version 29.8.1, build 4a63305
```

The **CLI is installed**. This is exactly the trap the gate exists to catch: a
present CLI proves nothing about a daemon.

### 1.2 `docker info`

```
Client:
 Version:    29.8.1
 Context:    desktop-linux
 Debug Mode: false

Server:
failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine;
check if the path is correct and if the daemon is running:
open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified.
```

### 1.3 `docker version`

```
Client:
 Version:           29.8.1
 API version:       1.56
 Context:           desktop-linux
failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine; ...
```

### 1.4 `docker context ls`

```
NAME              DESCRIPTION                               DOCKER ENDPOINT                             ERROR
default           Current DOCKER_HOST based configuration   npipe:////./pipe/docker_engine
desktop-linux *   Docker Desktop                            npipe:////./pipe/dockerDesktopLinuxEngine
```

Both contexts were tried; both endpoints are absent. `DOCKER_HOST` is unset.

### 1.5 `docker compose version`

```
docker: unknown command: docker compose
```

The compose v2 plugin is **also absent** — consistent with the gap already
recorded in `MASTER_PROJECT_READINESS.md` (G9, honest gap #1).

### 1.6 Corroborating checks

- No Docker daemon process is running (`tasklist` matched no `docker`/`com.docker` entries).
- Docker Desktop *is* installed on disk (`.../AppData/Local/Programs/DockerDesktop/Docker Desktop.exe`, 15,298,480 bytes, dated 2026-09-23) but the engine is **not running**.
- No alternative runtime exists: `podman`, `buildah`, `nerdctl` are all absent from `PATH`.
- The Docker Desktop backend that would be needed here (`desktop-linux` → WSL2) is **blocked by sandbox security policy** (`wsl.exe` is on the program blacklist), so starting it was not attempted.

---

## 2. What was delivered instead

Because no build could happen, the deliverable is the gate that performs the
work where a daemon *does* exist:

| File | Purpose |
| --- | --- |
| `scripts/verify_image_build.py` | The gate. Build → boot → health → HTTP. Exits non-zero on failure. |
| `docs/autonomous/G9-image-build-runbook.md` | This document. |

### 2.1 The gate's five checks

| # | Check | What it proves |
| --- | --- | --- |
| 1 | docker CLI **+ reachable daemon** | refuses to equate CLI presence with daemon availability |
| 2 | frozen-evidence guard | the Dockerfile never COPYs `audit_store.db`; build-context hygiene |
| 3 | `docker build` | real image ID, real byte size, real wall-clock duration |
| 4 | boot + health + HTTP | container reaches `healthy`; `/v1/health`, `/v1/ready`, `/v1/metrics/prometheus` all HTTP 200 with non-empty bodies |
| 5 | frozen evidence untouched | `sha256(audit_store.db)` identical before and after the whole run |

### 2.2 Exit codes

| Code | Meaning |
| --- | --- |
| `0` | all checks PASS |
| `1` | at least one **FAIL** — a real error occurred |
| `2` | **NOT_VERIFIED** — the environment cannot execute the gate |

`2` is non-zero **on purpose**. A CI job that goes green because the daemon
silently vanished is far worse than one that goes red.

### 2.3 Real run in this environment (verbatim)

```
D:/LiuHao-Data/UserRoot/.workbuddy/binaries/python/envs/default/Scripts/python.exe \
    scripts/verify_image_build.py
```

```

==========================================================================
G9b REAL IMAGE BUILD + BOOT + HEALTH GATE
repo: D:\LiuHao-AI-OS
ran : 2026-10-01T16:34:22.905877+00:00
SCOPE: locally built STAGING image. NOT a production deployment.
       Production target/registry = SOVEREIGN HUMAN DECISION.
==========================================================================
frozen evidence guard armed: audit_store.db sha256=4d2e602cf0c7c082...

[NOT_VERIFIED] 1. docker CLI + daemon
    evidence: docker CLI present but daemon NOT reachable: failed to connect to
      the docker API at npipe:////./pipe/dockerDesktopLinuxEngine; check if the
      path is correct and if the daemon is running: open
      //./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified.
      - GAP: no docker daemon in this environment.
      - Docker Desktop may be installed but the engine is not running, or its
      - WSL2/Hyper-V backend is unavailable.
      - Nothing in this gate can execute. Do NOT infer image buildability
      - from CLI presence alone -- that is precisely the fallacy this gate
      - exists to prevent.
[        PASS] 2. frozen-evidence guard
    evidence: frozen audit_store.db is not copied into the image; digest guard armed
      - Dockerfile COPY does not bake audit_store.db: confirmed
      - HYGIENE GAP: .dockerignore has no '*.db' rule; repo-root audit_store.db
        (~30 MB) is streamed in the build context on every build (it is NOT
        copied into the image -- only the upload is wasted).
      - frozen evidence present: audit_store.db (30146560 bytes)
[NOT_VERIFIED] 3. docker build
    evidence: skipped: no docker daemon
      - No image was built. This is a GAP, not a proven failure.
      - The Dockerfile was never executed by a build engine.
[NOT_VERIFIED] 4. container boot + health
    evidence: skipped: no docker daemon
      - No container was started. No HTTP endpoint was answered.
[        PASS] 5. frozen evidence untouched
    evidence: audit_store.db sha256 unchanged across the whole run: 4d2e602cf0c7c082...
      - The frozen HC-01 evidence file was not modified by this gate.
==========================================================================
JSON report written to: D:\cache\temp\g9b_image_build_report.json
VERDICT: NOT_VERIFIED
==========================================================================
```

Process exit code: **2**.

Frozen-evidence digest, full value, confirmed identical before and after:

```
4d2e602cf0c7c0825339e9e97fee97881730b709471638e4fcc550557c1b94b0  audit_store.db
```

The `HC-01` freeze boundary held.

### 2.4 The gate is proven to fail closed

A gate that can only ever print PASS is worthless. Its failure paths were
exercised directly (these run before any docker call, so they work without a daemon):

```
--- A) forbidden host port must FAIL ---
status = FAIL
evidence = refusing to publish host port 8000: reserved
           (8000, 8010, 9090, 9093 are in use by concurrent services)

--- B) 9090 also refused ---
status = FAIL | refusing to publish host port 9090: reserved ...

--- C) dead endpoint must FAIL, not pass ---
http status = 0 | body = '<transport-error: TimeoutError: timed out>'
would be judged OK? False
```

So the gate genuinely turns red on error; its current `NOT_VERIFIED` is a real
environment gap, not a rubber stamp.

---

## 3. How to run it where a daemon exists

```bash
# 1. confirm the daemon first — do not skip this
docker info

# 2. build (repo root; multi-stage: node console build -> python deps -> slim runtime)
docker build -t liuhao-ai-os:staging .

# 3. boot on host port 18000 (NEVER 8000/8010/9090/9093 — reserved)
docker run -d --name liuhao-staging-verify \
  -p 18000:8000 \
  -e PORT=8000 \
  -e APP_ENV=staging \
  -e LIUHAO_KERNEL_POLICY_ENFORCE=CRITICAL \
  -e AUDIT_DB_PATH=/app/data/audit_store.db \
  -e DATABASE_URL=sqlite:////app/data/liuhao_ai_os.db \
  -e JWT_SECRET_KEY=staging-dev-only-insecure \
  liuhao-ai-os:staging

# 4. wait for healthy, then probe
docker inspect -f '{{.State.Health.Status}}' liuhao-staging-verify
curl -s http://localhost:18000/v1/health
curl -s http://localhost:18000/v1/ready
curl -s http://localhost:18000/v1/metrics/prometheus | head

# 5. logs, then clean up (image is kept deliberately)
docker logs liuhao-staging-verify
docker rm -f liuhao-staging-verify
```

Or just run the gate, which does all of the above and exits non-zero on failure:

```bash
python scripts/verify_image_build.py                 # defaults: host 18000, container 8000
python scripts/verify_image_build.py --host-port 18001 --keep-container
```

Env/flag overrides: `G9B_HOST_PORT`, `G9B_CONTAINER_PORT`, `G9B_IMAGE`,
`G9B_CONTAINER`, `G9B_BUILD_TIMEOUT`, `G9B_HEALTH_TIMEOUT`, `G9B_KEEP_CONTAINER`,
`G9B_REPORT`.

Health polling budget is 180 s. That is not arbitrary: the Dockerfile
`HEALTHCHECK` uses `start-period=15s`, `interval=30s`, `retries=3`, so a slow
first boot can legitimately take ~105 s before it is declared `unhealthy`.

---

## 4. Findings discovered while preparing this (all real)

### F-1 — The container does **not** listen on 8000 by default

`src/gateway/__main__.py` sets `DEFAULT_PORT = 8080`, `resolve_bind()` reads
`$PORT`, and the Dockerfile declares `ENV PORT=8080` / `EXPOSE 8080`.

**Consequence:** a naive `docker run -p 18000:8000 ...` publishes a **dead
port** — the process is on 8080, the healthcheck probes 8080, and only the
publish mapping is wrong. This is a silent, confusing failure mode.

**Mitigation baked into the gate:** `PORT` is always passed explicitly
(`-e PORT=<container_port>`), so the process binds exactly the port published.

### F-2 — `.dockerignore` has no `*.db` rule

Confirmed absent. So repo-root `audit_store.db` (30,146,560 bytes) is streamed
to the daemon in the build context on **every** build.

Severity: **hygiene, not safety.** The Dockerfile's `COPY` set is
`src/`, `config/`, `scripts/`, `requirements.txt`, `apps/console/console/` — so
the frozen DB is **never baked into the image**. Only upload bandwidth and build
time are wasted.

Suggested fix (not applied — it touches `.dockerignore`, outside my mandate):

```
*.db
*.db-wal
*.db-shm
```

### F-3 — G9's "VERIFIED" label is scoped to upgrade/rollback, not to build/boot

`MASTER_PROJECT_READINESS.md` currently reads:

> `## G9 — Deployment & Upgrade` — **Status:** **VERIFIED**

Its own honest-gap list already discloses "the image was NOT actually
`docker build`-ed in this env", so nothing is hidden. But the headline label
`VERIFIED` on a gate named "Deployment" is easy to misread as "we deployed it".

**Recommendation:** restate as
`VERIFIED (upgrade/rollback path only) — image build + boot: NOT VERIFIED`,
and flip the build/boot sub-item only after a real `docker build` + healthy
container on a daemon-equipped host.

---

## 5. Honest limitations — read before quoting this document

1. **No image was built.** The Dockerfile has never been executed by a build
   engine. Whether `npm ci`, `npm run build`, or `pip install -r requirements.txt`
   succeed is **unknown**.
2. **No container was booted.** No HTTP endpoint was ever answered.
   `/v1/health`, `/v1/ready`, `/v1/metrics/prometheus` are **unproven in a container**.
3. **Docker Desktop could not be started.** Its likely backend (`wsl.exe`) is
   blocked by sandbox policy. This was not worked around — deliberately.
4. **No registry, no push.** The production deploy target/registry remains a
   **SOVEREIGN HUMAN DECISION**. The image was not, and must not be, pushed.
5. **Staging-only posture.** The env passed (`JWT_SECRET_KEY=staging-dev-only-insecure`)
   is a dev placeholder mirroring `infra/staging/docker-compose.yml`. It is not
   a credential and must never reach a non-staging environment.
6. **No CD, no rollback of a live service.** `ci-cd.yml` `deploy-*` jobs still
   `exit 1` by design. There is no helm chart, no k8s manifest, no target host.
7. **A future PASS is still not a production deploy.** It would mean only:
   *a locally built staging image booted and answered three endpoints*. No
   traffic, no secrets manager, no scale, no SLO. Writing it up as "production
   deployment verified" would be a false claim.

---

## 6. What "done" looks like

| Step | Status |
| --- | --- |
| Daemon reachable (`docker info` OK) | ❌ blocked here |
| `docker build -t liuhao-ai-os:staging .` succeeds | ❌ not executed |
| Image ID + size + duration recorded | ❌ not executed |
| Container reaches `healthy` | ❌ not executed |
| `/v1/health` → 200 with body | ❌ not executed |
| `/v1/ready` → 200 with body | ❌ not executed |
| `/v1/metrics/prometheus` → 200 non-empty | ❌ not executed |
| Boot logs scanned for policy-enforcement errors | ❌ not executed |
| Container removed, image retained | ❌ n/a |
| Gate exists, is re-runnable, fails closed | ✅ **DONE** |
| Runbook with honest limitations | ✅ **DONE** |
| Frozen `audit_store.db` provably untouched | ✅ **DONE** (digest identical) |

---

## 7. Sovereign decisions this document does NOT make

- Which registry/host receives the image.
- Whether the image is ever promoted beyond staging.
- Real secrets and where they come from (secret manager, never inline).
- Whether `LIUHAO_KERNEL_POLICY_ENFORCE=CRITICAL` is the right posture for the
  target environment.
- Database backend for non-staging (Postgres vs SQLite).

These are human decisions. Nothing in this runbook presumes an answer.
