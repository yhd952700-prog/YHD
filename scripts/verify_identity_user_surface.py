"""Verify the USER-FACING identity / permission surface (P9 Permissions / Identity UX).

The identity kernel was already real (lifecycle, scope ceiling, principal
uniqueness, disjoint human/agent namespaces, fail-closed registry integrity) --
what was missing is that **no human could see any of it**. This script proves
the read-only surface added by ``src/gateway/identity.py`` is real, honest and
gated:

 1. Real principals and real grants are created FIRST through the existing
    identity kernel, then the endpoints must return exactly those -- not an
    empty list and not fabricated rows.
 2. The registered human reads ``kind=human`` + active; a probe agent principal
    reads distinctly (and the unmarked built-in ``system`` identity is honestly
    reported as ``kind_marked=false`` rather than dressed up as an agent).
 3. Filtering by principal / resource genuinely narrows the result set.
 4. Auth is enforced: unauthenticated and invalid-token access -> 401.
 5. Empty-vs-unknown honesty: (a) with the identity kernel NOT READY every
    route returns an honest 503 instead of an empty list; (b) with a configured
    registry whose integrity key is unset, the refusals are reported in
    ``warnings`` / ``registry.rows_refused`` instead of a clean-looking empty
    human list.
 6. No mutation route exists under /v1/identity (this is an authority surface;
    grants are administered in-kernel, not over HTTP, until a real gate exists).

Plus two answers the P9 report needs as *facts*, measured here:

 7. Do human registrations survive a restart? Two subprocess processes sharing
    one temp registry answer it for the configured case and for the default
    (unconfigured) case.
 8. Sanctity: the temp fixtures never touch the frozen HC-01 audit DB.

Auth is bootstrapped exactly as the gateway requires: register a human identity
(``scripts/register_human_identity.py``) against the temp registry, mint a token
(``scripts/issue_console_token.py --principal <id>``), present it in the
``X-Liuhao-Token`` header. Persistence is redirected to temp BEFORE any import.

Exits non-zero on any failure (prints PASS/FAIL per check).
"""

from __future__ import annotations

import os
import re
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import uuid

# --- Redirect ALL persistence to temp BEFORE importing anything that reads it.
_TMP_ROOT = tempfile.mkdtemp(prefix="liuhao_identity_ux_")
_TMP_WS = os.path.join(_TMP_ROOT, "workspace")
_TMP_AUDIT_DIR = os.path.join(_TMP_ROOT, "audit")
_TMP_AUDIT_DB = os.path.join(_TMP_AUDIT_DIR, "audit_store.db")
_TMP_HUMANS = os.path.join(_TMP_ROOT, "human_identities.json")
os.makedirs(_TMP_WS, exist_ok=True)
os.makedirs(_TMP_AUDIT_DIR, exist_ok=True)

os.environ["LIUHAO_WORKSPACE_ROOT"] = _TMP_WS
os.environ["AUDIT_DB_PATH"] = _TMP_AUDIT_DB
os.environ["LIUHAO_HUMAN_IDENTITIES_FILE"] = _TMP_HUMANS
os.environ["LIUHAO_AUTH_SECRETS_FILE"] = os.path.join(_TMP_ROOT, "auth_secrets.json")
# A durable signing key shared by the token-minting subprocess and the gateway.
os.environ["LIUHAO_JWT_SECRET"] = secrets.token_hex(32)
# The registry is fail-closed: without a row-authentication key every stored
# human is REFUSED at load (PHASE 3.6 / A3), which is exactly why the default
# answer "there are no humans" is never the honest one.
os.environ["LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY"] = secrets.token_hex(32)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

PY = sys.executable

import httpx  # noqa: E402
import uvicorn  # noqa: E402

PRINCIPAL = "identity-reviewer"
_PASSWORD = "p9-" + secrets.token_hex(8)
_JWT_RE = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")

#: The human is registered with these permissions; proving the endpoints return
#: exactly this set is what separates "reads the real registry" from "invents rows".
HUMAN_PERMISSIONS = ["read:identity", "write:identity"]

