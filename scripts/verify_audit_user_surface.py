"""Verify the USER-FACING audit query/verify surface (P8 Explainability/Audit UX).

The audit mechanism itself has always been real (append -> verify_integrity() ->
tamper detected -> corruption quarantined, never deleted). What was missing is
that **no human could reach it**: no HTTP endpoint, no CLI. This script proves
the surface added by ``src/gateway/audit.py`` is real and usable:

 1. Real audit events are generated first (a REAL goal execution with a real
    file_write + python_compute executor), so nothing here inspects an empty
    or fabricated store.
 2. ``GET /v1/audit/events`` returns those events over REAL HTTP, and filtering
    by a known correlation_id returns exactly that goal's subset -- i.e. the
    goal -> action linkage is visible to a user, not just to the kernel.
 3. ``GET /v1/audit/verify`` reports ok=true on an INTACT chain.
 4. TAMPER TEST: against a THROWAWAY copy of the audit db, one row's content is
    rewritten with raw SQL, and the endpoint MUST return ok=false and name the
    failure. This is the check that separates a real surface from a stub that
    always answers True -- the "silent success" trap.
 5. Every audit endpoint returns 401 without a bearer token (the read-only
    surface is still sovereignty-gated; the audit trail is not anonymously
    readable).
 6. No mutation route is mounted under /v1/audit (a self-healing chain is not
    evidence).

Auth is bootstrapped exactly as the gateway requires: register a human identity
(``scripts/register_human_identity.py``) against the temp workspace, mint a
token (``scripts/issue_console_token.py --principal <id>``), present it in the
``X-Liuhao-Token`` header.

Persistence is redirected to temp (LIUHAO_WORKSPACE_ROOT + AUDIT_DB_PATH +
identity/secret/JWT env) BEFORE any import, so the frozen HC-01 production
audit evidence is never touched.

Exits non-zero on any failure (prints PASS/FAIL per check).
"""

from __future__ import annotations

import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import uuid

# --- Redirect ALL persistence to temp BEFORE importing anything that reads it.
_TMP_ROOT = tempfile.mkdtemp(prefix="liuhao_audit_ux_")
_TMP_WS = os.path.join(_TMP_ROOT, "workspace")
_TMP_AUDIT_DIR = os.path.join(_TMP_ROOT, "audit")
_TMP_AUDIT_DB = os.path.join(_TMP_AUDIT_DIR, "audit_store.db")
os.makedirs(_TMP_WS, exist_ok=True)
os.makedirs(_TMP_AUDIT_DIR, exist_ok=True)

os.environ["LIUHAO_WORKSPACE_ROOT"] = _TMP_WS
os.environ["AUDIT_DB_PATH"] = _TMP_AUDIT_DB
os.environ["LIUHAO_HUMAN_IDENTITIES_FILE"] = os.path.join(
    _TMP_ROOT, "human_identities.json")
os.environ["LIUHAO_AUTH_SECRETS_FILE"] = os.path.join(_TMP_ROOT, "auth_secrets.json")
# A durable signing key shared by the token-minting subprocess and the gateway.
os.environ["LIUHAO_JWT_SECRET"] = secrets.token_hex(32)
# The registry is fail-closed: without a row-authentication key every stored
# human is REFUSED at load (PHASE 3.6 / A3). A real deployment sets this; the
# test pins a throwaway one so registration can actually be verified.
os.environ["LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY"] = secrets.token_hex(32)
os.environ.pop("LIUHAO_ALLOW_SIMULATED_EXECUTION", None)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

PY = sys.executable

import httpx  # noqa: E402
import uvicorn  # noqa: E402

from src.ai.workspace import resolve_in_workspace  # noqa: E402
from src.kernels.execution import ExecutionEngine, Goal, Task  # noqa: E402

WORKSPACE = os.environ["LIUHAO_WORKSPACE_ROOT"]
AUDIT_DB = os.environ["AUDIT_DB_PATH"]

PRINCIPAL = "audit.operator"
_PASSWORD = "tmp-" + secrets.token_hex(8)

# The action name the execution kernel records for a task run; used to prove the
# endpoint can filter by action, and that events really come from real actions.
EXEC_ACTION = "execution.execute"

_JWT_RE = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")

