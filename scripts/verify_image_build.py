#!/usr/bin/env python3
"""
G9b — Real image build + boot + health gate
===========================================

`scripts/verify_deploy_readiness.py` proves the *upgrade/rollback* path: it runs
alembic on a TEMP database and checks the compose YAML. It never builds an image
and never boots a container. This script closes that specific gap:

    build the image  ->  boot the container  ->  become healthy  ->  answer HTTP

HARD BOUNDARIES (do not violate):
  * We NEVER touch the repo-root `audit_store.db` (or its -wal/-shm sidecars).
    It is frozen HC-01 evidence. This script SHA-256s it before and after the
    run and FAILS if the digest moved.
  * We NEVER modify anything under src/. Existing files are only READ.
  * We NEVER push the image to any registry. Build is LOCAL ONLY.
  * The production deploy target/registry is a SOVEREIGN HUMAN DECISION.

WHAT A PASS ACTUALLY MEANS (read this before quoting it):
  A PASS means: a staging image was built locally from this repo's Dockerfile
  and a container booted from it answered /v1/health, /v1/ready and
  /v1/metrics/prometheus on the published host port.
  A PASS does **NOT** mean "production deployment verified". There is no
  registry, no target host, no secret manager, no CD pipeline, no rollback of a
  live service, and no traffic. Writing it up as a production deploy is a
  false claim.

PORT DISCIPLINE:
  The container's *internal* port is overrideable via $PORT (see
  src/gateway/__main__.py, default 8080). We always pass PORT explicitly so the
  process binds exactly the port we publish -- otherwise a publish of
  `-p 18000:8000` would point at a dead port, because the Dockerfile's default
  is 8080, not 8000.
  The script REFUSES to publish host ports 8000 / 8010 / 9090 / 9093 (reserved
  for concurrently running services in this environment). Default host port is
  18000.

EXIT CODES:
  0  every check PASS
  1  at least one check FAIL (a real error occurred)
  2  NOT_VERIFIED -- the environment cannot execute this gate at all
     (no docker CLI, or CLI present but daemon unreachable). This is a GAP,
     not a proven failure, but it is NON-ZERO on purpose: a CI job that
     silently goes green because the daemon vanished is worse than one that
     turns red.

HOW TO RUN:
  python scripts/verify_image_build.py
  python scripts/verify_image_build.py --host-port 18000 --container-port 8000

  Optional env / flags:
    G9B_HOST_PORT        host port to publish      (default 18000)
    G9B_CONTAINER_PORT   container-side port       (default 8000)
    G9B_IMAGE            image tag                 (default liuhao-ai-os:staging)
    G9B_CONTAINER        container name            (default liuhao-staging-verify)
    G9B_BUILD_TIMEOUT    build timeout, seconds    (default 1800)
    G9B_HEALTH_TIMEOUT   health wait, seconds      (default 180)
    G9B_KEEP_CONTAINER   1 = do not remove the container afterwards
    G9B_REPORT           path to also write the JSON report
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# --------------------------------------------------------------------------- #
# Locations / constants
# --------------------------------------------------------------------------- #
SCRIPT = Path(__file__).resolve()
REPO_ROOT = SCRIPT.parents[1]
DOCKERFILE = REPO_ROOT / "Dockerfile"
FROZEN_DB = REPO_ROOT / "audit_store.db"

# Host ports reserved by other services running concurrently in this env.
FORBIDDEN_HOST_PORTS = (8000, 8010, 9090, 9093)

# Endpoints the contract promises.
ENDPOINTS = ("/v1/health", "/v1/ready", "/v1/metrics/prometheus")

# Substrings that indicate the process did NOT boot cleanly.
LOG_ERROR_MARKERS = (
    "Traceback (most recent call last)",
    "policy enforcement",
    "POLICY_ENFORCE",
    "fail-closed",
    "not armed",
    "PermissionError",
    "sqlite3.OperationalError",
)


@dataclass
class Check:
    name: str
    status: str                      # PASS | FAIL | NOT_VERIFIED
    evidence: str
    detail: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def run(cmd, env=None, cwd=None, timeout=300) -> subprocess.CompletedProcess:
    """Run a command, never raising on non-zero exit."""
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    return subprocess.run(
        cmd, env=full_env, cwd=str(cwd) if cwd else None,
        capture_output=True, text=True, timeout=timeout,
    )


def tail(text: str, n: int = 40) -> list[str]:
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    return lines[-n:]


def sha256_file(path: Path) -> Optional[str]:
    if not path.exists():
        return None
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def http_get(url: str, timeout: float = 5.0) -> tuple[int, str]:
    """Return (status_code, body). status_code 0 == transport-level failure."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
            return int(resp.status), body
    except urllib.error.HTTPError as e:
        # HTTPError IS a response: keep the real status code and body.
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            pass
        return int(e.code), body
    except Exception as e:
        return 0, f"<transport-error: {type(e).__name__}: {e}>"


