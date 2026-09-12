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
identities in memory only, so this script persists registrations to a JSON seed
file that the kernel loads at startup:

    LIUHAO_HUMAN_IDENTITIES_FILE=/path/to/human_identities.json

The kernel reads that variable in ``IdentityManager.__init__``. Unset means
**zero registered humans** -- deliberately fail-closed rather than silently
falling back to a machine identity.

What it verifies
----------------
Writing the file is not the point; *being able to approve afterwards* is. After
every write this script boots a **fresh** ``IdentityManager`` with the seed file
pointed at it, and asserts the new principal passes ``is_human_identity``. If
that round trip fails the script exits non-zero rather than leaving behind a
file that looks like a registration but is not.

Trust boundary
--------------
Same anchor as ``scripts/issue_console_token.py``: **access to this machine's
filesystem**. Anyone who can run this can already edit the seed file, so this
is bookkeeping and audit trail, not an access control.

Usage
-----
    python scripts/register_human_identity.py --list
    python scripts/register_human_identity.py --principal xin.hongda \
        --display-name "辛宏达"
    python scripts/register_human_identity.py --revoke xin.hongda
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.kernels.identity import (  # noqa: E402
    HUMAN_IDENTITIES_FILE_ENV,
    HUMAN_KIND,
    IdentityManager,
    METADATA_DISPLAY_NAME_KEY,
    METADATA_KIND_KEY,
    is_human_identity,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FILE = REPO_ROOT / "config" / "human_identities.json"


def seed_path(explicit: str | None) -> Path:
    """Resolve where registrations live: --file > env var > repo default."""
    if explicit:
        return Path(explicit).expanduser()
    env = (os.environ.get(HUMAN_IDENTITIES_FILE_ENV) or "").strip()
    if env:
        return Path(env).expanduser()
    return DEFAULT_FILE


def load(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {"humans": []}
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if isinstance(payload, list):  # tolerate the bare-list form
        return {"humans": list(payload)}
    if isinstance(payload, dict):
        payload.setdefault("humans", [])
        return payload
    raise SystemExit(f"{path} is not a JSON object or list -- refusing to touch it")


def save(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(tmp, path)  # atomic: never leave a half-written seed file


def verify_round_trip(path: Path, principal: str) -> bool:
    """Boot a fresh manager off the seed file and ask the real question."""
    previous = os.environ.get(HUMAN_IDENTITIES_FILE_ENV)
    os.environ[HUMAN_IDENTITIES_FILE_ENV] = str(path)
    try:
        mgr = IdentityManager()
        ident = mgr.get_identity_by_principal(principal)
        return is_human_identity(ident)
    finally:
        if previous is None:
            os.environ.pop(HUMAN_IDENTITIES_FILE_ENV, None)
        else:
            os.environ[HUMAN_IDENTITIES_FILE_ENV] = previous


def cmd_list(path: Path) -> int:
    payload = load(path)
    humans: List[Dict[str, Any]] = payload.get("humans") or []
    print(f"Registered humans (seed file: {path})")
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

    ok = verify_round_trip_many(path, [h.get("principal") for h in humans])
    for entry in humans:
        name = entry.get(METADATA_DISPLAY_NAME_KEY) or "-"
        perms = ", ".join(sorted(entry.get("permissions") or [])) or "-"
        flag = "OK " if entry.get("principal") in ok else "BAD"
        print(f"  [{flag}] {entry.get('principal')}")
        print(f"         display_name={name}  permissions={perms}")
    print()
    print(f"Kernel reads this file when {HUMAN_IDENTITIES_FILE_ENV} points at it.")
    print(f"Currently: {os.environ.get(HUMAN_IDENTITIES_FILE_ENV) or '(unset -- kernel will load nobody)'}")
    return 0 if len(ok) == len(humans) else 1


def verify_round_trip_many(path: Path, principals: List[Any]) -> set:
    previous = os.environ.get(HUMAN_IDENTITIES_FILE_ENV)
    os.environ[HUMAN_IDENTITIES_FILE_ENV] = str(path)
    try:
        mgr = IdentityManager()
        return {
            p
            for p in principals
            if p and is_human_identity(mgr.get_identity_by_principal(str(p)))
        }
    finally:
        if previous is None:
            os.environ.pop(HUMAN_IDENTITIES_FILE_ENV, None)
        else:
            os.environ[HUMAN_IDENTITIES_FILE_ENV] = previous


def cmd_register(path: Path, principal: str, display_name: str | None,
                 permissions: List[str]) -> int:
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

    payload = load(path)
    humans = payload.setdefault("humans", [])
    for entry in humans:
        if entry.get("principal") == principal:
            entry[METADATA_DISPLAY_NAME_KEY] = display_name or entry.get(
                METADATA_DISPLAY_NAME_KEY
            )
            entry["permissions"] = sorted(set(permissions))
            action = "updated"
            break
    else:
        humans.append(
            {
                "principal": principal,
                METADATA_DISPLAY_NAME_KEY: display_name,
                "permissions": sorted(set(permissions)),
            }
        )
        action = "registered"
    save(path, payload)

    print(f"{action}: {principal}")
    print(f"seed file : {path}")
    print(f"marker    : metadata[{METADATA_KIND_KEY!r}] = {HUMAN_KIND!r}")

    if not verify_round_trip(path, principal):
        print()
        print("!! VERIFICATION FAILED: a freshly booted kernel does not treat")
        print("   this principal as a human. The file was written but the")
        print(f"   channel is not live. Check {HUMAN_IDENTITIES_FILE_ENV} points")
        print(f"   at {path} in the service that needs to approve.")
        return 1

    print("verified  : a fresh kernel loads this principal as a human.")
    print()
    print("To make it live, set this in the service environment:")
    print(f"  {HUMAN_IDENTITIES_FILE_ENV}={path}")
    print()
    print("Undo with:")
    print(f"  python scripts/register_human_identity.py --revoke {principal}")
    print()
    print("NOTE: this records WHO may approve. It does not itself approve")
    print("anything -- issue a grant per action with POST /v1/policy/approvals,")
    print("or scripts/issue_console_token.py for the console token.")
    return 0


def cmd_revoke(path: Path, principal: str) -> int:
    payload = load(path)
    humans = payload.get("humans") or []
    kept = [h for h in humans if h.get("principal") != principal]
    if len(kept) == len(humans):
        print(f"{principal!r} was not registered in {path} -- nothing to revoke.")
        return 1
    payload["humans"] = kept
    save(path, payload)
    print(f"revoked: {principal}")
    print(f"seed file: {path}")
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
                        help=f"seed file (default: ${HUMAN_IDENTITIES_FILE_ENV} or "
                             f"{DEFAULT_FILE})")
    args = parser.parse_args(argv)

    path = seed_path(args.file)

    if args.list:
        return cmd_list(path)
    if args.revoke:
        return cmd_revoke(path, args.revoke.strip())
    if args.principal:
        perms = [p.strip() for p in args.permissions.split(",") if p.strip()]
        return cmd_register(path, args.principal, args.display_name, perms)

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