results = []


def check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    results.append((status, label, detail))
    print(f"[{status}] {label}" + (f" -- {detail}" if detail else ""))
    return bool(condition)


# ---------------------------------------------------------------------------
# 1. real audit events
# ---------------------------------------------------------------------------


def real_executor_factory(workspace_root):
    """A REAL capability executor (touches the filesystem / runs Python for real)."""

    def executor(capability_id, inputs):
        if capability_id == "file_write":
            abs_path = resolve_in_workspace(inputs.get("path"), workspace_root)
            os.makedirs(os.path.dirname(abs_path) or ".", exist_ok=True)
            with open(abs_path, "w", encoding="utf-8") as fh:
                fh.write(inputs.get("content", ""))
            return {"success": True, "written": abs_path, "status": "executed"}
        if capability_id == "python_compute":
            n = int(inputs.get("n", 10))
            return {"success": True, "result": sum(range(1, n + 1)), "status": "executed"}
        raise ValueError(f"no REAL executor for capability {capability_id!r}")

    return executor


def generate_real_events(correlation_id: str):
    """Run a REAL goal against the temp audit db. Returns the execution context."""
    import src.kernels.audit as _audit_mod

    # Force the store to be constructed against our temp path.
    _audit_mod._audit_store = None

    engine = ExecutionEngine(scope="L1")
    engine.set_capability_executor(real_executor_factory(WORKSPACE))

    goal = Goal(
        id="G_" + uuid.uuid4().hex[:8],
        natural_language="write deliverable.txt containing HELLO; compute sum 1..10",
        scope="L1",
        correlation_id=correlation_id,
    )
    fw_task = Task(
        id="T_fw_" + uuid.uuid4().hex[:8],
        goal_id=goal.id,
        name="WriteFile",
        description="Write a file inside the workspace (REAL)",
        capability_id="file_write",
        capability_namespace="kernel",
        inputs={"path": "deliverable.txt", "content": "HELLO"},
        expected_outputs={"content": "HELLO"},
        scope=goal.scope,
    )
    pc_task = Task(
        id="T_pc_" + uuid.uuid4().hex[:8],
        goal_id=goal.id,
        name="Compute",
        description="Run a local computation (REAL)",
        capability_id="python_compute",
        capability_namespace="kernel",
        inputs={"n": 10},
        expected_outputs={"result": 55},
        scope=goal.scope,
    )
    engine.decomposer.decompose = lambda g: [fw_task, pc_task]
    ctx = engine.execute_goal(goal, plan_mode="sequential")
    return ctx, fw_task, pc_task


# ---------------------------------------------------------------------------
# 2. auth bootstrap (exactly what the gateway accepts)
# ---------------------------------------------------------------------------


def _run(args, stdin=None):
    proc = subprocess.run(
        [PY] + args,
        cwd=REPO_ROOT,
        env=os.environ.copy(),
        input=stdin,
        capture_output=True,
        text=True,
        timeout=180,
    )
    return proc


def bootstrap_auth():
    reg = _run([
        "scripts/register_human_identity.py",
        "--principal", PRINCIPAL,
        "--display-name", "Audit Operator",
        "--password", _PASSWORD,
    ])
    if reg.returncode != 0:
        raise RuntimeError(
            f"register_human_identity failed ({reg.returncode}):\n"
            f"{reg.stdout}\n{reg.stderr}"
        )

    issue = _run([
        "scripts/issue_console_token.py",
        "--principal", PRINCIPAL,
        "--ttl", "3600",
    ])
    if issue.returncode != 0:
        raise RuntimeError(
            f"issue_console_token failed ({issue.returncode}):\n"
            f"{issue.stdout}\n{issue.stderr}"
        )

    token = None
    for line in issue.stdout.splitlines():
        line = line.strip()
        if _JWT_RE.match(line):
            token = line
            break
    if token is None:
        raise RuntimeError(f"no JWT found in issue_console_token output:\n{issue.stdout}")
    return token


# ---------------------------------------------------------------------------
# 3. a TRUE HTTP server (uvicorn in a background thread)
# ---------------------------------------------------------------------------