# --------------------------------------------------------------------------- #
# Check 1 — preflight: docker CLI + reachable daemon
# --------------------------------------------------------------------------- #
def check_docker_daemon() -> Check:
    docker = shutil.which("docker")
    if not docker:
        return Check(
            "1. docker CLI + daemon", "NOT_VERIFIED",
            "docker binary not found on PATH",
            ["GAP: no container runtime in this environment.",
             "Image build / container boot is therefore NOT VERIFIED."],
        )

    ver = run([docker, "version", "--format", "{{.Server.Version}}"], timeout=60)
    info = run([docker, "info", "--format", "{{.ServerVersion}}|{{.OperatingSystem}}"], timeout=60)

    if ver.returncode == 0 and ver.stdout.strip():
        return Check(
            "1. docker CLI + daemon", "PASS",
            f"docker CLI {run([docker, '--version'], timeout=30).stdout.strip()} "
            f"| daemon ServerVersion={ver.stdout.strip()}",
            [f"docker info: {info.stdout.strip() or info.stderr.strip()}"],
        )

    # CLI present, daemon unreachable: this is the exact state observed on the
    # authoring host (Docker Desktop installed, engine not running).
    err = (ver.stderr.strip() or info.stderr.strip()
           or "daemon unreachable").strip()
    return Check(
        "1. docker CLI + daemon", "NOT_VERIFIED",
        f"docker CLI present but daemon NOT reachable: {err}",
        ["GAP: no docker daemon in this environment.",
         "Docker Desktop may be installed but the engine is not running, or its",
         "WSL2/Hyper-V backend is unavailable.",
         "Nothing in this gate can execute. Do NOT infer image buildability",
         "from CLI presence alone -- that is precisely the fallacy this gate",
         "exists to prevent."],
    )


