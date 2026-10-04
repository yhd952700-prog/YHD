#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_openapi_contract.py — make the published API contract (openapi.yaml) VERIFIABLE.

WHY THIS EXISTS
---------------
`openapi.yaml` is the repository's only hand-maintained, machine-readable
description of the gateway's public HTTP surface. It was found (2026-10-04) to
describe an API that **does not exist**:

  * it declared 6 paths -- `/v1/health`, `/v1/metrics`, `/v1/ready`,
    `/v1/auth/token`, `/v1/api/providers`, `/v1/rbac/roles`
  * the running gateway (FastAPI, `src.gateway.main.get_app()`) exposes 58
  * **overlap was ZERO**: 6/6 declared paths were ghosts, 58/58 real paths
    were undeclared
  * it advertised `https://api.liuhao-ai-os.com/v1` as the production server --
    a host the project does not operate

A reader (human or integrator or agent) trusting that file would have concluded
the gateway exposes a different API than it does. That is the worst class of doc
defect in this repo: not staleness, but *invention*.

WHAT THIS SCRIPT DOES
---------------------
It re-derives the contract from the running application and diffs it against the
committed file. Drift is a FAIL. It does NOT "fix" anything silently:

  * default        -> verify only, exit 1 on any drift (CI mode)
  * `--write`      -> regenerate openapi.yaml from the runtime (maintainer mode)

It deliberately does NOT load `.env` or require a server: the app object is
constructed in-process, which is exactly what makes this reproducible on a
laptop and on a CI runner.