def _free_port():
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    def __init__(self):
        from src.gateway.main import get_app

        self.port = _free_port()
        config = uvicorn.Config(
            get_app(), host="127.0.0.1", port=self.port, log_level="warning")
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def __enter__(self):
        self._thread.start()
        deadline = time.time() + 60
        while not self._server.started and time.time() < deadline:
            time.sleep(0.05)
        if not self._server.started:
            raise RuntimeError("uvicorn server did not start")
        return self

    def __exit__(self, *exc):
        self._server.should_exit = True
        self._thread.join(timeout=30)

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.port}"


def get(base_url, path, token=None, **params):
    headers = {}
    if token:
        # The gateway's private header (policy.TOKEN_HEADER) wins over
        # Authorization, matching what the console sends.
        headers["X-Liuhao-Token"] = f"Bearer {token}"
    url = base_url + path
    with httpx.Client(timeout=120.0) as client:
        return client.get(url, params=params, headers=headers)


def post(base_url, path, token=None):
    headers = {}
    if token:
        headers["X-Liuhao-Token"] = f"Bearer {token}"
    with httpx.Client(timeout=120.0) as client:
        return client.post(base_url + path, headers=headers)


# ---------------------------------------------------------------------------
# checks
# ---------------------------------------------------------------------------


def check_events_and_filters(base_url, token, known_corr):
    r = get(base_url, "/v1/audit/events", token, limit=500)
    ok = check("GET /v1/audit/events returns 200", r.status_code == 200,
               detail=f"status={r.status_code} body={r.text[:200]}")
    if not ok:
        return None
    body = r.json()
    all_events = body.get("events", [])
    check("GET /v1/audit/events returns real, non-empty events",
          len(all_events) > 0, detail=f"count={body.get('count')}")

    exec_events = [e for e in all_events if (e.get("details") or {}).get("action") == EXEC_ACTION]
    check(f"the real goal produced {EXEC_ACTION} events",
          len(exec_events) >= 2, detail=f"execution.execute={len(exec_events)}")

    # --- correlation_id filter: the goal -> action linkage is user-visible ---
    r2 = get(base_url, "/v1/audit/events", token, correlation_id=known_corr, limit=500)
    ok2 = check("GET /v1/audit/events?correlation_id=... returns 200",
                r2.status_code == 200, detail=f"status={r2.status_code}")
    if not ok2:
        return None
    filtered = r2.json().get("events", [])
    all_match = bool(filtered) and all(e.get("correlation_id") == known_corr for e in filtered)
    check("correlation_id filter returns EXACTLY the matching subset",
          all_match, detail=f"matched={len(filtered)}, corr={known_corr}")
    check("correlation_id filter is not vacuous (>=2 events tied to the goal)",
          len(filtered) >= 2, detail=f"matched={len(filtered)}")

    # A correlation id that does not exist must return an honest EMPTY list,
    # not the full chain and not an error.
    r3 = get(base_url, "/v1/audit/events", token, correlation_id="NO-SUCH-CORR-" + uuid.uuid4().hex)
    check("unknown correlation_id returns an honest empty list",
          r3.status_code == 200 and r3.json().get("count") == 0,
          detail=f"status={r3.status_code} count={r3.json().get('count')}")

    # --- action filter (pushed down to SQL, so limit means "N matching") ---
    r4 = get(base_url, "/v1/audit/events", token, action=EXEC_ACTION, limit=500)
    by_action = r4.json().get("events", []) if r4.status_code == 200 else []
    check(f"action filter returns only {EXEC_ACTION} events",
          r4.status_code == 200 and bool(by_action)
          and all((e.get("details") or {}).get("action") == EXEC_ACTION for e in by_action),
          detail=f"status={r4.status_code} matched={len(by_action)}")

    # --- single event by seq ---
    seq = all_events[0].get("seq")
    r5 = get(base_url, f"/v1/audit/events/{seq}", token)
    got_seq = (r5.json().get("event") or {}).get("seq") if r5.status_code == 200 else None
    check(f"GET /v1/audit/events/{{seq}} returns that exact event",
          r5.status_code == 200 and got_seq == seq,
          detail=f"status={r5.status_code} asked={seq} got={got_seq}")

    # --- summary ---
    r6 = get(base_url, "/v1/audit/summary", token)
    check("GET /v1/audit/summary returns real counts",
          r6.status_code == 200 and r6.json().get("total_events", 0) > 0,
          detail=f"status={r6.status_code} body={str(r6.json())[:160]}")
    return all_events