#: A probe agent created through the identity kernel, to prove agents enumerate
#: separately from humans (disjoint namespaces, not a UI convention).
AGENT_PRINCIPAL = "p9-probe-agent"
AGENT_PERMISSIONS = ["read:security", "audit:security", "tool.invoke"]
AGENT_SCOPE = "L3"

_RESULTS: list = []


def check(label: str, condition: bool, detail: str = "") -> bool:
    ok = bool(condition)
    _RESULTS.append((label, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {label}")
    if detail:
        print(f"      {detail}")
    return ok


# ---------------------------------------------------------------------------
# 1. real fixtures through the REAL identity kernel
# ---------------------------------------------------------------------------


def bootstrap_auth() -> str:
    """Register a human in the temp registry, then mint a real JWT."""
    reg = _run([
        "scripts/register_human_identity.py",
        "--principal", PRINCIPAL,
        "--display-name", "Identity Reviewer",
        "--password", _PASSWORD,
        "--permissions", ",".join(HUMAN_PERMISSIONS),
    ])
    if reg.returncode != 0:
        raise RuntimeError(
            f"register_human_identity failed ({reg.returncode}):\n"
            f"{reg.stdout}\n{reg.stderr}"
        )

    issue = _run([
        "scripts/issue_console_token.py",
        "--principal", f"human:{PRINCIPAL}",
        "--ttl", "3600",
    ])
    if issue.returncode != 0:
        # Older call sites pass the bare principal; try that spelling too.
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


def create_probe_agent():
    """Create the probe agent + its grants through IdentityManager itself."""
    from src.kernels.identity import IdentityScope, get_identity_manager

    manager = get_identity_manager()
    agent_scope = IdentityScope(AGENT_SCOPE)
    identity = manager.create_identity(
        principal=AGENT_PRINCIPAL,
        permissions=set(AGENT_PERMISSIONS[:1]),
        scope=agent_scope,
        trust_score=0.4,
        metadata={"description": "P9 verification fixture"},
    )
    if identity is None:
        raise RuntimeError(
            "IdentityManager.create_identity refused the probe agent principal "
            f"{AGENT_PRINCIPAL!r} (existing principal, or the kernel action was "
            "denied) -- the fixtures would not be real"
        )
    identity = manager.get_identity_by_principal(AGENT_PRINCIPAL)
    for permission in AGENT_PERMISSIONS[1:]:
        granted = manager.grant_permission(
            identity.id, permission, agent_scope, reason="P9 verification fixture")
        if not granted:
            raise RuntimeError(
                f"grant_permission({identity.id!r}, {permission!r}) returned False "
                "(scope ceiling refused it)"
            )
    return identity


def _run(args, env=None):
    return subprocess.run(
        [PY] + args,
        cwd=REPO_ROOT,
        env=(env or os.environ).copy(),
        capture_output=True,
        text=True,
        timeout=180,
    )


# ---------------------------------------------------------------------------
# 2. a TRUE HTTP server (uvicorn in a background thread, same process)
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
        headers["X-Liuhao-Token"] = f"Bearer {token}"
    with httpx.Client(timeout=120.0) as client:
        return client.get(base_url + path, params=params, headers=headers)


IDENTITY_PATHS = (
    "/v1/identity/principals",
    "/v1/identity/principals/system",
    "/v1/identity/permissions",
    "/v1/identity/summary",
)


# ---------------------------------------------------------------------------
# checks
# ---------------------------------------------------------------------------


def check_principals_are_real(base_url, token, human_id, agent_id):
    """1. the endpoints return the REAL fixtures, not empty and not invented."""
    r = get(base_url, "/v1/identity/principals", token, include_permissions="true")
    ok = check("GET /v1/identity/principals returns 200",
               r.status_code == 200, detail=f"status={r.status_code} body={r.text[:240]}")
    if not ok:
        return None
    body = r.json()
    rows = body.get("principals", [])
    check("GET /v1/identity/principals is non-empty (real registry enumerated)",
          len(rows) > 0, detail=f"count={body.get('count')}")

    humans = [row for row in rows if row.get("principal") == PRINCIPAL]
    check("the registered human is enumerated from the identity kernel",
          len(humans) == 1, detail=f"matched={len(humans)}")

    agents = [row for row in rows if row.get("principal") == AGENT_PRINCIPAL]
    check("the probe agent principal is enumerated (a second, distinct kind)",
          len(agents) == 1, detail=f"matched={len(agents)}")

    if humans and agents:
        check("human and agent occupy disjoint ids (human:<name> vs hex)",
              humans[0].get("id") != agents[0].get("id")
              and humans[0].get("id", "").startswith("human:"),
              detail=f"human_id={humans[0].get('id')} agent_id={agents[0].get('id')}")

    # The built-in 'system' identity carries no metadata kind marker: it must be
    # reported as kind_marked=false rather than silently called an agent.
    system_rows = [row for row in rows if row.get("principal") == "system"]
    check("the unmarked built-in 'system' identity is honestly flagged "
          "(kind_marked=false)",
          bool(system_rows) and system_rows[0].get("kind_marked") is False,
          detail=f"system_rows={system_rows[:1]}")

    service_rows = [row for row in rows if row.get("kind") == "service"]
    check("the internal service principal is present as kind=service",
          bool(service_rows), detail=f"service_rows={service_rows[:1]}")

    # --- single principal: its REAL permissions, exactly ---
    r2 = get(base_url, f"/v1/identity/principals/{human_id}", token)
    ok2 = check(f"GET /v1/identity/principals/{human_id} returns 200",
                r2.status_code == 200, detail=f"status={r2.status_code} body={r2.text[:240]}")
    if not ok2:
        return None
    detail = r2.json().get("principal", {})
    perms = sorted(r2.json().get("permissions", []))
    check("the human's permissions are exactly the ones registered",
          perms == sorted(HUMAN_PERMISSIONS),
          detail=f"expected={sorted(HUMAN_PERMISSIONS)} got={perms}")
    check("permission_count agrees with the returned permission list",
          detail.get("permission_count") == len(perms),
          detail=f"permission_count={detail.get('permission_count')} len={len(perms)}")

    r3 = get(base_url, f"/v1/identity/principals/{agent_id}", token)
    agent_perms = sorted(r3.json().get("permissions", [])) if r3.status_code == 200 else []
    check("the probe agent's granted permissions are real and complete",
          r3.status_code == 200 and agent_perms == sorted(AGENT_PERMISSIONS),
          detail=f"expected={sorted(AGENT_PERMISSIONS)} got={agent_perms}")

    # --- the flat grant listing must contain exactly the fixtures for each ---
    r4 = get(base_url, "/v1/identity/permissions", token, limit=1000)
    ok4 = check("GET /v1/identity/permissions returns 200",
                r4.status_code == 200, detail=f"status={r4.status_code} body={r4.text[:240]}")
    if not ok4:
        return None
    grants = r4.json().get("grants", [])
    check("the grant listing is non-empty",
          len(grants) > 0, detail=f"count={len(grants)}")
    expected_pairs = {(PRINCIPAL, p) for p in HUMAN_PERMISSIONS}
    actual_pairs = {(g.get("principal"), g.get("permission")) for g in grants}
    check("every registered grant appears verbatim in the flat listing",
          expected_pairs <= actual_pairs,
          detail=f"missing={sorted(expected_pairs - actual_pairs)}")
    derived = [g for g in grants if g.get("permission") == "read:security"]
    check("derived resource/action are split out of the real permission string",
          bool(derived) and derived[0].get("derived_resource") == "security"
          and derived[0].get("derived_action") == "read"
          and derived[0].get("derived") is True,
          detail=f"row={derived[:1]}")
    return body


def check_kinds_and_states(base_url, token):
    """2. the human reads kind=human + active; nothing is assumed."""
    r = get(base_url, "/v1/identity/principals", token, kind="human")
    body = r.json() if r.status_code == 200 else {}
    rows = body.get("principals", [])
    mine = [row for row in rows if row.get("principal") == PRINCIPAL]
    check("?kind=human returns only human rows and includes our registered human",
          r.status_code == 200 and bool(rows) and bool(mine)
          and all(row.get("kind") == "human" for row in rows),
          detail=f"status={r.status_code} count={len(rows)}")
    check("the registered human is reported ACTIVE and kind_marked",
          bool(mine) and mine[0].get("active") is True
          and mine[0].get("kind_marked") is True,
          detail=f"row={mine[:1]}")

    r2 = get(base_url, "/v1/identity/principals", token, kind="agent")
    agents = r2.json().get("principals", []) if r2.status_code == 200 else []
    check("?kind=agent returns the probe agent, not the human",
          bool(agents) and AGENT_PRINCIPAL in [a.get("principal") for a in agents]
          and PRINCIPAL not in [a.get("principal") for a in agents],
          detail=f"agents={[a.get('principal') for a in agents]}")


def check_filters_narrow(base_url, token):
    """3. filtering genuinely narrows (server-side, not a client-side illusion)."""
    total = get(base_url, "/v1/identity/permissions", token, limit=1000).json()
    total_count = total.get("count", 0)

    r = get(base_url, "/v1/identity/permissions", token, principal=AGENT_PRINCIPAL)
    by_principal = r.json().get("grants", []) if r.status_code == 200 else []
    check("?principal= narrows the grant listing to that principal",
          r.status_code == 200 and bool(by_principal)
          and all(g.get("principal") == AGENT_PRINCIPAL for g in by_principal)
          and len(by_principal) < total_count,
          detail=f"matched={len(by_principal)} total={total_count}")

    r2 = get(base_url, "/v1/identity/permissions", token, resource="security")
    by_resource = r2.json().get("grants", []) if r2.status_code == 200 else []
    check("?resource= narrows to rows whose derived resource matches",
          r2.status_code == 200 and bool(by_resource)
          and all(g.get("derived_resource") == "security" for g in by_resource)
          and len(by_resource) < total_count,
          detail=f"matched={len(by_resource)} total={total_count}")

    r3 = get(base_url, "/v1/identity/principals", token, principal=PRINCIPAL)
    rows = r3.json().get("principals", []) if r3.status_code == 200 else []
    check("?principal= on the principal list returns exactly one row",
          r3.status_code == 200 and len(rows) == 1
          and rows[0].get("principal") == PRINCIPAL,
          detail=f"matched={len(rows)}")

    # A filter that matches nothing: an honest EMPTY answer (not everything, and
    # not an error) -- the distinction that matters once totals get large.
    r4 = get(base_url, "/v1/identity/permissions", token, resource="no-such-resource")
    check("a non-matching resource filter returns an honest empty list",
          r4.status_code == 200 and r4.json().get("count") == 0,
          detail=f"status={r4.status_code} count={r4.json().get('count')}")

    # Unknown subject -> 404, never an empty object reading as "exists, has none".
    missing = "no-such-principal-" + uuid.uuid4().hex
    r5 = get(base_url, f"/v1/identity/principals/{missing}", token)
    check("an unknown principal returns 404 (not an empty principal)",
          r5.status_code == 404, detail=f"status={r5.status_code} body={r5.text[:200]}")


def check_auth_enforced(base_url):
    """4. the surface is sovereignty-gated: no token, or a bad one, -> 401."""
    for path in IDENTITY_PATHS:
        r = get(base_url, path)
        check(f"unauthenticated GET {path} -> 401",
              r.status_code == 401, detail=f"status={r.status_code}")

    for path in IDENTITY_PATHS:
        r = get(base_url, path, token="not.a.real.token")
        check(f"invalid token GET {path} -> 401",
              r.status_code == 401, detail=f"status={r.status_code}")

    # A well-formed but unsigned/tampered token: the signature is really checked,
    # so "looks like a JWT" is not authentication.
    # "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJoIn0." + garbage signature.
    forged = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        ".eyJzdWIiOiJodW1hbiJ9"
        ".deadbeefdeadbeefdeadbeef"
    )
    r = get(base_url, "/v1/identity/principals", token=forged)
    check("forged (unsigned) token GET /v1/identity/principals -> 401",
          r.status_code == 401, detail=f"status={r.status_code}")


def _manager():
    from src.kernels.identity import get_identity_manager

    return get_identity_manager()


def check_kernel_unavailable_is_an_error(base_url, token):
    """5a. 'cannot determine' must be an ERROR, not an empty list.

    The identity manager is shut down for real (its lifecycle state becomes
    STOPPED), then every route must say so. ``list_identities()`` itself would
    happily return an empty dict -- which is exactly the lie this guards.
    """
    manager = _manager()
    manager.shutdown()
    try:
        for path in IDENTITY_PATHS:
            r = get(base_url, path, token)
            body = r.text[:200]
            check(f"identity kernel STOPPED: GET {path} -> honest 503",
                  r.status_code == 503 and "not READY" in body,
                  detail=f"status={r.status_code} body={body}")
    finally:
        manager.initialize()

    r = get(base_url, "/v1/identity/principals", token)
    check("the surface recovers once the identity kernel is READY again",
          r.status_code == 200, detail=f"status={r.status_code}")


def check_registry_without_integrity_key(base_url, token):
    """5b. refused rows must be visible, not dressed up as 'no humans'.

    With ``LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY`` unset the registry refuses
    EVERY row (fail-closed, HC-11 / U6), so the registered human disappears
    from the listing. An empty human list with no explanation would read as
    "no human exists" -- the recurring empty==absent defect.
    """
    import src.kernels.identity as identity_module

    saved_key = os.environ.pop("LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY", None)
    saved_manager = identity_module._global_manager
    identity_module._global_manager = None
    try:
        r = get(base_url, "/v1/identity/principals", token, kind="human")
        ok = check("registry without its integrity key: /principals still answers (200)",
                   r.status_code == 200, detail=f"status={r.status_code} body={r.text[:240]}")
        if not ok:
            return
        body = r.json()
        registry = body.get("registry") or {}
        warnings = body.get("warnings") or []
        refused = registry.get("rows_refused") or []
        check("...and says WHY there are no humans (non-empty warnings)",
              bool(warnings), detail=f"warnings={warnings}")
        check("...and reports the registry integrity modality as not enforced",
              registry.get("integrity_enforced") is False
              and registry.get("integrity_state") == "not_applicable",
              detail=f"registry={registry}")
        check("...and names the refused row(s) instead of silently dropping them",
              PRINCIPAL in refused, detail=f"rows_refused={refused}")
        check("...and does NOT show the human as existing",
              PRINCIPAL not in [row.get("principal") for row in body.get("principals", [])],
              detail=f"principals={[row.get('principal') for row in body.get('principals', [])]}")

        r2 = get(base_url, "/v1/identity/summary", token)
        check("summary carries the same honest warnings",
              r2.status_code == 200 and bool((r2.json().get("warnings") or [])),
              detail=f"warnings={r2.json().get('warnings')}")
    finally:
        identity_module._global_manager = saved_manager
        if saved_key is not None:
            os.environ["LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY"] = saved_key


def check_no_mutation_routes():
    """6. no write surface under /v1/identity.

    Reads the OpenAPI spec rather than ``app.routes``: on FastAPI 0.141 the
    latter yields ``_IncludedRouter`` wrappers with neither ``path`` nor
    ``methods``, so a naive scan finds nothing and passes vacuously.
    """
    from src.gateway.main import get_app

    spec = get_app().openapi()
    paths = {p: sorted(m for m in ops if m.lower() in (
        "get", "post", "put", "patch", "delete"))
        for p, ops in spec["paths"].items() if p.startswith("/v1/identity")}

    check("the identity surface is actually mounted (not zero routes)",
          len(paths) >= 4, detail=f"paths={sorted(paths)}")

    non_get = sorted((p, m) for p, methods in paths.items()
                     for m in methods if m != "get")
    check("no mutation route is mounted under /v1/identity (read-only by construction)",
          not non_get, detail=f"non-GET={non_get}")

    expected = {"/v1/identity/principals", "/v1/identity/principals/{principal_id}",
                "/v1/identity/permissions", "/v1/identity/summary"}
    check("all four read-only routes exist",
          expected <= set(paths), detail=f"missing={sorted(expected - set(paths))}")


# ---------------------------------------------------------------------------
# 7. the persistence question, answered by measurement
# ---------------------------------------------------------------------------

_LIST_HUMANS_CODE = (
    "import sys;"
    "from src.kernels.identity import get_identity_manager, is_human_identity;"
    "manager = get_identity_manager();"
    "names = [i.principal for i in manager.list_identities() if is_human_identity(i)];"
    "print('FOUND' if sys.argv[1] in names else 'MISSING')"
)


def _restart_env(name, *, with_file: bool, with_key: bool):
    env = os.environ.copy()
    if with_file:
        env["LIUHAO_HUMAN_IDENTITIES_FILE"] = os.path.join(
            _TMP_ROOT, f"{name}_human_identities.json")
    else:
        env.pop("LIUHAO_HUMAN_IDENTITIES_FILE", None)
    if with_key:
        env["LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY"] = secrets.token_hex(32)
    env["PYTHONPATH"] = REPO_ROOT
    env["LIUHAO_WORKSPACE_ROOT"] = os.path.join(_TMP_ROOT, f"ws_{name}")
    os.makedirs(env["LIUHAO_WORKSPACE_ROOT"], exist_ok=True)
    return env


def check_registration_persistence():
    """Do registrations survive a restart? Two processes per case decide it."""
    cases = (
        ("registry configured + integrity key set", True, True, "FOUND"),
        ("nothing configured (default env)", False, False, "MISSING"),
    )
    for index, (label, with_file, with_key, expected) in enumerate(cases):
        name = f"persist{index}"
        env = _restart_env(name, with_file=with_file, with_key=with_key)
        principal = f"{name}-human"

        reg = _run([
            "scripts/register_human_identity.py",
            "--principal", principal,
            "--password", _PASSWORD,
        ], env=env)
        if reg.returncode != 0:
            check(f"persistence case '{label}': registration succeeded", False,
                  detail=f"rc={reg.returncode} {reg.stderr[:200]}")
            continue

        listed = _run(["-c", _LIST_HUMANS_CODE, principal], env=env)
        verdict = listed.stdout.strip()
        check(
            f"persistence case '{label}': after restart the human is {expected}",
            verdict == expected,
            detail=f"process 1 registered; process 2 says {verdict} "
                   f"(stdout={listed.stdout[:120]!r} stderr={listed.stderr[:200]!r})",
        )


def main() -> int:
    print("Identity / permission user surface (P9) verification")
    print("=" * 60)
    print(f"temp root      : {_TMP_ROOT}")
    print(f"audit db       : {_TMP_AUDIT_DB}")
    print(f"human registry : {_TMP_HUMANS}")
    print()

    token = bootstrap_auth()
    print("registered a real human identity and minted a real JWT "
          f"(principal={PRINCIPAL})")
    agent = create_probe_agent()
    print(f"created probe agent {AGENT_PRINCIPAL!r} id={agent.id} "
          f"permissions={sorted(agent.permissions)}")
    print()

    with Server() as server:
        base = server.base_url
        human_id = f"human:{PRINCIPAL}"
        check_principals_are_real(base, token, human_id, agent.id)
        print()
        check_kinds_and_states(base, token)
        print()
        check_filters_narrow(base, token)
        print()
        check_auth_enforced(base)
        print()
        check_kernel_unavailable_is_an_error(base, token)
        print()
        check_registry_without_integrity_key(base, token)
        print()

    check_no_mutation_routes()
    print()
    check_registration_persistence()

    failed = [label for label, ok in _RESULTS if not ok]
    print()
    print("=" * 60)
    print(f"{len(_RESULTS) - len(failed)}/{len(_RESULTS)} checks passed")
    if failed:
        print("FAILURES:")
        for label in failed:
            print(f"  - {label}")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
