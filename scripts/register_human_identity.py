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

Two different things, two different stores
------------------------------------------
Registering an identity records **who may hold sovereignty**. It does not give
anyone a way to sign in to the console -- that needs a credential, which lives
in its own store (``LIUHAO_AUTH_SECRETS_FILE``, see ``src/gateway/auth.py``).
An identity without a credential can approve nothing from the UI, because it
cannot log in to get a token.

``--password`` sets both halves in one command, and then *proves* the result by
performing a real login.

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

When a credential is set it goes further and runs the actual login path
(``src.gateway.auth.authenticate``), so "you can sign in" is demonstrated
rather than assumed. That call writes one audit event, which is intentional:
a login is a security-relevant fact.

Secrets are never echoed
------------------------
Output reports only *that* a credential exists and its KDF parameters. Salt and
hash values are never printed, logged, or placed in an error message.

Trust boundary
--------------
Same anchor as ``scripts/issue_console_token.py``: **access to this machine's
filesystem**. Anyone who can run this can already edit both stores, so this is
bookkeeping and audit trail, not an access control.

Usage
-----
    python scripts/register_human_identity.py --list
    python scripts/register_human_identity.py --principal xin.hongda \
        --display-name "辛宏达"
    python scripts/register_human_identity.py --principal xin.hongda \
        --password-stdin
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

from src.gateway.auth import (  # noqa: E402
    AUTH_SECRETS_FILE_ENV,
    DEFAULT_SECRETS_PATH,
    authenticate,
    describe_credential,
    resolve_secret_store,
)
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

#: Convenience variable so the credential can be piped in without appearing in
#: the process list or shell history.
PASSWORD_ENV = "LIUHAO_NEW_PASSWORD"

#: Refused outright: these are the kernel's own machine identities. Registering
#: either as human would re-open the C-7 hole this script exists to close.
MACHINE_PRINCIPALS = ("system", "liuhao-internal-service")


# ---------------------------------------------------------------------------
# store resolution
# ---------------------------------------------------------------------------


def build_store(args: argparse.Namespace):
    """Resolve the identity store from CLI flags first, environment second."""
    if args.file:
        os.environ[HUMAN_IDENTITIES_FILE_ENV] = str(Path(args.file).expanduser())
    if args.backend:
        os.environ[HUMAN_IDENTITIES_BACKEND_ENV] = args.backend
    if args.db:
        os.environ[HUMAN_IDENTITIES_DB_ENV] = str(Path(args.db).expanduser())
    return resolve_human_identity_store()


def build_secret_store(args: argparse.Namespace):
    """Resolve the credential store from CLI flags first, environment second."""
    if args.secrets_file:
        os.environ[AUTH_SECRETS_FILE_ENV] = str(Path(args.secrets_file).expanduser())
    return resolve_secret_store()


def env_hint(args: argparse.Namespace) -> str:
    """The variable the *service* must set for this store to be read at boot."""
    if os.environ.get(HUMAN_IDENTITIES_DB_ENV):
        return f"{HUMAN_IDENTITIES_DB_ENV}={os.environ[HUMAN_IDENTITIES_DB_ENV]}"
    if os.environ.get(HUMAN_IDENTITIES_FILE_ENV):
        return f"{HUMAN_IDENTITIES_FILE_ENV}={os.environ[HUMAN_IDENTITIES_FILE_ENV]}"
    return f"({HUMAN_IDENTITIES_FILE_ENV} unset -- kernel will load nobody)"


def secrets_hint(secret_store) -> str:
    """The variable the *service* must set for logins to be possible."""
    if not secret_store.available():
        return f"({AUTH_SECRETS_FILE_ENV} unset or missing -- nobody can log in)"
    return f"{AUTH_SECRETS_FILE_ENV}={secret_store.path}"


# ---------------------------------------------------------------------------
# identity round trip
# ---------------------------------------------------------------------------


