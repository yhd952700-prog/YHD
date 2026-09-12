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

The ``system`` identity (measured 2026-09-12, open item C-7)
-----------------------------------------------------------
``_is_verified_human`` accepts any ACTIVE identity whose metadata kind is not
``"service"``. The built-in ``system`` account carries **no metadata**, so it
passes as a *verified human*: ``issue_grant("system", ...)`` succeeds and an
armed CRITICAL action then runs inside its window. Measured end to end --
``actor {"type":"human","principal":"system"}`` -> ``allowed=True,
rule=human_sovereignty:allow``.

That does **not** grant capability beyond local filesystem access (whoever can
mint this token can also clear the enforcement switch outright), but it does
break the *accountability* the approval channel exists for: the audit would
record ``system`` as the approver instead of a person. This script therefore
warns instead of silently handing you a machine identity to approve with; it
does not refuse, because tightening the predicate is a security-semantics
decision (C-7), not a script's call to make.

What the guard is for
---------------------
``_validated_principal`` in ``src/kernels/_sovereignty`` refuses a principal
that is unknown, inactive, or a *service* identity (OD-010). This script checks
the same three things up front so the operator is told *before* pasting a token
that will 400 on first use -- the failure would otherwise surface as an opaque
``unknown principal for sovereignty grant``.
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import time

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

DEFAULT_TTL_SECONDS = 3600


def _is_marked_human(ident) -> bool:
    """True iff the identity carries the explicit ``kind="human"`` marker."""
    metadata = ident.metadata if isinstance(ident.metadata, dict) else {}
    return metadata.get("kind") == "human"


def _eligible_principals() -> list:
    """ACTIVE, non-service identities -- i.e. what the gate currently accepts.

    Note this is *not* "registered humans": the gate's predicate is a deny-list
    (``kind != "service"``), so an unmarked machine identity like the built-in
    ``system`` account is included. Kept faithful to the gate so the listing
    never claims to be stricter than reality; ``_is_marked_human`` is what
    callers use to tell the operator which entries are actually people.
    """
    from src.kernels.identity import IdentityStatus, get_identity_manager

    mgr = get_identity_manager()
    eligible = []
    for ident in mgr.list_identities():
        metadata = ident.metadata if isinstance(ident.metadata, dict) else {}
        if ident.status is IdentityStatus.ACTIVE and metadata.get("kind") != "service":
            eligible.append(ident)
    return eligible


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
    metadata = ident.metadata if isinstance(ident.metadata, dict) else {}
    if metadata.get("kind") == "service":
        return None, (
            f"identity {ident.id!r} is a service account. A service identity may "
            "never hold human sovereignty -- that is the whole point of the "
            "gate (OD-010), so this token could approve nothing."
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
            print("No identity may currently approve: none is ACTIVE and non-service.")
            print("The approval channel has no eligible human -- this is a real")
            print("finding, not an empty list to paper over.")
            return 1
        humans = [i for i in eligible if _is_marked_human(i)]
        print("Identities the approval gate currently ACCEPTS (ACTIVE, non-service):")
        for ident in eligible:
            mark = "human" if _is_marked_human(ident) else "NOT a registered human"
            print(f"  {ident.id}  principal={ident.principal}  "
                  f"scope={ident.scope.value}  [{mark}]")
        if not humans:
            print()
            print("WARNING: none of these is registered as a human (no identity carries")
            print("metadata.kind == 'human'). The gate's predicate is a deny-list, so the")
            print("account(s) above pass anyway -- but an approval made with one is")
            print("recorded against a machine identity, which is exactly what OD-010's")
            print("'verified human' requirement is meant to prevent. Open item C-7.")
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
    if not _is_marked_human(ident):
        print()
        print("WARNING: this identity is NOT registered as a human (no")
        print("metadata.kind == 'human'). The approval gate accepts it anyway because")
        print("its predicate is a deny-list, so anything you approve will be recorded")
        print("against a machine identity rather than a person -- see open item C-7.")
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
