#!/usr/bin/env python
"""P0-8c — explicit migration that stamps legacy hash-chain files with `hash_alg`.

Why this exists
---------------
Before P0-8b/8c the JSON hash-chain stores (HC-02..HC-08) computed their links
with a **hard-coded** ``hashlib.sha256`` and never recorded which algorithm was
used. P0-8c then removed the silent default, so such an envelope is now
UNVERIFIED (``verify_integrity()`` returns False) instead of being silently
read as sha256.

This script is the migration that makes legacy data verifiable again.

Why 'sha256' is a FACT here, not an assumption
----------------------------------------------
The pre-change code is in git history and can be checked by anyone:

    git show 0c353117514e0d3bc5be1a8dbabd6ea468c63b4f:<store>.py | grep sha256

Every one of the seven stores used ``hashlib.sha256(...)``. Stamping 'sha256'
therefore records what actually happened; it does not guess.

Guarantees
----------
* **No hash is recomputed and no record is rewritten.** Only the envelope's
  ``hash_alg`` key is added. Existing chains and records are byte-identical.
* **Old evidence is not overwritten without a copy**: a ``*.bak`` sidecar is
  written before any modification.
* **Idempotent**: a file that already declares ``hash_alg`` is left untouched.
* **Conservative**: only files that actually look like a chain file (they carry
  a ``hash_chain`` key) are considered.

Usage
-----
    python scripts/migrate_p08_hash_alg.py              # dry run (default)
    python scripts/migrate_p08_hash_alg.py --apply      # actually migrate
    python scripts/migrate_p08_hash_alg.py --apply data/plugins/marketplace/plugins.json

Exit code is 0 in all reporting cases; it is 1 only on an I/O error.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from typing import List

#: The algorithm the pre-P0-8 code actually used (see git forensics above).
LEGACY_HASH_ALG = "sha256"

DEFAULT_SCAN_DIRS = ["data"]


def _is_chain_file(payload: object) -> bool:
    return isinstance(payload, dict) and "hash_chain" in payload


def migrate_file(path: str, apply: bool) -> str:
    """Return a one-line status for *path*."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, ValueError) as exc:
        return f"SKIP (unreadable: {exc})"

    if not _is_chain_file(payload):
        return "SKIP (not a hash-chain file)"

    if payload.get("hash_alg"):
        return f"ALREADY-DECLARED ({payload['hash_alg']})"

    if not apply:
        return f"WOULD-STAMP -> {LEGACY_HASH_ALG} (dry run)"

    # Preserve the old evidence before touching anything.
    backup = path + ".bak"
    if not os.path.exists(backup):
        shutil.copy2(path, backup)

    payload["hash_alg"] = LEGACY_HASH_ALG
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    return f"STAMPED -> {LEGACY_HASH_ALG} (backup: {os.path.basename(backup)})"


def _iter_candidate_files(roots: List[str]):
    for root in roots:
        if os.path.isfile(root):
            yield root
            continue
        for dirpath, _dirnames, filenames in os.walk(root):
            for name in filenames:
                if name.endswith(".json") and not name.endswith(".bak"):
                    yield os.path.join(dirpath, name)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("paths", nargs="*", default=DEFAULT_SCAN_DIRS,
                    help="files or directories to migrate (default: %(default)s)")
    ap.add_argument("--apply", action="store_true",
                    help="actually write changes (default is a dry run)")
    args = ap.parse_args()

    mode = "APPLY" if args.apply else "DRY RUN"
    print(f"P0-8c legacy hash_alg migration — {mode}")
    print(f"legacy algorithm stamped: {LEGACY_HASH_ALG} "
          "(confirmed from git history, not assumed)")

    seen = 0
    changed = 0
    for path in _iter_candidate_files(args.paths):
        status = migrate_file(path, args.apply)
        if status.startswith("SKIP"):
            continue
        seen += 1
        if status.startswith(("WOULD-STAMP", "STAMPED")):
            changed += 1
        print(f"  {path}: {status}")

    print(f"\nchain files examined: {seen}; needing migration: {changed}")
    if not args.apply and changed:
        print("Re-run with --apply to migrate (a .bak copy is kept).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