def _point_kernel_at(store) -> Dict[str, Optional[str]]:
    """Point the identity env at ``store``; return the previous values."""
    previous: Dict[str, Optional[str]] = {
        key: os.environ.get(key)
        for key in (
            HUMAN_IDENTITIES_BACKEND_ENV,
            HUMAN_IDENTITIES_DB_ENV,
            HUMAN_IDENTITIES_FILE_ENV,
        )
    }
    if store.backend_name == BACKEND_SQLITE:
        os.environ[HUMAN_IDENTITIES_BACKEND_ENV] = BACKEND_SQLITE
        os.environ[HUMAN_IDENTITIES_DB_ENV] = store.location
        os.environ.pop(HUMAN_IDENTITIES_FILE_ENV, None)
    else:
        os.environ.pop(HUMAN_IDENTITIES_BACKEND_ENV, None)
        os.environ.pop(HUMAN_IDENTITIES_DB_ENV, None)
        os.environ[HUMAN_IDENTITIES_FILE_ENV] = store.location
    return previous


def _restore_env(previous: Dict[str, Optional[str]]) -> None:
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def verify_round_trip(store, principal: str) -> bool:
    """Boot a fresh manager against the same store and ask the real question."""
    previous = _point_kernel_at(store)
    try:
        manager = IdentityManager()
        return is_human_identity(manager.get_identity_by_principal(principal))
    finally:
        _restore_env(previous)


def verify_round_trip_many(store, principals: List[Any]) -> set:
    previous = _point_kernel_at(store)
    try:
        manager = IdentityManager()
        return {
            str(p)
            for p in principals
            if p and is_human_identity(manager.get_identity_by_principal(str(p)))
        }
    finally:
        _restore_env(previous)


# ---------------------------------------------------------------------------
# credential helpers
# ---------------------------------------------------------------------------


def credential_summary(secret_store, principal: str) -> str:
    """One line describing the login credential. Never includes the secret."""
    info = describe_credential(secret_store.credential_for(principal))
    if not info.get("configured"):
        return "no login credential -- cannot sign in"
    return (
        f"login credential set (algo={info.get('algo')}, "
        f"iterations={info.get('iterations')})"
    )


def resolve_password(args: argparse.Namespace) -> Optional[str]:
    """Read the credential from a flag, stdin, or the environment.

    ``is not None`` rather than truthiness: ``--password ""`` must be reported
    as an empty credential and refused, not silently downgraded to "no
    password was given" (which would register an identity nobody can sign in
    as, while the operator believes the opposite).
    """
    if args.password_stdin:
        data = sys.stdin.read()
        return data.rstrip("\r\n")
    if args.password is not None:
        return args.password
    return os.environ.get(PASSWORD_ENV) or None


def prove_login(principal: str, password: str) -> Optional[str]:
    """Run the real login path. Returns an error string, or None on success."""
    try:
        result = authenticate(principal, password, client="web")
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        return f"{type(exc).__name__}: {exc}"
    return None if result.get("access_token") else "no token was issued"


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------


def cmd_list(store, secret_store, args: argparse.Namespace) -> int:
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
    logins = 0
    for entry in humans:
        principal = entry.get(FIELD_PRINCIPAL)
        name = entry.get(FIELD_DISPLAY_NAME) or "-"
        perms = ", ".join(sorted(entry.get(FIELD_PERMISSIONS) or [])) or "-"
        registered = entry.get(FIELD_REGISTERED_AT) or "-"
        flag = "OK " if principal in ok else "BAD"
        summary = credential_summary(secret_store, str(principal))
        if summary.startswith("login credential"):
            logins += 1
        print(f"  [{flag}] {principal}")
        print(f"         display_name={name}  permissions={perms}")
        print(f"         registered_at={registered}")
        print(f"         {summary}")
    print()
    print(f"Can sign in: {logins}/{len(humans)}  (needs a login credential)")
    print(f"Kernel reads this store when: {env_hint(args)}")
    print(f"Console reads credentials from: {secrets_hint(secret_store)}")
    return 0 if len(ok) == len(humans) else 1


