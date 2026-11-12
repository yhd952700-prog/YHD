#!/usr/bin/env python
"""PROVE that a REAL user can hand LIUHAO real work over REAL HTTP and get a REAL result.

This is the P6 (End-to-End Execution) operational-closure proof that the
product-readiness doc could not yet make honestly: it recorded PASS while
caveating that "the full ``POST /v1/goals`` HTTP round-trip was not separately
re-run". This script closes that gap.

What makes this REAL (not an in-process shortcut)
-------------------------------------------------
* The app is booted through ``src.gateway.main.get_app()`` and served by a
  **real ``uvicorn.Server``** on a real TCP socket at ``127.0.0.1``. Incoming
  requests therefore traverse the real ASGI stack: lifespan startup, every
  middleware, router dependencies, the real ``require_human_principal``
  sovereignty gate and the real kernel layers. Nothing is invoked by calling a
  Python function directly.
* The client is a real ``httpx`` HTTP client issuing real request bytes.
* The bearer token is minted exactly the way the product intends it -- by
  ``scripts/register_human_identity.py`` followed by
  ``scripts/issue_console_token.py`` (there is no ``/v1/auth/login`` endpoint
  by design). Both run as real subprocesses against the temp stores.

What is asserted (every check must HOLD, not merely be present)
---------------------------------------------------------------
 1  the listener is genuinely reachable over HTTP (health is public);
 7  the sovereignty gate is real: no token -> 401, garbage token -> 401;
 1  ``POST /v1/goals`` accepts a real natural-language file-creation goal;
 2  polling ``GET /v1/goals/{id}`` reaches a terminal state that tells the
    truth (completed only when the work really succeeded);
 3  the artifact really exists on disk, read back independently by this
    process, with the exact expected content -- the core closure proof;
 4  ``GET /v1/employees`` reflects that this goal was carried by a real employee;
 5  the temp audit store carries events bound to the goal's correlation id;
 6  ``POST /v1/goals/{id}/stop`` on a genuinely RUNNING goal terminates it as
    ``cancelled`` -- cooperative cancellation, never a success that was staged;
 8  ``POST /v1/goals/{id}/replan`` on a FAILED goal stays honest.

Isolation
---------
Every persistence path is redirected into a fresh temp tree *before* any
``src`` import: workspace root, audit SQLite (never the frozen HC-01 evidence),
human-identity registry, the raw-auth secret store and the JWT signing key.

Exit status is non-zero if any check fails.
"""

from __future__ import annotations

import json
import os
import secrets
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

# --------------------------------------------------------------------------
# 1. Redirect EVERY persistence path to a fresh temp tree.
#
# This must happen before any ``src`` module is imported, because several
# kernels read these variables at construction time (and the identity manager
# and JWT handler are lazily-constructed singletons that would otherwise latch
# onto the production paths on first use).
# --------------------------------------------------------------------------
_TMP_ROOT = tempfile.mkdtemp(prefix="liuhao_http_closure_")
WORKSPACE = os.path.join(_TMP_ROOT, "workspace")
AUDIT_DIR = os.path.join(_TMP_ROOT, "audit")
AUDIT_DB = os.path.join(AUDIT_DIR, "audit_store.db")
HUMAN_IDENTITIES_FILE = os.path.join(_TMP_ROOT, "human_identities.json")
HUMAN_IDENTITIES_KEY = secrets.token_urlsafe(32)
AUTH_SECRETS_FILE = os.path.join(_TMP_ROOT, "auth_secrets.json")
JWT_SECRET = secrets.token_urlsafe(48)
PRINCIPAL = "http.closure.operator"

os.makedirs(WORKSPACE, exist_ok=True)
os.makedirs(AUDIT_DIR, exist_ok=True)

os.environ["LIUHAO_WORKSPACE_ROOT"] = WORKSPACE
os.environ["AUDIT_DB_PATH"] = AUDIT_DB
os.environ["LIUHAO_HUMAN_IDENTITIES_FILE"] = HUMAN_IDENTITIES_FILE
os.environ["LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY"] = HUMAN_IDENTITIES_KEY
os.environ["LIUHAO_AUTH_SECRETS_FILE"] = AUTH_SECRETS_FILE
os.environ["LIUHAO_JWT_SECRET"] = JWT_SECRET
# Ensure the simulated path is OFF so the fail-closed contract is exercised.
os.environ.pop("LIUHAO_ALLOW_SIMULATED_EXECUTION", None)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import httpx  # noqa: E402
import uvicorn  # noqa: E402