# --------------------------------------------------------------------------- #
# Check 2 — frozen-evidence guard + build-context hygiene
# --------------------------------------------------------------------------- #
def check_freeze_guard() -> Check:
    detail: list[str] = []
    problems: list[str] = []

    if not DOCKERFILE.exists():
        return Check("2. frozen-evidence guard", "FAIL",
                     f"Dockerfile missing at {DOCKERFILE}", [])

    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    # (a) the Dockerfile must never COPY the frozen HC-01 database in.
    db_baked = any(
        "audit_store.db" in ln and ln.strip().upper().startswith("COPY")
        for ln in dockerfile.splitlines()
    )
    if db_baked:
        problems.append("Dockerfile COPYs audit_store.db into the image")
    detail.append("Dockerfile COPY does not bake audit_store.db: "
                  f"{'NO' if db_baked else 'confirmed'}")

    # (b) build-context hygiene: .dockerignore should exclude *.db so the frozen
    #     30 MB evidence file is not streamed into the daemon on every build.
    ignore = REPO_ROOT / ".dockerignore"
    if ignore.exists():
        has_db_rule = any(
            ln.strip() and not ln.strip().startswith("#") and ln.strip().endswith("*.db")
            for ln in ignore.read_text(encoding="utf-8").splitlines()
        )
        if not has_db_rule:
            detail.append("HYGIENE GAP: .dockerignore has no '*.db' rule; "
                          "repo-root audit_store.db (~30 MB) is streamed in the "
                          "build context on every build (it is NOT copied into "
                          "the image -- only the upload is wasted).")
        else:
            detail.append(".dockerignore excludes *.db: confirmed")
    else:
        detail.append("HYGIENE GAP: no .dockerignore at repo root")

    if not FROZEN_DB.exists():
        detail.append(f"note: {FROZEN_DB.name} not present at repo root; "
                      "pre/post digest guard is moot")
    else:
        detail.append(f"frozen evidence present: {FROZEN_DB.name} "
                      f"({FROZEN_DB.stat().st_size} bytes)")

    return Check(
        "2. frozen-evidence guard", "FAIL" if problems else "PASS",
        "; ".join(problems) if problems else
        "frozen audit_store.db is not copied into the image; digest guard armed",
        detail,
    )


# --------------------------------------------------------------------------- #
# Check 3 — real image build
# --------------------------------------------------------------------------- #
def check_build(image: str, build_timeout: int) -> tuple[Check, dict]:
    docker = shutil.which("docker") or "docker"
    cmd = [docker, "build", "-t", image, "-f", str(DOCKERFILE), "."]
    t0 = time.time()
    try:
        cp = run(cmd, cwd=REPO_ROOT, timeout=build_timeout)
    except subprocess.TimeoutExpired:
        return Check(
            "3. docker build", "FAIL",
            f"build exceeded {build_timeout}s timeout",
            [f"command: {' '.join(cmd)}"],
        ), {}
    duration = round(time.time() - t0, 1)

    if cp.returncode != 0:
        return Check(
            "3. docker build", "FAIL",
            f"`docker build` exited {cp.returncode} after {duration}s",
            [f"command: docker build -t {image} -f Dockerfile .",
             "--- last build output ---", *tail(cp.stdout),
             "--- last build errors ---", *tail(cp.stderr)],
        ), {"duration_s": duration}

    # Real facts: image id, size, created.
    insp = run([docker, "image", "inspect", image,
                "--format", "{{.Id}}|{{.Size}}|{{.Created}}"], timeout=60)
    if insp.returncode != 0:
        return Check(
            "3. docker build", "FAIL",
            "build reported success but `docker image inspect` failed",
            [insp.stderr.strip() or insp.stdout.strip()],
        ), {"duration_s": duration}

    parts = (insp.stdout.strip() or "||").split("|")
    image_id, size_raw, created = (parts + ["", "", ""])[:3]
    try:
        size_mb = round(int(size_raw) / (1024 * 1024), 1)
    except ValueError:
        size_mb = None

    facts = {
        "duration_s": duration,
        "image_id": image_id,
        "image_size_bytes": int(size_raw) if size_raw.isdigit() else None,
        "image_size_mb": size_mb,
        "created": created,
    }
    return Check(
        "3. docker build", "PASS",
        f"built {image} in {duration}s | id={image_id[:19]} | size="
        f"{size_mb} MB ({size_raw} bytes)",
        [f"command: {' '.join(cmd)} (cwd={REPO_ROOT})",
         f"created: {created}",
         "NOTE: this is a LOCALLY built image. It has NOT been pushed to any",
         "      registry. The production target/registry is a HUMAN DECISION."],
    ), facts