HONESTY BOUNDARY
----------------
PASS means "the committed snapshot equals what the code currently produces".
It does NOT mean the endpoints work, are authenticated correctly, or are
production-ready -- several are known-open risks tracked elsewhere
(see MASTER_PROJECT_READINESS.md). This is a *contract-freshness* gate, not a
functional test. It also cannot see routes that exist in another ASGI app that
`src.gateway.main` does not mount; the diff is scoped to the mounted app.
"""

from __future__ import annotations

import argparse
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

CONTRACT_PATH = os.path.join(REPO_ROOT, "openapi.yaml")

# Fields we intentionally do NOT diff, because they are maintainer-authored
# prose rather than machine-derived contract facts. Drift on `info.description`
# must never be able to break CI, otherwise nobody will re-generate.
IGNORED_TOP_LEVEL_KEYS = ("info", "servers", "tags", "externalDocs")


def _load_runtime_spec():
    from src.gateway.main import get_app  # imported late: needs sys.path set up

    return get_app().openapi()


def _load_committed(path: str):
    import yaml

    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _paths_of(spec):
    return set((spec or {}).get("paths") or {})


def _normalise(paths):
    """Strip a trailing/leading slash so `/v1/x` and `/v1/x/` are one path.

    Servers in this repo are mounted at the root, so the path already contains
    its own prefix. The comparison is purely about which routes EXIST; request
    and response schema drift is FastAPI's own contract and is out of scope.
    """
    out = set()
    for p in paths:
        if not isinstance(p, str):
            continue
        p = p.strip()
        if len(p) > 1 and p.endswith("/"):
            p = p.rstrip("/")
        out.add(p)
    return out


def _operations(paths):
    """Map path -> set of HTTP methods, so a method-only change is still drift."""
    ops = {}
    for p in paths:
        entry = paths[p] or {}
        ops[p] = {
            m.upper()
            for m in entry
            if isinstance(m, str) and m.lower() in {"get", "post", "put", "delete", "patch", "head", "options"}
        }
    return ops


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        action="store_true",
        help="regenerate openapi.yaml from the running gateway (maintainer mode)",
    )
    parser.add_argument("--contract", default=CONTRACT_PATH, help="path to the contract file")
    args = parser.parse_args(argv)

    try:
        runtime = _load_runtime_spec()
    except Exception as exc:  # fail loud: a silent skip would make this a fake gate
        print(f"FAIL: could not build the gateway app: {type(exc).__name__}: {exc}")
        print("      This gate cannot verify anything without the real app object.")
        return 2

    runtime_paths = _normalise(_paths_of(runtime))
    if not runtime_paths:
        print("FAIL: the running gateway exposed ZERO paths -- that is not a real contract.")
        return 2

    if args.write:
        import yaml

        committed = _load_committed(args.contract) or {}
        out = {
            "openapi": runtime.get("openapi", "3.1.0"),
            "info": {
                "title": "LiuHao AI OS Gateway API",
                "version": ((committed.get("info") or {}).get("version") or "1.0.0"),
                "description": (
                    "GENERATED SNAPSHOT of the REAL runtime contract, exported from\n"
                    "`src.gateway.main.get_app().openapi()` by "
                    "`scripts/verify_openapi_contract.py --write`.\n\n"
                    "WHY THIS FILE USED TO BE WRONG: the previous hand-written openapi.yaml\n"
                    "declared 6 paths (`/v1/health`, `/v1/metrics`, `/v1/ready`,\n"
                    "`/v1/auth/token`, `/v1/api/providers`, `/v1/rbac/roles`) -- NOT ONE of\n"
                    "which exists in the running gateway (0/6 overlap), while omitting all 58\n"
                    "real paths. It also advertised a production host\n"
                    "`https://api.liuhao-ai-os.com` that the project does not operate.\n\n"
                    "HONESTY RULES FOR THIS FILE:\n"
                    "* It is a SNAPSHOT, not the source of truth. The source of truth is the\n"
                    "  code: `GET /openapi.json` on a running gateway (the console reads that,\n"
                    "  not this file -- see `apps/console/console/src/pages/Directory.tsx`).\n"
                    "* Verify freshness with `python scripts/verify_openapi_contract.py`.\n"
                    "  Drift is a FAIL, not a warning.\n"
                    "* Auth is fail-closed: most `/v1/*` paths require a human token.\n"
                    "  A path's presence here does NOT mean it is safe to call unauthenticated."
                ),
            },
            "servers": [
                {
                    "url": "http://localhost:8080",
                    "description": (
                        "Local gateway. Each path below carries its own prefix (e.g. `/v1/...`); "
                        "the server URL has no extra `/v1` suffix."
                    ),
                }
            ],
            "tags": committed.get("tags") or [],
            "paths": runtime.get("paths") or {},
        }
        if runtime.get("components"):
            out["components"] = runtime["components"]

        with open(args.contract, "w", encoding="utf-8") as fh:
            fh.write(
                "# AUTO-GENERATED from the running gateway by "
                "scripts/verify_openapi_contract.py --write.\n"
                "# DO NOT HAND-EDIT: hand edits are overwritten, and the next verify run\n"
                "# FAILS on drift.\n"
                "# Source of truth = GET /openapi.json on a live gateway.\n"
                "# Verify with: python scripts/verify_openapi_contract.py\n"
            )
            yaml.safe_dump(out, fh, sort_keys=True, allow_unicode=True, width=100)
        print(f"WROTE {os.path.relpath(args.contract, REPO_ROOT)} with {len(runtime_paths)} paths")
        return 0

    # ---- verify mode --------------------------------------------------
    try:
        committed = _load_committed(args.contract)
    except FileNotFoundError:
        print(f"FAIL: contract file not found: {args.contract}")
        return 1

    if committed is None:
        print(f"FAIL: {os.path.relpath(args.contract, REPO_ROOT)} is empty -- a missing contract")
        print("      is not a contract. Run with --write.")
        return 1

    committed_paths = _normalise(_paths_of(committed))

    ghosts = sorted(committed_paths - runtime_paths)
    absent = sorted(runtime_paths - committed_paths)

    rt_ops = _operations({p: runtime["paths"][p] for p in runtime_paths if p in runtime["paths"]})
    cm_ops = _operations({p: committed["paths"][p] for p in committed_paths if p in committed["paths"]})
    method_drift = sorted(
        f"{p}: committed={sorted(cm_ops.get(p, set()))} runtime={sorted(rt_ops.get(p, set()))}"
        for p in sorted(set(cm_ops) & set(rt_ops))
        if cm_ops.get(p) != rt_ops.get(p)
    )

    ok = True
    if ghosts:
        ok = False
        print(f"FAIL: {len(ghosts)} path(s) DECLARED IN THE CONTRACT DO NOT EXIST AT RUNTIME:")
        for p in ghosts:
            print(f"  - {p}")
        print("  A declared-but-absent endpoint is an invented capability: fix the contract")
        print("  (or the route), never soften this check.")
    if absent:
        ok = False
        print(f"FAIL: {len(absent)} runtime path(s) MISSING FROM THE CONTRACT:")
        for p in absent:
            print(f"  + {p}")
    if method_drift:
        ok = False
        print(f"FAIL: {len(method_drift)} path(s) disagree on HTTP method:")
        for d in method_drift:
            print(f"  ~ {d}")

    for key in IGNORED_TOP_LEVEL_KEYS:
        if key in committed and key in runtime and committed[key] == runtime[key]:
            continue

    if ok:
        print(
            f"PASS: contract matches runtime -- {len(runtime_paths)} paths, "
            f"0 ghosts, 0 missing, methods agree."
        )
        print("      (freshness only: this does NOT assert the endpoints work)")
        return 0

    print(f"\nRESULT: FAIL ({len(ghosts)} ghost, {len(absent)} missing, {len(method_drift)} method drift)")
    print("Regenerate with: python scripts/verify_openapi_contract.py --write")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