PYTHON = sys.executable

#: Long-running python_compute payload. The RestrictedPython backend kills a
#: runaway program with a child-process timeout (default 10s for a goal-driven
#: task), giving us a goal that is genuinely *still executing* long enough for a
#: human stop to arrive mid-flight.
LONG_RUNNING_GOAL = "python: x = 0\nwhile True:\n    x = x + 1\nresult = x"

RESULTS: List[Tuple[str, str, str]] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    status = "PASS" if ok else "FAIL"
    RESULTS.append((status, label, detail))
    print(f"[{status}] {label}" + (f" -- {detail}" if detail else ""))
    return bool(ok)


def http_error(response: httpx.Response) -> None:
    response.raise_for_status()


# --------------------------------------------------------------------------
# 2. Real user bootstrap: registered human -> locally minted token.
# --------------------------------------------------------------------------


def bootstrap_token() -> str:
    """Register a temp human and mint a console token the way an operator would.

    Both steps are the documented operator scripts. The integrity key MUST be
    configured: with it unset the registry refuses every stored row
    (fail-closed), so no human would ever be admitted and no token could be
    minted. That is deliberate product behaviour, not an obstacle to route
    around -- so we configure it rather than bypass it.
    """
    reg = subprocess.run(
        [
            PYTHON, os.path.join(REPO_ROOT, "scripts", "register_human_identity.py"),
            "--principal", PRINCIPAL,
            "--file", HUMAN_IDENTITIES_FILE,
            "--secrets-file", AUTH_SECRETS_FILE,
        ],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=180,
    )
    if reg.returncode != 0:
        raise RuntimeError(
            f"human registration failed (rc={reg.returncode}):\n"
            f"STDOUT:\n{reg.stdout}\nSTDERR:\n{reg.stderr}"
        )

    mint = subprocess.run(
        [
            PYTHON, os.path.join(REPO_ROOT, "scripts", "issue_console_token.py"),
            "--principal", PRINCIPAL, "--ttl", "1800",
        ],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=180,
    )
    if mint.returncode != 0:
        raise RuntimeError(
            f"token minting failed (rc={mint.returncode}):\n"
            f"STDOUT:\n{mint.stdout}\nSTDERR:\n{mint.stderr}"
        )

    # The token is echoed on its own line between two blank lines.
    for raw in mint.stdout.splitlines():
        token = raw.strip()
        if len(token) > 200 and token.startswith("eyJ") and "." in token:
            return token
    raise RuntimeError(f"could not find the token in issue_console_token output:\n{mint.stdout}")


# --------------------------------------------------------------------------
# 3. A real HTTP server, in a background thread.
# --------------------------------------------------------------------------


def free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


class HttpServer:
    """``get_app()`` served by a real uvicorn listener on a real socket."""

    def __init__(self) -> None:
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        from src.gateway.main import get_app

        config = uvicorn.Config(
            get_app(),
            host="127.0.0.1",
            port=self.port,
            loop="asyncio",
            log_level="warning",
            access_log=False,
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(
            target=self._server.run, name="p36-http-closure", daemon=True
        )

    def start(self, timeout: float = 120.0) -> None:
        self._thread.start()
        deadline = time.time() + timeout
        last_error = "never connected"
        # ``trust_env=False``: httpx otherwise honours whatever HTTP_PROXY the
        # shell exported and sends even 127.0.0.1 requests through it, which
        # rewrites the request line and turns every endpoint into a 404 SPA
        # fallback. Nothing about the product changes here -- this only stops
        # the *test client* from routing loopback traffic off-host.
        with httpx.Client(timeout=10.0, trust_env=False) as probe:
            while time.time() < deadline:
                if self._server.should_exit:
                    raise RuntimeError("uvicorn exited during startup")
                try:
                    probe.get(f"{self.base}/v1/health")
                    return
                except Exception as exc:  # noqa: BLE001 - retried until timeout
                    last_error = f"{type(exc).__name__}: {exc}"
                    time.sleep(0.4)
        raise RuntimeError(f"server did not become ready in {timeout}s: {last_error}")

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=30.0)


def read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return None


