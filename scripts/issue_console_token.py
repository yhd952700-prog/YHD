"""Mint a console approval token for a verified human principal.

Run:  .venv/Scripts/python.exe scripts/issue_console_token.py --list
      .venv/Scripts/python.exe scripts/issue_console_token.py --principal <id>

Why a local script and not a login endpoint
-------------------------------------------
The gateway deliberately has no ``/v1/auth/login``. Adding one would create a
new, unauthenticated, internet-reachable credential-minting surface (brute
force, lockout, rate limiting, its own audit trail) -- a security review in its
own right, well beyond "wire the console panel". Until that review happens,
the honest position is that the token's trust anchor is **access to this
machine's filesystem**: whoever can run this script can already read the JWT
signing key, so a login form would add ceremony, not security.

C-7 is fixed: only a *registered human* may approve (since 2026-09-12)
--------------------------------------------------------------------
The predicate used to be a reverse exclusion (``kind != "service"``), which
**fails open** for any identity lacking the marker. The built-in ``system``
account carries no metadata, so it passed as a *verified human*:
``issue_grant("system", ...)`` succeeded and an armed CRITICAL action then ran
inside its window -- with the audit recording a machine as the approver.

Since Policy C-7 the predicate is a **positive allowlist**
(``is_human_identity``: ACTIVE *and* ``metadata["kind"] == "human"``), so
``system`` is refused. **The consequence is that a human must be registered
first** -- see ``scripts/register_human_identity.py``. If nobody is registered
this script says so and exits non-zero rather than offering a machine identity.

What the guard is for
---------------------
``_validated_principal`` in ``src/kernels/_sovereignty`` refuses a principal
that is unknown, inactive, or not a registered human (OD-010). This script
checks the same things up front so the operator is told *before* pasting a
token that will 400 on first use -- the failure would otherwise surface as an
opaque ``unknown principal for sovereignty grant``.
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import time

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

DEFAULT_TTL_SECONDS = 3600


def _eligible_principals() -> list:
    """Registered humans -- exactly what the gate accepts since Policy C-7.

    Delegates to ``is_human_identity`` rather than restating the predicate, so
    the listing can never drift from the kernel's actual decision.
    """
    from src.kernels.identity import get_identity_manager, is_human_identity

    mgr = get_identity_manager()
    return [ident for ident in mgr.list_identities() if is_human_identity(ident)]


def _resolve_principal(raw: str):
    """Resolve an id or a principal string to an eligible identity, or explain why not."""
    from src.kernels.identity import IdentityStatus, get_identity_manager

    mgr = get_identity_manager()
    ident = mgr.get_identity(raw) or mgr.get_identity_by_principal(raw)
    if ident is None:
        return None, (
            f"no identity for {raw!r}. Run with --list to see the known principals."
        )
    if ident.status is not IdentityStatus.ACTIVE:
        return None, (
            f"identity {ident.id!r} is {ident.status.value}, not active -- a "
            "sovereignty grant would be refused (OD-010)."
        )
    from src.kernels.identity import is_human_identity
    if not is_human_identity(ident):
        kind = (ident.metadata or {}).get("kind") if isinstance(ident.metadata, dict) else None
        what = "a service account" if kind == "service" else (
            f"not registered as a human (metadata kind={kind!r})"
        )
        return None, (
            f"identity {ident.id!r} is {what}. Since Policy C-7 only a "
            "registered human may hold sovereignty -- that is the whole point "
            "of the gate (OD-010), so this token could approve nothing. "
            "Register a human with scripts/register_human_identity.py."
        )
    return ident, None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--principal",
        help="human identity id or principal string to mint a token for",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="list the principals that are allowed to approve, then exit",
    )
    parser.add_argument(
        "--ttl",
        type=int,
        default=DEFAULT_TTL_SECONDS,
        help=f"token lifetime in seconds (default {DEFAULT_TTL_SECONDS})",
    )
    args = parser.parse_args()

    if args.list:
        eligible = _eligible_principals()
        if not eligible:
            print("No identity may currently approve: no registered human exists.")
            print()
            print("Since Policy C-7 the gate is a positive allowlist, so an")
            print("unmarked or machine identity can no longer approve. Register")
            print("someone first:")
            print()
            print("  python scripts/register_human_identity.py --principal <name>")
            print()
            print("(This is fail-closed by design, not a bug -- but the approval")
            print("channel has no subject until a human is registered.)")
            return 1
        print("Principals that may hold sovereignty (registered, ACTIVE humans):")
        for ident in eligible:
            name = (ident.metadata or {}).get("display_name") if isinstance(
                ident.metadata, dict
            ) else None
            suffix = f"  name={name}" if name else ""
            print(f"  {ident.id}  principal={ident.principal}  "
                  f"scope={ident.scope.value}{suffix}")
        return 0

    if not args.principal:
        eligible = _eligible_principals()
        hint = eligible[0].id if eligible else "<id>"
        print("error: --principal is required (or use --list).", file=sys.stderr)
        print(f"example: {pathlib.Path(__file__).name} --principal {hint}", file=sys.stderr)
        return 2

    ident, problem = _resolve_principal(args.principal)
    if ident is None:
        print(f"error: {problem}", file=sys.stderr)
        return 2

    from src.security.jwt_handler import create_access_token

    token, payload = create_access_token(ident.id, ttl=args.ttl)

    print("Console approval token")
    print("=" * 60)
    print(f"subject     : {payload.sub}")
    print(f"principal   : {ident.principal}")
    print(f"scope       : {ident.scope.value}")
    print(f"expires_at  : {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(payload.exp))}"
          f"  ({args.ttl}s from now)")
    print(f"jti         : {payload.jti}")
    print()
    print("Paste this into the console's 审批中心 panel (it is stored in")
    print("sessionStorage only, so closing the tab discards it):")
    print()
    print(token)
    print()
    print("Revoke it early with src.security.jwt_handler.get_jwt_handler().revoke_token_by_jti(jti).")
    print()
    print("Scope of this token: it names *who is approving*. It does not itself")
    print("unblock a gated action -- POST /v1/policy/approvals records the")
    print("authorization, and execution stays in-process inside grant_window().")
    return 0


if __name__ == "__main__":
    sys.exit(main())