def check_verify_intact(base_url, token):
    r = get(base_url, "/v1/audit/verify", token)
    ok = check("GET /v1/audit/verify returns 200 on an intact chain",
               r.status_code == 200, detail=f"status={r.status_code} body={r.text[:200]}")
    if not ok:
        return None
    body = r.json()
    check("GET /v1/audit/verify reports ok=true on an INTACT chain",
          body.get("ok") is True, detail=f"ok={body.get('ok')}")
    check("verify reports a real entries_checked (>0)",
          isinstance(body.get("entries_checked"), int) and body.get("entries_checked") > 0,
          detail=f"entries_checked={body.get('entries_checked')}")
    return body


def check_unauthenticated(base_url):
    for method, path in (
        ("GET", "/v1/audit/events"),
        ("GET", "/v1/audit/events/1"),
        ("GET", "/v1/audit/verify"),
        ("POST", "/v1/audit/verify"),
        ("GET", "/v1/audit/summary"),
    ):
        r = post(base_url, path) if method == "POST" else get(base_url, path)
        check(f"unauthenticated {method} {path} -> 401",
              r.status_code == 401, detail=f"status={r.status_code}")

    # A garbage token must also be refused -- "present but invalid" is not auth.
    for path in ("/v1/audit/events", "/v1/audit/verify"):
        r = get(base_url, path, token="not.a.real.token")
        check(f"invalid token GET {path} -> 401",
              r.status_code == 401, detail=f"status={r.status_code}")


def check_no_mutation_routes():
    """No write/delete surface under /v1/audit -- a self-healing chain is not evidence.

    Reads the OpenAPI spec rather than ``app.routes``: on FastAPI 0.141 the
    latter yields ``_IncludedRouter`` wrappers with neither ``path`` nor
    ``methods``, so a naive scan finds nothing and passes vacuously -- exactly
    the kind of check that looks like proof and isn't.
    """
    from src.gateway.main import get_app

    spec = get_app().openapi()
    audit_paths = {p: sorted(m for m in ops if m.lower() in (
        "get", "post", "put", "patch", "delete"))
        for p, ops in spec["paths"].items() if p.startswith("/v1/audit")}

    check("the audit surface is actually mounted (not zero routes)",
          len(audit_paths) >= 4, detail=f"paths={sorted(audit_paths)}")

    # The ONLY non-GET method allowed is POST /v1/audit/verify, which runs the
    # read-only verification (it exists as a POST so no cache can serve a stale
    # "chain is fine" answer).
    non_get = [(p, m) for p, methods in audit_paths.items()
               for m in methods if m != "get"]
    unexpected = [(p, m) for p, m in non_get
                  if not (m == "post" and p == "/v1/audit/verify")]
    check("no mutation route is mounted under /v1/audit (no PUT/POST-write/DELETE/PATCH)",
          not unexpected, detail=f"unexpected={unexpected}")
    check("the only non-GET audit route is POST /v1/audit/verify",
          sorted(non_get) == [("/v1/audit/verify", "post")],
          detail=f"non-GET={sorted(non_get)}")