# --------------------------------------------------------------------------- #
# Check 4 — boot + health + HTTP endpoints
# --------------------------------------------------------------------------- #
def check_boot(image: str, container: str, host_port: int,
               container_port: int, health_timeout: int,
               keep: bool) -> tuple[Check, dict]:
    docker = shutil.which("docker") or "docker"
    facts: dict = {}

    # -- host port discipline ------------------------------------------------
    if host_port in FORBIDDEN_HOST_PORTS:
        return Check(
            "4. container boot + health", "FAIL",
            f"refusing to publish host port {host_port}: reserved "
            f"({', '.join(map(str, FORBIDDEN_HOST_PORTS))} are in use by "
            "concurrent services)", [],
        ), facts

    s = socket.socket()
    try:
        s.bind(("127.0.0.1", host_port))
    except OSError as e:
        return Check(
            "4. container boot + health", "FAIL",
            f"host port {host_port} is not bindable: {e}", [],
        )
    finally:
        s.close()

    # -- remove any stale container with the same name -----------------------
    run([docker, "rm", "-f", container], timeout=60)

    # -- run -----------------------------------------------------------------
    # PORT is passed EXPLICITLY: src/gateway/__main__.py honours $PORT and the
    # Dockerfile default is 8080. Publishing host:<host_port> -> <container_port>
    # only works if the process actually binds <container_port>.
    env_args = [
        "-e", f"PORT={container_port}",
        "-e", "APP_ENV=staging",
        "-e", "LIUHAO_ENV=staging",
        "-e", "ENVIRONMENT=staging",
        "-e", "LIUHAO_KERNEL_POLICY_ENFORCE=CRITICAL",
        "-e", "AUDIT_DB_PATH=/app/data/audit_store.db",
        "-e", "DATABASE_URL=sqlite:////app/data/liuhao_ai_os.db",
        # dev-only placeholder, identical to the staging compose contract.
        # NOT a credential.
        "-e", "JWT_SECRET_KEY=staging-dev-only-insecure",
        "-e", "LOG_LEVEL=INFO",
    ]
    cmd = [docker, "run", "-d", "--name", container,
           "-p", f"{host_port}:{container_port}", *env_args, image]
    cp = run(cmd, timeout=180)
    if cp.returncode != 0:
        return Check(
            "4. container boot + health", "FAIL",
            f"`docker run` exited {cp.returncode}",
            [f"command: {' '.join(cmd)}",
             *tail(cp.stdout), *tail(cp.stderr)],
        ), facts
    facts["run_command"] = " ".join(cmd)

    # -- poll container health ----------------------------------------------
    deadline = time.time() + health_timeout
    health = "starting"
    state = "running"
    last = ""
    while time.time() < deadline:
        insp = run([docker, "inspect", "-f",
                    "{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}"
                    "{{else}}none{{end}}", container], timeout=30)
        last = (insp.stdout.strip() or insp.stderr.strip())
        if insp.returncode == 0 and "|" in last:
            state, health = last.split("|", 1)
            if state != "running":
                break
            if health == "healthy":
                break
        time.sleep(3)

    facts["container_state"] = state
    facts["health_status"] = health
    base = f"http://127.0.0.1:{host_port}"

    # -- always capture logs, whatever happened ------------------------------
    logs = run([docker, "logs", container], timeout=60)
    log_text = (logs.stdout or "") + (logs.stderr or "")
    facts["log_lines"] = len(log_text.splitlines())
    hits = sorted({m for m in LOG_ERROR_MARKERS if m.lower() in log_text.lower()})
    facts["log_error_markers"] = hits
    facts["log_tail"] = tail(log_text, 60)

    if state != "running":
        _cleanup(docker, container, keep)
        return Check(
            "4. container boot + health", "FAIL",
            f"container exited (state={state}, health={health})",
            [f"run: {facts.get('run_command')}",
             "--- docker logs ---", *tail(log_text, 60)],
        ), facts

    if health != "healthy":
        _cleanup(docker, container, keep)
        return Check(
            "4. container boot + health", "FAIL",
            f"container never became healthy within {health_timeout}s "
            f"(last health={health!r})",
            [f"run: {facts.get('run_command')}",
             "--- docker logs ---", *tail(log_text, 60)],
        ), facts

    # -- HTTP endpoints ------------------------------------------------------
    results = {}
    all_ok = True
    for ep in ENDPOINTS:
        url = base + ep
        code, body = http_get(url, timeout=5.0)
        if ep == "/v1/metrics/prometheus":
            ok = code == 200 and bool(body.strip())
        else:
            ok = code == 200 and bool(body.strip())
        results[ep] = {"http_status": code, "ok": ok,
                       "body": body[:800]}
        if not ok:
            all_ok = False

    facts["endpoints"] = results
    _cleanup(docker, container, keep)

    if not all_ok:
        bad = [f"{ep} -> HTTP {r['http_status']}: {r['body'][:200]!r}"
               for ep, r in results.items() if not r["ok"]]
        return Check(
            "4. container boot + health", "FAIL",
            "container healthy but at least one endpoint did not answer 200 "
            "with a non-empty body",
            [f"base url: {base}", *bad,
             "--- docker logs ---", *tail(log_text, 30)],
        ), facts

    return Check(
        "4. container boot + health", "PASS",
        f"container reached healthy; all {len(ENDPOINTS)} contract endpoints "
        f"answered HTTP 200 with non-empty bodies on {base}",
        [f"run: {facts.get('run_command')}",
         f"state={state} health={health}",
         *[f"{ep} -> 200, {len(r['body'])} bytes: {r['body'][:160]!r}"
           for ep, r in results.items()],
         f"boot log markers found: {hits or 'none'}",
         "SCOPE: this is a locally built STAGING image booting in a container.",
         "       It is NOT a production deployment."],
    ), facts