def cmd_register(store, secret_store, args: argparse.Namespace, principal: str,
                 display_name: Optional[str], permissions: List[str],
                 password: Optional[str]) -> int:
    principal = principal.strip()
    if not principal:
        print("--principal is required", file=sys.stderr)
        return 2
    if principal in MACHINE_PRINCIPALS:
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

    if password:
        if not secret_store.set_credential(principal, password):
            print()
            print(f"!! could not write the credential to {secret_store.path}.",
                  file=sys.stderr)
            return 1
        print(f"credential: written to {secret_store.path} (salt + hash only; "
              "the secret is not stored)")
        failure = prove_login(principal, password)
        if failure:
            print()
            print("!! VERIFICATION FAILED: the credential was written but a real")
            print(f"   login still fails: {failure}")
            return 1
        print("verified  : an actual sign-in with this credential succeeded.")

    print()
    print("To make it live, set these in the service environment:")
    print(f"  {env_hint(args)}")
    print(f"  {secrets_hint(secret_store)}")
    if not password:
        print()
        print("No login credential was set, so this principal cannot sign in to")
        print("the console yet. Add one with:")
        print(f"  python scripts/register_human_identity.py --principal {principal} "
              "--password-stdin")
    print()
    print("Undo with:")
    print(f"  python scripts/register_human_identity.py --revoke {principal}")
    print()
    print("NOTE: this records WHO may approve. It does not itself approve")
    print("anything -- issue a grant per action with POST /v1/policy/approvals,")
    print("or scripts/issue_console_token.py for the console token.")
    return 0


def cmd_clear_credential(secret_store, principal: str) -> int:
    if not secret_store.remove_credential(principal):
        print(f"{principal!r} has no login credential in {secret_store.path} "
              "-- nothing to clear.")
        return 1
    print(f"credential cleared: {principal}")
    print(f"store             : {secret_store.path}")
    print()
    print("The identity is untouched: it still counts as a registered human.")
    print("It simply can no longer obtain a token, so nobody can sign in as it.")
    return 0


def cmd_revoke(store, secret_store, principal: str) -> int:
    removed_identity = store.remove(principal)
    removed_credential = secret_store.remove_credential(principal)
    if not removed_identity and not removed_credential:
        print(f"{principal!r} was not registered in "
              f"{store.backend_name} @ {store.location} -- nothing to revoke.")
        return 1
    print(f"revoked        : {principal}")
    print(f"identity store : {store.backend_name} @ {store.location} "
          f"({'removed' if removed_identity else 'not present'})")
    print(f"credential     : {secret_store.path} "
          f"({'removed' if removed_credential else 'not present'})")
    print()
    print("Revoking here stops FUTURE approvals and sign-ins by this principal.")
    print("It does not recall grants already issued -- revoke those with")
    print("DELETE /v1/policy/approvals/{grant_id}. Tokens already issued remain")
    print("valid until they expire; POST /v1/auth/logout revokes one early.")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
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
    parser.add_argument("--clear-credential",
                        help="remove only the login credential for a principal")
    parser.add_argument("--password",
                        help="set the console login credential (visible in the "
                             "process list -- prefer --password-stdin)")
    parser.add_argument("--password-stdin", action="store_true",
                        help=f"read the credential from stdin (or ${PASSWORD_ENV})")
    parser.add_argument("--file", dest="file",
                        help=f"JSON seed file (default: ${HUMAN_IDENTITIES_FILE_ENV} "
                             f"or {DEFAULT_FILE})")
    parser.add_argument("--db", dest="db",
                        help=f"SQLite database (sets ${HUMAN_IDENTITIES_DB_ENV}; "
                             "implies --backend sqlite)")
    parser.add_argument("--backend", dest="backend", choices=["file", "sqlite"],
                        help="which store to write to (default: inferred from env)")
    parser.add_argument("--secrets-file", dest="secrets_file",
                        help=f"credential file (default: ${AUTH_SECRETS_FILE_ENV} "
                             f"or {DEFAULT_SECRETS_PATH})")
    args = parser.parse_args(argv)

    store = build_store(args)
    secret_store = build_secret_store(args)

    if args.list:
        return cmd_list(store, secret_store, args)
    if args.revoke:
        return cmd_revoke(store, secret_store, args.revoke.strip())
    if args.clear_credential:
        return cmd_clear_credential(secret_store, args.clear_credential.strip())
    if args.principal:
        perms = [p.strip() for p in args.permissions.split(",") if p.strip()]
        password = resolve_password(args)
        if password == "":
            print("refusing an empty credential", file=sys.stderr)
            return 2
        return cmd_register(
            store, secret_store, args, args.principal, args.display_name, perms, password
        )

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