def tamper_and_verify():
    """THROWAWAY copy -> raw SQL tamper -> the endpoint MUST say ok=false."""
    throwaway = os.path.join(_TMP_ROOT, "tampered", "audit_store.db")
    os.makedirs(os.path.dirname(throwaway), exist_ok=True)
    # Copy the whole store INCLUDING -wal/-shm so no committed event is lost.
    for suffix in ("", "-wal", "-shm"):
        src = AUDIT_DB + suffix
        if os.path.exists(src):
            shutil.copy2(src, throwaway + suffix)

    # --- raw SQL tamper: rewrite the content of one real row ---
    conn = sqlite3.connect(throwaway)
    try:
        row = conn.execute(
            "SELECT seq, event_id, outcome FROM audit_events "
            "ORDER BY seq ASC LIMIT 1 OFFSET 1").fetchone()
        if row is None:
            conn.close()
            check("tamper test found a row to corrupt", False)
            return
        seq, event_id, outcome = row
        # Change the recorded OUTCOME (content), leaving event_hash untouched --
        # exactly the "rewrite history but leave the hash" attack.
        conn.execute(
            "UPDATE audit_events SET outcome = ? WHERE seq = ?",
            ("TAMPERED-BY-TEST", seq))
        conn.commit()
    finally:
        conn.close()

    # --- point the process at the throwaway copy and restart the server ---
    os.environ["AUDIT_DB_PATH"] = throwaway
    import src.kernels.audit as _audit_mod

    _audit_mod._audit_store = None

    with Server() as srv:
        base = srv.base_url
        r = get(base, "/v1/audit/verify", TOKEN)
        status_ok = r.status_code in (200, 503)
        body = r.json()
        ok_flag = body.get("ok")
        first = body.get("first_failure")
        failures = (body.get("details") or {}).get("failures") or []

        check("TAMPER: verify endpoint responds (200 or an honest 503)",
              status_ok, detail=f"status={r.status_code}")
        check("TAMPER: verify endpoint reports ok=false on a TAMPERED chain",
              ok_flag is False, detail=f"ok={ok_flag} status={r.status_code}")
        check("TAMPER: the failure is pinpointed (first_failure names a seq)",
              isinstance(first, str) and "seq=" in first,
              detail=f"first_failure={first!r}")
        check("TAMPER: failures list is non-empty",
              len(failures) > 0, detail=f"failures={failures[:3]}")
        check("TAMPER: details say no mutation was performed (read-only surface)",
              (body.get("details") or {}).get("mutation_performed") is False,
              detail=f"mutation_performed={(body.get('details') or {}).get('mutation_performed')}")
        # The read surface must still work on the tampered store: an auditor has
        # to be able to LOOK at the tampered row, not just be told "broken".
        r2 = get(base, f"/v1/audit/events/{seq}", TOKEN)
        seen = (r2.json().get("event") or {}).get("outcome") if r2.status_code == 200 else None
        check("TAMPER: the tampered row is still readable through /events/{seq}",
              r2.status_code == 200 and seen == "TAMPERED-BY-TEST",
              detail=f"status={r2.status_code} outcome={seen!r}")
        print()
        print("    observed tamper response:")
        print(f"      ok             = {ok_flag}")
        print(f"      entries_checked= {body.get('entries_checked')}")
        print(f"      first_failure  = {first}")
        print(f"      failures       = {failures[:5]}")
        print()


TOKEN = None


def main():
    global TOKEN

    known_corr = "GOAL-UX-" + uuid.uuid4().hex[:16]
    print(f"temp root: {_TMP_ROOT}")
    print(f"audit db (temp): {AUDIT_DB}")
    print(f"goal correlation_id: {known_corr}")
    print()

    # ---- 1. real events -------------------------------------------------
    ctx, _fw, pc = generate_real_events(known_corr)
    pc_result = ctx.task_results.get(pc.id)
    check("a REAL goal execution produced real side effects (sum 1..10 == 55)",
          pc_result is not None and pc_result.success
          and isinstance(pc_result.output, dict)
          and pc_result.output.get("result") == 55,
          detail=f"output={getattr(pc_result, 'output', None)}")

    # ---- 2. auth bootstrap ----------------------------------------------
    TOKEN = bootstrap_auth()
    check("registered a human identity and minted a console token",
          bool(TOKEN), detail=f"principal={PRINCIPAL}")

    # ---- 3. intact chain over real HTTP ----------------------------------
    with Server() as srv:
        base = srv.base_url
        print(f"server (intact): {base}")
        check_events_and_filters(base, TOKEN, known_corr)
        check_verify_intact(base, TOKEN)
        check_unauthenticated(base)

    check_no_mutation_routes()

    # ---- 4. TAMPER TEST --------------------------------------------------
    tamper_and_verify()

    failed = [r for r in results if r[0] == "FAIL"]
    print()
    print(f"=== SUMMARY: {len(results) - len(failed)}/{len(results)} passed ===")
    print(f"    workspace (temp): {WORKSPACE}")
    print(f"    audit db (temp):  {AUDIT_DB}")
    if failed:
        for status, label, detail in failed:
            print(f"  FAILED: {label} {detail}")
        return 1
    print("ALL PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