def query_audit(correlation_id: str) -> List[Dict[str, Any]]:
    """Read the temp audit SQLite directly -- independent of the app's ORM."""
    if not os.path.exists(AUDIT_DB):
        return []
    uri = "file:" + AUDIT_DB.replace("\\", "/") + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=10.0)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT event_type, outcome, correlation_id, details "
            "FROM audit_events WHERE correlation_id = ? ORDER BY seq",
            (correlation_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def audit_action_names(rows: List[Dict[str, Any]]) -> List[str]:
    names: List[str] = []
    for row in rows:
        try:
            details = json.loads(row.get("details") or "{}")
        except json.JSONDecodeError:
            details = {}
        name = details.get("action")
        if name:
            names.append(name)
    return names


def poll_terminal(
    client: httpx.Client, goal_id: str, headers: Dict[str, str], timeout: float
) -> Dict[str, Any]:
    """Poll ``GET /v1/goals/{id}`` until the goal leaves the running state."""
    deadline = time.time() + timeout
    last: Dict[str, Any] = {}
    while time.time() < deadline:
        response = client.get(f"/v1/goals/{goal_id}", headers=headers)
        if response.status_code != 200:
            last = {"__http_status__": response.status_code, "__body__": response.text}
            break
        last = response.json()
        if last.get("state") not in (None, "", "running"):
            return last
        time.sleep(0.3)
    return last


# --------------------------------------------------------------------------
# 4. The checks
# --------------------------------------------------------------------------


def check_server_reachable(client: httpx.Client) -> None:
    try:
        response = client.get("/v1/health")
    except Exception as exc:  # noqa: BLE001 - reported as FAIL
        check("server reachable over real HTTP (public health endpoint)", False,
              f"{type(exc).__name__}: {exc}")
        return
    check("server reachable over real HTTP (public health endpoint)",
          response.status_code == 200,
          f"GET /v1/health -> {response.status_code}")


def check_auth_gate(client: httpx.Client, token: str) -> Dict[str, str]:
    body = {"natural_language": "create a file named report.txt containing 'hello world'"}

    bare = client.post("/v1/goals", json=body)
    check("AUTH: POST /v1/goals with NO token is rejected with 401",
          bare.status_code == 401,
          f"status={bare.status_code} body={bare.text[:180]}")

    bogus = client.post(
        "/v1/goals", json=body, headers={"X-Liuhao-Token": "Bearer not-a-real-token"}
    )
    check("AUTH: POST /v1/goals with a garbage token is rejected with 401",
          bogus.status_code == 401,
          f"status={bogus.status_code} body={bogus.text[:180]}")

    headers = {"X-Liuhao-Token": f"Bearer {token}"}
    probe = client.post("/v1/goals", json=body, headers=headers)
    check("AUTH: the same request WITH the minted token is accepted (not 401)",
          probe.status_code == 200,
          f"status={probe.status_code}")
    # This response is also the real goal used by the later checks; caller re-uses it.
    return headers


def check_goal_round_trip(client: httpx.Client, headers: Dict[str, str]) -> Dict[str, Any]:
    goal_text = "create a file named report.txt containing 'hello world'"
    response = client.post("/v1/goals", json={"natural_language": goal_text}, headers=headers)
    if not check("GOAL-CREATE: POST /v1/goals returned 200", response.status_code == 200,
                 f"status={response.status_code} body={response.text[:300]}"):
        return {}
    payload = response.json()
    goal_id = payload.get("goal_id")
    check("GOAL-CREATE: response carries a real goal_id",
          isinstance(goal_id, str) and bool(goal_id), f"goal_id={goal_id!r}")

    detail = poll_terminal(client, goal_id, headers, timeout=90.0)
    state = detail.get("state")
    check("GOAL-POLL: GET /v1/goals/{id} reached a terminal state",
          state in ("completed", "failed", "cancelled"), f"state={state!r}")

    tasks = detail.get("tasks") or []
    failed_tasks = [t for t in tasks if t.get("status") == "failed"]
    # Guard against a vacuous pass: a goal that reports zero tasks could not
    # have completed anything, so "completed" with no tasks is not proof.
    check("GOAL-POLL: a 'completed' goal reports the tasks it actually ran",
          state != "completed" or len(tasks) >= 1,
          f"state={state!r} task_count={len(tasks)}")
    check("GOAL-POLL: a 'completed' goal does not conceal failed tasks",
          not (state == "completed" and failed_tasks),
          f"state={state!r} failed_tasks={len(failed_tasks)} "
          f"error={detail.get('error')!r}")
    if state == "completed":
        check("GOAL-POLL: the completed task is the real file_write capability",
              any(t.get("capability_id") == "file_write" for t in tasks),
              f"capabilities={sorted({t.get('capability_id') for t in tasks})} "
              f"statuses={[t.get('status') for t in tasks]}")
    return detail


def check_artifact(detail: Dict[str, Any]) -> str:
    path = os.path.join(WORKSPACE, "report.txt")
    exists = os.path.isfile(path)
    content = read_text(path)
    check("CLOSURE: the artifact really exists inside the workspace", exists,
          f"path={path}")
    check("CLOSURE: the artifact content is exactly 'hello world'", content == "hello world",
          f"content={content!r}")
    return path


def check_employee_binding(client: httpx.Client, headers: Dict[str, str]) -> None:
    response = client.get("/v1/employees", headers=headers)
    if not check("EMPLOYEE: GET /v1/employees returned 200",
                 response.status_code == 200,
                 f"status={response.status_code} body={response.text[:200]}"):
        return
    payload = response.json()
    employees = payload.get("employees") or []
    check("EMPLOYEE: at least one real employee exists", len(employees) >= 1,
          f"count={len(employees)}")
    completed = sum(
        int((e.get("stats") or {}).get("total_goals_completed") or 0) for e in employees
    )
    failed = sum(
        int((e.get("stats") or {}).get("total_goals_failed") or 0) for e in employees
    )
    check("EMPLOYEE: the executed goal is counted on a real employee",
          completed >= 1,
          f"total_goals_completed={completed} total_goals_failed={failed} "
          f"names={[e.get('name') for e in employees]}")


def check_audit_correlation(detail: Dict[str, Any]) -> None:
    correlation_id = detail.get("correlation_id")
    check("AUDIT: the goal reports a correlation id",
          isinstance(correlation_id, str) and bool(correlation_id),
          f"correlation_id={correlation_id!r}")
    if not correlation_id:
        return
    rows = query_audit(correlation_id)
    actions = audit_action_names(rows)
    check("AUDIT: the temp audit store holds events bound to that correlation id",
          len(rows) >= 1,
          f"rows={len(rows)} actions={sorted(set(actions))}")
    check("AUDIT: execution.execute is linked to the goal's correlation id",
          "execution.execute" in actions,
          f"actions={sorted(set(actions))}")


def check_stop_cancels(client: httpx.Client, headers: Dict[str, str]) -> None:
    """Start a goal that genuinely runs, then abort it through HTTP."""
    response = client.post(
        "/v1/goals",
        json={"natural_language": LONG_RUNNING_GOAL, "background": True},
        headers=headers,
    )
    if not check("STOP: background goal accepted by POST /v1/goals",
                 response.status_code == 200,
                 f"status={response.status_code} body={response.text[:300]}"):
        return
    created = response.json()
    goal_id = created.get("goal_id")
    state_now = created.get("state")
    check("STOP: the goal was still RUNNING when the POST returned",
          state_now == "running",
          f"state={state_now!r} goal_id={goal_id!r}")

    # Give the background execution time to actually enter the long-running task.
    time.sleep(2.0)
    running = client.get(f"/v1/goals/{goal_id}", headers=headers).json()
    check("STOP: the goal is genuinely running mid-execution when we stop it",
          running.get("state") == "running",
          f"state={running.get('state')!r}")

    stopped = client.post(f"/v1/goals/{goal_id}/stop", headers=headers)
    if not check("STOP: POST /v1/goals/{id}/stop returned 200",
                 stopped.status_code == 200,
                 f"status={stopped.status_code} body={stopped.text[:300]}"):
        return
    final = stopped.json()
    check("STOP: the aborted goal terminates as 'cancelled'",
          final.get("state") == "cancelled",
          f"state={final.get('state')!r} error={final.get('error')!r}")
    check("STOP: the cancelled goal does not claim success",
          final.get("state") != "completed",
          f"state={final.get('state')!r}")
    echoed = client.get(f"/v1/goals/{goal_id}", headers=headers).json()
    check("STOP: GET after stop still reports 'cancelled' (persisted honestly)",
          echoed.get("state") == "cancelled", f"state={echoed.get('state')!r}")


def check_stop_honesty_on_terminal(client: httpx.Client, headers: Dict[str, str],
                                   completed_goal_id: str) -> None:
    """Stopping a goal that already finished must not rewrite its real outcome."""
    if not completed_goal_id:
        return
    response = client.post(f"/v1/goals/{completed_goal_id}/stop", headers=headers)
    body = response.json() if response.status_code == 200 else {}
    check("STOP-HONESTY: stopping an already-completed goal keeps its real state",
          response.status_code == 200 and body.get("state") == "completed",
          f"status={response.status_code} state={body.get('state')!r}")


def check_replan_honesty(client: httpx.Client, headers: Dict[str, str]) -> None:
    """``replan`` on a failed goal must never report success it did not achieve."""
    response = client.post(
        "/v1/goals",
        json={"natural_language": "python: result = 1/0"},
        headers=headers,
    )
    if response.status_code != 200:
        check("REPLAN: a deliberately broken goal was rejected/create failed", False,
              f"status={response.status_code} body={response.text[:200]}")
        return
    failed = response.json()
    check("REPLAN: the broken goal terminated honestly as 'failed'",
          failed.get("state") == "failed",
          f"state={failed.get('state')!r} error={failed.get('error')!r}")
    # A failure that reports no tasks would be an unexplainable failure.
    broken_tasks = failed.get("tasks") or []
    check("REPLAN: the failed goal exposes the task that broke it",
          any(t.get("status") == "failed" for t in broken_tasks),
          f"task_count={len(broken_tasks)} "
          f"statuses={[t.get('status') for t in broken_tasks]}")

    replanned = client.post(f"/v1/goals/{failed['goal_id']}/replan", headers=headers)
    if not check("REPLAN: POST /v1/goals/{id}/replan answered",
                 replanned.status_code == 200,
                 f"status={replanned.status_code} body={replanned.text[:300]}"):
        return
    body = replanned.json()
    state = body.get("state")
    tasks = body.get("tasks") or []
    failed_tasks = [t for t in tasks if t.get("status") == "failed"]
    check("REPLAN: replan does not return 'completed' while tasks failed",
          not (state == "completed" and failed_tasks),
          f"state={state!r} failed_tasks={len(failed_tasks)} replan_count={body.get('replan_count')}")
    check("REPLAN: replan either genuinely succeeded or honestly stayed failed",
          state in ("completed", "failed", "cancelled"),
          f"state={state!r}")


def main() -> int:
    print("=" * 78)
    print("LIUHAO P6 -- REAL HTTP END-TO-END CLOSURE")
    print("=" * 78)
    print(f"python        : {PYTHON}")
    print(f"temp root     : {_TMP_ROOT}")
    print(f"workspace     : {WORKSPACE}")
    print(f"audit db      : {AUDIT_DB}")
    print(f"identities    : {HUMAN_IDENTITIES_FILE}")
    print(f"auth secrets  : {AUTH_SECRETS_FILE}")
    print()

    token = bootstrap_token()
    print(f"bootstrapped human principal {PRINCIPAL!r}; minted a real JWT "
          f"({len(token)} chars)\n")

    server = HttpServer()
    print(f"starting uvicorn on 127.0.0.1:{server.port} ...")
    server.start()
    print(f"server ready at {server.base}\n")

    completed_goal_id = ""
    try:
        with httpx.Client(base_url=server.base, timeout=180.0, trust_env=False) as client:
            check_server_reachable(client)
            headers = check_auth_gate(client, token)
            if not headers:
                return 1

            detail = check_goal_round_trip(client, headers)
            completed_goal_id = detail.get("goal_id") or ""
            if detail:
                check_artifact(detail)
            check_employee_binding(client, headers)
            if detail:
                check_audit_correlation(detail)

            print()
            check_stop_cancels(client, headers)
            check_stop_honesty_on_terminal(client, headers, completed_goal_id)
            print()
            check_replan_honesty(client, headers)
    finally:
        server.stop()

    failed = [r for r in RESULTS if r[0] == "FAIL"]
    print()
    print("=" * 78)
    print(f"SUMMARY: {len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    print("=" * 78)
    for status, label, detail in failed:
        print(f"  FAIL: {label}" + (f" -- {detail}" if detail else ""))
    if failed:
        print(f"\n{len(failed)} CHECK(S) FAILED -- HTTP closure NOT proven.")
        return 1
    print("\nALL CHECKS PASSED -- a real user gets a real result over real HTTP.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