def _cleanup(docker: str, container: str, keep: bool) -> None:
    """Stop + remove the container. The IMAGE is deliberately left in place."""
    if keep:
        return
    run([docker, "stop", "-t", "10", container], timeout=60)
    run([docker, "rm", "-f", container], timeout=60)


# --------------------------------------------------------------------------- #
# Check 5 — frozen evidence untouched (post-run digest comparison)
# --------------------------------------------------------------------------- #
def check_db_untouched(before: Optional[str]) -> Check:
    if before is None:
        return Check("5. frozen evidence untouched", "PASS",
                     f"{FROZEN_DB.name} absent at repo root; nothing to protect", [])
    after = sha256_file(FROZEN_DB)
    if after == before:
        return Check(
            "5. frozen evidence untouched", "PASS",
            f"{FROZEN_DB.name} sha256 unchanged across the whole run: "
            f"{after[:16]}...",
            ["The frozen HC-01 evidence file was not modified by this gate."],
        )
    return Check(
        "5. frozen evidence untouched", "FAIL",
        f"{FROZEN_DB.name} DIGEST CHANGED during the run",
        [f"before: {before}", f"after : {after}",
         "The frozen HC-01 evidence file was modified. Treat as an incident."],
    )


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description="G9b real image build+boot gate")
    ap.add_argument("--host-port", type=int,
                    default=int(os.environ.get("G9B_HOST_PORT", "18000")))
    ap.add_argument("--container-port", type=int,
                    default=int(os.environ.get("G9B_CONTAINER_PORT", "8000")))
    ap.add_argument("--image", default=os.environ.get("G9B_IMAGE",
                                                      "liuhao-ai-os:staging"))
    ap.add_argument("--container", default=os.environ.get(
        "G9B_CONTAINER", "liuhao-staging-verify"))
    ap.add_argument("--build-timeout", type=int,
                    default=int(os.environ.get("G9B_BUILD_TIMEOUT", "1800")))
    ap.add_argument("--health-timeout", type=int,
                    default=int(os.environ.get("G9B_HEALTH_TIMEOUT", "180")))
    ap.add_argument("--keep-container", action="store_true",
                    default=os.environ.get("G9B_KEEP_CONTAINER") == "1")
    args = ap.parse_args()

    print("=" * 74)
    print("G9b REAL IMAGE BUILD + BOOT + HEALTH GATE")
    print("repo:", REPO_ROOT)
    print("ran :", datetime.now(timezone.utc).isoformat())
    print("SCOPE: locally built STAGING image. NOT a production deployment.")
    print("       Production target/registry = SOVEREIGN HUMAN DECISION.")
    print("=" * 74)

    db_before = sha256_file(FROZEN_DB)
    if db_before:
        print(f"frozen evidence guard armed: {FROZEN_DB.name} sha256="
              f"{db_before[:16]}...")

    checks: list[Check] = []
    facts: dict = {"host_port": args.host_port,
                   "container_port": args.container_port,
                   "image": args.image}

    # 1 preflight -- if the daemon is absent, nothing downstream can run.
    c1 = check_docker_daemon()
    checks.append(c1)

    c2 = check_freeze_guard()
    checks.append(c2)

    daemon_ok = c1.status == "PASS"
    if daemon_ok:
        c3, bf = check_build(args.image, args.build_timeout)
        checks.append(c3)
        facts.update(bf)
        if c3.status == "PASS":
            c4, rf = check_boot(args.image, args.container, args.host_port,
                                args.container_port, args.health_timeout,
                                args.keep_container)
            checks.append(c4)
            facts.update(rf)
        else:
            checks.append(Check(
                "4. container boot + health", "NOT_VERIFIED",
                "skipped: image build did not succeed, so there is nothing to boot",
                ["NOT a partial pass. No container was started."],
            ))
    else:
        checks.append(Check(
            "3. docker build", "NOT_VERIFIED",
            "skipped: no docker daemon",
            ["No image was built. This is a GAP, not a proven failure.",
             "The Dockerfile was never executed by a build engine."],
        ))
        checks.append(Check(
            "4. container boot + health", "NOT_VERIFIED",
            "skipped: no docker daemon",
            ["No container was started. No HTTP endpoint was answered."],
        ))

    c5 = check_db_untouched(db_before)
    checks.append(c5)

    # -- report --------------------------------------------------------------
    print()
    for ch in checks:
        print(f"[{ch.status:>12}] {ch.name}")
        print(f"    evidence: {ch.evidence}")
        for d in ch.detail:
            print(f"      - {d}")
    print("=" * 74)

    any_fail = any(ch.status == "FAIL" for ch in checks)
    any_nv = any(ch.status == "NOT_VERIFIED" for ch in checks)

    if any_fail:
        verdict = "FAIL"
    elif any_nv:
        verdict = "NOT_VERIFIED"
    else:
        verdict = "PASS"

    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "repo_root": str(REPO_ROOT),
        "verdict": verdict,
        "scope": "locally built STAGING image booting in a container; "
                 "NOT a production deployment",
        "production_target": "HUMAN_DECISION_REQUIRED",
        "image_pushed_to_registry": False,
        "checks": [{"name": c.name, "status": c.status,
                    "evidence": c.evidence, "detail": c.detail} for c in checks],
        "facts": facts,
    }
    report_path = os.environ.get("G9B_REPORT") or str(
        Path(tempfile.gettempdir()) / "g9b_image_build_report.json")
    try:
        Path(report_path).write_text(json.dumps(summary, indent=2),
                                     encoding="utf-8")
        print(f"JSON report written to: {report_path}")
    except Exception as e:
        print(f"(could not write JSON report: {e})")

    print("VERDICT:", verdict)
    if verdict == "PASS":
        print("  meaning: image built locally + container booted healthy + all "
              "contract endpoints answered 200.")
        print("  NOT meaning: production deployment verified.")
    print("=" * 74)

    return 1 if any_fail else (2 if any_nv else 0)


if __name__ == "__main__":
    sys.exit(main())
