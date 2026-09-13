#!/usr/bin/env python
"""Register a human identity that may hold sovereignty (OD-010 / Policy C-7).

Why this exists
---------------
Since Policy C-7 the question "is this actor a verified human?" is answered by
a **positive allowlist**: ``metadata["kind"] == "human"``. Merely not being a
service is no longer enough -- the built-in ``system`` account used to slip
through that reverse exclusion and could be recorded as the approver of a
CRITICAL action.

That fix has a consequence: **a human must actually be registered**, or the
approval channel has nobody who may approve. ``IdentityManager`` keeps
identities in memory only, so registrations are persisted through the store
layer in ``src/kernels/identity/_persistence.py``:

    JSON seed file   LIUHAO_HUMAN_IDENTITIES_FILE=/path/to/human_identities.json
    SQLite database  LIUHAO_HUMAN_IDENTITIES_DB=/path/to/human_identities.sqlite3

The kernel reads those variables in ``IdentityManager.__init__``. Neither
configured means **zero registered humans** -- deliberately fail-closed rather
than silently falling back to a machine identity.

Which backend to use
--------------------
``file`` is the default and keeps the historical behaviour. ``sqlite`` is the
better choice once more than one process can register, or when an operator
wants a durable registry that is queryable and cannot be clobbered by two
concurrent read-modify-write cycles. Pass ``--backend sqlite`` (optionally with
``--db``) to use it.

What it verifies
----------------
Writing the record is not the point; *being able to approve afterwards* is.
After every write this script boots a **fresh** ``IdentityManager`` against the
same store and asserts the new principal passes ``is_human_identity``. If that
round trip fails the script exits non-zero rather than leaving behind a record
that looks like a registration but is not.

Trust boundary
--------------
Same anchor as ``scripts/issue_console_token.py``: **access to this machine's
filesystem**. Anyone who can run this can already edit the store, so this is
bookkeeping and audit trail, not an access control.

Usage
-----
    python scripts/register_human_identity.py --list
    python scripts/register_human_identity.py --principal xin.hongda \
        --display-name "辛宏达"
    python scripts/register_human_identity.py --backend sqlite \
        --db data/human_identities.sqlite3 --principal xin.hongda
    python scripts/register_human_identity.py --revoke xin.hongda
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.kernels.identity import (  # noqa: E402
    HUMAN_IDENTITIES_BACKEND_ENV,
    HUMAN_IDENTITIES_DB_ENV,
    HUMAN_IDENTITIES_FILE_ENV,
    HUMAN_KIND,
    IdentityManager,
    METADATA_KIND_KEY,
    is_human_identity,
)
from src.kernels.identity._persistence import (  # noqa: E402
    BACKEND_SQLITE,
    FIELD_DISPLAY_NAME,
    FIELD_PERMISSIONS,
    FIELD_PRINCIPAL,
    FIELD_REGISTERED_AT,
    FIELD_SCOPE,
    FIELD_SOURCE,
    resolve_human_identity_store,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FILE = REPO_ROOT / "config" / "human_identities.json"


def build_store(args: argparse.Namespace):
    """Resolve the store from CLI flags first, environment second."""
    if args.file:
        os.environ[HUMAN_IDENTITIES_FILE_ENV] = str(Path(args.file).expanduser())
    if args.backend:
        os.environ[HUMAN_IDENTITIES_BACKEND_ENV] = args.backend
    if args.db:
        os.environ[HUMAN_IDENTITIES_DB_ENV] = str(Path(args.db).expanduser())
    return resolve_human_identity_store()


def env_hint(args: argparse.Namespace) -> str:
    """The variable the *service* must set for this store to be read at boot."""
    if os.environ.get(HUMAN_IDENTITIES_DB_ENV):
        return f"{HUMAN_IDENTITIES_DB_ENV}={os.environ[HUMAN_IDENTITIES_DB_ENV]}"
    if os.environ.get(HUMAN_IDENTITIES_FILE_ENV):
        return f"{HUMAN_IDENTITIES_FILE_ENV}={os.environ[HUMAN_IDENTITIES_FILE_ENV]}"
    return f"({HUMAN_IDENTITIES_FILE_ENV} unset -- kernel will load nobody)"


def verify_round_trip(store, principal: str) -> bool:
    """Boot a fresh manager against the same store and ask the real question."""
    previous: Dict[str, Optional[str]] = {
        key: os.environ.get(key)
        for key in (
            HUMAN_IDENTITIES_BACKEND_ENV,
            HUMAN_IDENTITIES_DB_ENV,
            HUMAN_IDENTITIES_FILE_ENV,
        )
    }
    try:
        if store.backend_name == BACKEND_SQLITE:
            os.environ[HUMAN_IDENTITIES_BACKEND_ENV] = BACKEND_SQLITE
            os.environ[HUMAN_IDENTITIES_DB_ENV] = store.location
            os.environ.pop(HUMAN_IDENTITIES_FILE_ENV, None)
        else:
            os.environ.pop(HUMAN_IDENTITIES_BACKEND_ENV, None)
            os.environ.pop(HUMAN_IDENTITIES_DB_ENV, None)
            os.environ[HUMAN_IDENTITIES_FILE_ENV] = store.location
        manager = IdentityManager()
        return is_human_identity(manager.get_identity_by_principal(principal))
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def verify_round_trip_many(store, principals: List[Any]) -> set:
    previous: Dict[str, Optional[str]] = {
        key: os.environ.get(key)
        for key in (
            HUMAN_IDENTITIES_BACKEND_ENV,
            HUMAN_IDENTITIES_DB_ENV,
            HUMAN_IDENTITIES_FILE_ENV,
        )
    }
    try:
        if store.backend_name == BACKEND_SQLITE:
            os.environ[HUMAN_IDENTITIES_BACKEND_ENV] = BACKEND_SQLITE
            os.environ[HUMAN_IDENTITIES_DB_ENV] = store.location
            os.environ.pop(HUMAN_IDENTITIES_FILE_ENV, None)
        else:
            os.environ.pop(HUMAN_IDENTITIES_BACKEND_ENV, None)
            os.environ.pop(HUMAN_IDENTITIES_DB_ENV, None)
            os.environ[HUMAN_IDENTITIES_FILE_ENV] = store.location
        manager = IdentityManager()
        return {
            str(p)
            for p in principals
            if p and is_human_identity(manager.get_identity_by_principal(str(p)))
        }
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def cmd_list(store, args: argparse.Namespace) -> int:
    humans: List[Dict[str, Any]] = store.load_all()
    print(f"Registered humans (store: {store.backend_name} @ {store.location})")
    print("=" * 66)
    if not humans:
        print("  (none)")
        print()
        print("Nobody can approve HIGH/CRITICAL actions right now. That is the")
        print("fail-closed default, not a bug -- but the approval channel has no")
        print("subject until someone is registered:")
        print()
        print("  python scripts/register_human_identity.py --principal <name>")
        return 1

    ok = verify_round_trip_many(store, [h.get(FIELD_PRINCIPAL) for h in humans])
    for entry in humans:
        name = entry.get(FIELD_DISPLAY_NAME) or "-"
        perms = ", ".join(sorted(entry.get(FIELD_PERMISSIONS) or [])) or "-"
        registered = entry.get(FIELD_REGISTERED_AT) or "-"
        flag = "OK " if entry.get(FIELD_PRINCIPAL) in ok else "BAD"
        print(f"  [{flag}] {entry.get(FIELD_PRINCIPAL)}")
        print(f"         display_name={name}  permissions={perms}")
        print(f"         registered_at={registered}")
    print()
    print(f"Kernel reads this store when: {env_hint(args)}")
    return 0 if len(ok) == len(humans) else 1


def cmd_register(store, args: argparse.Namespace, principal: str,
                 display_name: str | None, permissions: List[str]) -> int:
    principal = principal.strip()
    if not principal:
        print("--principal is required", file=sys.stderr)
        return 2
    if principal in ("system", "liuhao-internal-service"):
        print(
            f"refusing to register the built-in machine identity {principal!r}.\n"
            "It is a machine -- registering it as human would re-open C-7.",
            file=sys.stderr,
        )
        return 2

    # Reading an existing registry must not emit the kernel's "no such file
    # exists -- no human identity can approve" warning. That warning exists to
    # alert an operator of a *running service*; during registration the record
    # is about to be created by this very command, so it is pure noise.
    identity_logger = logging.getLogger("liuhao.kernel.identity")
    previous_level = identity_logger.level
    identity_logger.setLevel(logging.ERROR)
    try:
        existing = {h.get(FIELD_PRINCIPAL): h for h in store.load_all()}
    finally:
        identity_logger.setLevel(previous_level)
    previous = existing.get(principal) or {}
    action = "updated" if previous else "registered"

    wrote = store.upsert(
        {
            FIELD_PRINCIPAL: principal,
            FIELD_DISPLAY_NAME: display_name or previous.get(FIELD_DISPLAY_NAME),
            FIELD_PERMISSIONS: sorted(set(permissions)),
            FIELD_SCOPE: previous.get(FIELD_SCOPE) or "L0",
            # "" lets the store keep the original instant on an update and
            # stamp "now" through the kernel on a first registration.
            FIELD_REGISTERED_AT: previous.get(FIELD_REGISTERED_AT) or "",
            FIELD_SOURCE: "register_human_identity.py",
        }
    )
    if not wrote:
        print(
            f"could not write to the store ({store.backend_name} @ "
            f"{store.location}) -- nothing registered.",
            file=sys.stderr,
        )
        return 1

    print(f"{action}: {principal}")
    print(f"store     : {store.backend_name} @ {store.location}")
    print(f"marker    : metadata[{METADATA_KIND_KEY!r}] = {HUMAN_KIND!r}")

    if not verify_round_trip(store, principal):
        print()
        print("!! VERIFICATION FAILED: a freshly booted kernel does not treat")
        print("   this principal as a human. The record was written but the")
        print(f"   channel is not live. Check that {env_hint(args)} is set in")
        print("   the service that needs to approve.")
        return 1

    print("verified  : a fresh kernel loads this principal as a human.")
    print()
    print("To make it live, set this in the service environment:")
    print(f"  {env_hint(args)}")
    print()
    print("Undo with:")
    print(f"  python scripts/register_human_identity.py --revoke {principal}")
    print()
    print("NOTE: this records WHO may approve. It does not itself approve")
    print("anything -- issue a grant per action with POST /v1/policy/approvals,")
    print("or scripts/issue_console_token.py for the console token.")
    return 0


def cmd_revoke(store, principal: str) -> int:
    if not store.remove(principal):
        print(f"{principal!r} was not registered in "
              f"{store.backend_name} @ {store.location} -- nothing to revoke.")
        return 1
    print(f"revoked: {principal}")
    print(f"store  : {store.backend_name} @ {store.location}")
    print()
    print("Revoking here stops FUTURE approvals by this principal. It does not")
    print("recall grants already issued -- revoke those with")
    print("DELETE /v1/policy/approvals/{grant_id}.")
    return 0


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Register human identities that may hold sovereignty.",
    )
    parser.add_argument("--list", action="store_true",
                        help="show registered humans and whether they verify")
    parser.add_argument("--principal", help="principal to register, e.g. xin.hongda")
    parser.add_argument("--display-name", dest="display_name",
                        help="human-friendly name shown in tooling")
    parser.add_argument("--permissions", default="",
                        help="comma-separated permissions (optional)")
    parser.add_argument("--revoke", help="remove a registered principal")
    parser.add_argument("--file", dest="file",
                        help=f"JSON seed file (default: ${HUMAN_IDENTITIES_FILE_ENV} "
                             f"or {DEFAULT_FILE})")
    parser.add_argument("--db", dest="db",
                        help=f"SQLite database (sets ${HUMAN_IDENTITIES_DB_ENV}; "
                             "implies --backend sqlite)")
    parser.add_argument("--backend", dest="backend", choices=["file", "sqlite"],
                        help="which store to write to (default: inferred from env)")
    args = parser.parse_args(argv)

    store = build_store(args)

    if args.list:
        return cmd_list(store, args)
    if args.revoke:
        return cmd_revoke(store, args.revoke.strip())
    if args.principal:
        perms = [p.strip() for p in args.permissions.split(",") if p.strip()]
        return cmd_register(store, args, args.principal, args.display_name, perms)

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
