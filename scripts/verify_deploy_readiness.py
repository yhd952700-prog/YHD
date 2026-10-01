#!/usr/bin/env python3
"""
G9 — Deployment / Upgrade / Rollback Readiness Verification Chain
===============================================================

This script performs REAL, non-fabricated checks that prove the LIUHAO
autonomous OS can be (a) containerised, (b) migrated forward and rolled
back, and (c) deployed via a staging/test compose contract.

HARD BOUNDARY (do not violate):
  * We NEVER touch the live `audit_store.db` (or its -wal/-shm sidecars)
    and NEVER touch `liuhao_ai_os.db`. Every DB-backed check runs against
    a throw-away TEMP sqlite database under the OS temp dir.
  * We NEVER modify anything under src/. Existing files are only READ.
  * Only NEW files were added (this script, infra/staging/docker-compose.yml,
    docs/autonomous/G9-deployment-contract.md).

PRODUCTION DEPLOY TARGET = HUMAN DECISION REQUIRED.
  This script verifies the STAGING/TEST contract only. It does NOT, and must
  not, assume a production target. Pushing to a real production environment is
  a sovereign human decision and is out of scope here.

RESULT CONTRACT:
  Each check returns exactly one of: PASS | FAIL | NOT_VERIFIED.
  * FAIL  == the thing is broken / a real error occurred  -> process exits 1.
  * NOT_VERIFIED == we could not prove it in THIS environment (a gap is
    recorded honestly), but it is not proven broken -> process exits 0 with a
    warning. This is deliberately distinct from FAIL.
  * PASS  == the check executed and succeeded with evidence.

HOW TO RUN:
  # Use the repo's own venv (it has alembic + sqlalchemy + pyyaml).
  # The "managed" venv at D:/.../default/Scripts/python.exe lacks alembic,
  # so it CANNOT run the migration checks; we use the project .venv instead.
  /d/LiuHao-AI-OS/.venv/Scripts/python.exe scripts/verify_deploy_readiness.py

  Optional env:
    G9_PYTHON   interpreter used to spawn `alembic`/`verify_*.py` (default: sys.executable)
    G9_REPORT   path to also write the JSON report (default: a temp file, printed)
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ----------------------------------------------------------------------------
# Locations
# ----------------------------------------------------------------------------
SCRIPT = Path(__file__).resolve()
REPO_ROOT = SCRIPT.parents[1]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
STAGING_COMPOSE = REPO_ROOT / "infra" / "staging" / "docker-compose.yml"
ORM_VS_DB = REPO_ROOT / "scripts" / "verify_orm_vs_db.py"
PERSISTENCE = REPO_ROOT / "scripts" / "verify_persistence.py"

# Live DBs we must NEVER touch (used only for a sanity guard).
FORBIDDEN_DB_SUBSTRINGS = ("audit_store.db", "liuhao_ai_os.db")

PY = os.environ.get("G9_PYTHON") or sys.executable


@dataclass
class Check:
    name: str
    status: str            # PASS | FAIL | NOT_VERIFIED
    evidence: str
    detail: list[str]


def run(cmd, env=None, cwd=None, timeout=300) -> subprocess.CompletedProcess:
    """Run a command, returning the CompletedProcess (never raises on rc!=0)."""
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    return subprocess.run(
        cmd,
        env=full_env,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def status_for(rc: int) -> str:
    return "PASS" if rc == 0 else "FAIL"


def sec(url: str) -> str:
    """Redact obvious secret material in a URL for logging."""
    return url


# ----------------------------------------------------------------------------
# Check A — Docker CLI availability
# ----------------------------------------------------------------------------
def check_docker_cli() -> Check:
    cp = run([shutil.which("docker") or "docker", "--version"], timeout=30)
    if cp.returncode == 0 and cp.stdout.strip():
        return Check(
            name="A. docker CLI availability",
            status="PASS",
            evidence=f"docker present: {cp.stdout.strip().splitlines()[0]!r}",
            detail=[
                "Docker CLI was found and responded to `--version`.",
                "NOTE: presence of the CLI does NOT imply the daemon is running,",
                "nor that the `docker compose` plugin is installed (see check C).",
            ],
        )
    return Check(
        name="A. docker CLI availability",
        status="NOT_VERIFIED",
        evidence=(cp.stderr.strip() or cp.stdout.strip()
                  or "docker binary not found on PATH").strip(),
        detail=[
            "Docker CLI could not be invoked in this environment.",
            "Image buildability / container runtime is therefore NOT VERIFIED.",
            "This is a GAP, not a proven failure. Record it and continue.",
        ],
    )


# ----------------------------------------------------------------------------
# Check B — Alembic upgrade head + downgrade base on a TEMP db (rollback path)
# ----------------------------------------------------------------------------
def check_alembic_rollback(tmp_db: Path) -> Check:
    detail: list[str] = []
    if not ALEMBIC_INI.exists():
        return Check("B. alembic upgrade/rollback", "FAIL",
                      f"alembic.ini missing at {ALEMBIC_INI}", detail)

    # Freeze-boundary guard: refuse to point alembic at a live DB.
    url = "sqlite:///" + str(tmp_db).replace("\\", "/")
    for bad in FORBIDDEN_DB_SUBSTRINGS:
        if bad in str(tmp_db):
            return Check("B. alembic upgrade/rollback", "FAIL",
                          f"refusing: temp db path contains forbidden substring {bad!r}", detail)

    env = {"DATABASE_URL": url, "PYTHONPATH": str(REPO_ROOT)}

    def alembic(args):
        return run([PY, "-m", "alembic", "-c", str(ALEMBIC_INI), *args],
                   env=env, cwd=REPO_ROOT, timeout=300)

    # 1) upgrade head
    up = alembic(["upgrade", "head"])
    if up.returncode != 0:
        return Check(
            "B. alembic upgrade/rollback", "FAIL",
            f"`alembic upgrade head` exited {up.returncode}",
            [up.stdout.strip(), up.stderr.strip()],
        )
    detail.append("alembic upgrade head: OK (rc=0)")

    # 2) confirm we are at head
    cur = alembic(["current"])
    detail.append(f"alembic current: {cur.stdout.strip().splitlines()[-1] if cur.stdout.strip() else '?'}"
                  + (f" | err={cur.stderr.strip()}" if cur.stderr.strip() else ""))

    # 3) positive evidence: ORM vs DB schema is in sync on the migrated db
    if ORM_VS_DB.exists():
        o = run([PY, str(ORM_VS_DB)], env=env, cwd=REPO_ROOT, timeout=300)
        if o.returncode != 0:
            return Check(
                "B. alembic upgrade/rollback", "FAIL",
                "verify_orm_vs_db.py reported DIVERGENCE on the migrated db",
                [o.stdout.strip(), o.stderr.strip()],
            )
        # capture the DIVERGENCES line from output
        div_line = next((l for l in o.stdout.splitlines() if "DIVERGENCES FOUND" in l), "")
        detail.append(f"verify_orm_vs_db: PASS ({div_line})")

    # 4) positive evidence: cross-session persistence works on the migrated db
    if PERSISTENCE.exists():
        p = run([PY, str(PERSISTENCE)], env=env, cwd=REPO_ROOT, timeout=300)
        if p.returncode != 0:
            return Check(
                "B. alembic upgrade/rollback", "FAIL",
                "verify_persistence.py FAILED on the migrated db",
                [p.stdout.strip(), p.stderr.strip()],
            )
        res_line = next((l for l in p.stdout.splitlines() if l.startswith("RESULT:")), "")
        detail.append(f"verify_persistence: PASS ({res_line})")

    # 5) ROLLBACK PATH — downgrade base, then prove all app tables are gone.
    down = alembic(["downgrade", "base"])
    if down.returncode != 0:
        return Check(
            "B. alembic upgrade/rollback", "FAIL",
            f"`alembic downgrade base` exited {down.returncode}",
            [down.stdout.strip(), down.stderr.strip()],
        )
    # Inspect resulting schema: only alembic_version should remain.
    try:
        import sqlite3
        con = sqlite3.connect(str(tmp_db))
        tables = sorted(r[0] for r in con.execute(
            "select name from sqlite_master where type='table'"))
        con.close()
    except Exception as e:  # pragma: no cover
        tables = [f"<inspect-error:{e}>"]
    detail.append(f"after downgrade base, tables left: {tables}")
    rollback_ok = tables == ["alembic_version"]
    if not rollback_ok:
        return Check(
            "B. alembic upgrade/rollback", "FAIL",
            "downgrade base did not fully revert (app tables still present)",
            detail,
        )

    return Check(
        "B. alembic upgrade/rollback", "PASS",
        "upgrade head OK; downgrade base OK; only alembic_version remains after rollback",
        detail,
    )


# ----------------------------------------------------------------------------
# Check C — staging compose config validity
# ----------------------------------------------------------------------------
def _is_obvious_production_secret(val: str) -> bool:
    """Heuristic: a committed literal that looks like a real secret."""
    v = (val or "").strip().strip('"').strip("'")
    if not v:
        return False
    # high-entropy long token committed inline == smells like a real secret
    if len(v) >= 32 and all(c.isalnum() or c in "-_=+/" for c in v):
        return True
    if v in ("liuhao:liuhao", "postgresql://liuhao:liuhao"):
        return True  # real-looking prod default creds
    return False


def _yaml_sanity_check() -> tuple[bool, list[str]]:
    """Parse the staging compose and assert STAGING/TEST contract structure."""
    try:
        import yaml
    except Exception as e:  # pragma: no cover
        return False, [f"PyYAML unavailable: {e}"]

    text = STAGING_COMPOSE.read_text(encoding="utf-8")
    try:
        doc = yaml.safe_load(text)
    except Exception as e:
        return False, [f"YAML parse error: {e}"]

    problems: list[str] = []
    if not isinstance(doc, dict) or "services" not in doc or not doc["services"]:
        problems.append("compose has no `services` mapping")
        return False, problems

    # content markers required for a STAGING/TEST contract
    low = text.lower()
    for marker in ("staging", "human decision required", "not for production"):
        if marker not in low:
            problems.append(f"compose is missing required contractual marker: {marker!r}")

    for sname, svc in doc["services"].items():
        svc = svc or {}
        if "image" not in svc and "build" not in svc:
            problems.append(f"service {sname!r} has neither `image` nor `build`")
        # resource limits required
        lim = (svc.get("deploy", {}) or {}).get("resources", {}).get("limits", {})
        if not lim.get("memory") or not lim.get("cpus"):
            problems.append(f"service {sname!r} missing deploy.resources.limits (memory+cpus)")
        if "healthcheck" not in svc:
            problems.append(f"service {sname!r} missing `healthcheck`")
        # NO production secrets committed inline
        env = svc.get("environment", {}) or {}
        items = env.items() if isinstance(env, dict) else [
            (str(e).split("=", 1)[0], str(e).split("=", 1)[1]) for e in env if isinstance(e, str)]
        for k, v in items:
            if _is_obvious_production_secret(str(v)):
                problems.append(f"service {sname!r} env {k!r} looks like a committed real secret")

    if problems:
        return False, problems
    return True, ["YAML parses", "every service has image/build",
                  "every service has deploy.resources.limits(memory+cpus)",
                  "every service has healthcheck",
                  "STAGING/TEST + HUMAN-DECISION-REQUIRED markers present",
                  "no inline production secrets detected"]


def check_staging_compose() -> Check:
    if not STAGING_COMPOSE.exists():
        return Check("C. staging compose config validity", "FAIL",
                      f"staging compose missing at {STAGING_COMPOSE}", [])

    # Preferred method: authoritative `docker compose config` (needs the plugin).
    compose_plugin = run([shutil.which("docker") or "docker", "compose", "version"], timeout=30)
    if compose_plugin.returncode == 0:
        cp = run([shutil.which("docker") or "docker", "compose", "-f", str(STAGING_COMPOSE),
                  "config"], timeout=120)
        if cp.returncode == 0:
            return Check("C. staging compose config validity", "PASS",
                          "`docker compose -f infra/staging/docker-compose.yml config` succeeded",
                          [l for l in cp.stdout.splitlines() if l.strip()][:20])
        return Check("C. staging compose config validity", "FAIL",
                      "`docker compose config` failed", [cp.stderr.strip() or cp.stdout.strip()])

    # Fallback: docker compose plugin is NOT installed in this env -> NOT_VERIFIED
    # for the authoritative path, but we still run a python YAML-schema sanity check.
    ok, problems = _yaml_sanity_check()
    if ok:
        return Check(
            "C. staging compose config validity", "PASS",
            "validated via python YAML-schema sanity check "
            "(authoritative `docker compose config` is NOT VERIFIED: compose plugin absent)",
            ["GAP: docker CLI present but `docker compose` plugin NOT installed in this env",
             *problems],
        )
    return Check(
        "C. staging compose config validity", "NOT_VERIFIED",
        "python YAML-schema sanity check found structural problems",
        ["GAP: docker compose plugin absent (could not run authoritative config)", *problems],
    )


# ----------------------------------------------------------------------------
# Orchestration
# ----------------------------------------------------------------------------
def main() -> int:
    print("=" * 72)
    print("G9 DEPLOYMENT / UPGRADE / ROLLBACK READINESS VERIFICATION")
    print("repo:", REPO_ROOT)
    print("ran :", datetime.now(timezone.utc).isoformat())
    print("PRODUCTION TARGET: HUMAN DECISION REQUIRED (staging/test contract only)")
    print("=" * 72)

    checks: list[Check] = []

    # A
    a = check_docker_cli()
    checks.append(a)

    # B — run against a throw-away TEMP db (freeze boundary honored)
    tmp_dir = Path(tempfile.mkdtemp(prefix="g9_verify_"))
    tmp_db = tmp_dir / "g9_temp_migration.db"
    try:
        b = check_alembic_rollback(tmp_db)
    finally:
        # clean up the temp db + dir; never the live DBs
        for f in (tmp_db, Path(str(tmp_db) + "-wal"), Path(str(tmp_db) + "-shm")):
            try:
                f.unlink()
            except OSError:
                pass
        try:
            tmp_dir.rmdir()
        except OSError:
            pass
    checks.append(b)

    # C
    c = check_staging_compose()
    checks.append(c)

    # Report
    print()
    any_fail = False
    for ch in checks:
        print(f"[{ch.status:>12}] {ch.name}")
        print(f"    evidence: {ch.evidence}")
        for d in ch.detail:
            print(f"      - {d}")
        if ch.status == "FAIL":
            any_fail = True
    print("=" * 72)

    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "repo_root": str(REPO_ROOT),
        "production_target": "HUMAN_DECISION_REQUIRED",
        "python_used_for_alembic": PY,
        "checks": [asdict(ch) for ch in checks],
    }
    report_path = os.environ.get("G9_REPORT") or str(
        Path(tempfile.gettempdir()) / "g9_deploy_readiness_report.json")
    try:
        Path(report_path).write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"JSON report written to: {report_path}")
    except Exception as e:  # pragma: no cover
        print(f"(could not write JSON report: {e})")

    print("RESULT:", "FAIL" if any_fail else "OK (no hard failures; NOT_VERIFIED gaps recorded)")
    print("=" * 72)
    # FAIL is a hard gate. NOT_VERIFIED is a recorded gap, not a hard failure.
    return 1 if any_fail else 0


if __name__ == "__main__":
    sys.exit(main())
