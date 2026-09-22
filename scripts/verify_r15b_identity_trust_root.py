#!/usr/bin/env python
"""Runtime probe: prove R-15b identity trust-root escalation is blocked.

Drives the REAL ``IdentityManager`` against a REAL on-disk store -- both the
JSON file backend and the SQLite backend. Nothing here mocks the store, the
manager, or the audit path. The original R-15b escalation (challenge report
§F.3 F29 / §D.1 R-15b) was:

    a human registry row whose principal equalled an existing agent id landed on
    the agent's slot (human id == principal; dedup looked only at the principal
    index) and inherited trust_score=1.0 plus human permissions -- deterministic
    privilege escalation available on day one, leaving no audit trace.

This probe asserts the containment that replaced it:

  * a human whose principal is shaped like an agent id is REFUSED on the seed
    path (``_claim_slot`` refuses ``is_agent_id(principal)`` for humans);
  * a human can never be created onto an existing agent's principal slot
    (the principal-index dedup now guards this -- the original hole);
  * when the integrity key is configured, a row that does not authenticate is
    REFUSED (fail-closed), while a correctly tagged row is admitted;
  * a legitimate human (well-formed principal, correct tag / no key) is still
    admitted -- i.e. the opt-in integrity design does not break registration.

Exit codes:
  0  every security assertion passed
  1  a security assertion FAILED (regression -- must not ship)
  2  partial / known gap (unused: this probe is all-or-nothing)
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

# Make the repository importable when invoked as ``python scripts/...``.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.kernels.identity import (  # noqa: E402
    IdentityManager,
    is_agent_id,
    is_human_identity,
)
from src.kernels.identity._persistence import (  # noqa: E402
    HUMAN_IDENTITIES_FILE_ENV,
    HUMAN_IDENTITIES_DB_ENV,
    HUMAN_IDENTITIES_BACKEND_ENV,
    HUMAN_IDENTITIES_INTEGRITY_KEY_ENV,
    BACKEND_FILE,
    BACKEND_SQLITE,
    JsonFileStore,
    SqliteHumanIdentityStore,
    compute_row_tag,
    integrity_key,
)

_RESULTS: list[tuple[bool, str]] = []


def _record(ok: bool, label: str) -> None:
    _RESULTS.append((ok, label))
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {label}")


def _clear_identity_env() -> None:
    for key in (
        HUMAN_IDENTITIES_FILE_ENV,
        HUMAN_IDENTITIES_DB_ENV,
        HUMAN_IDENTITIES_BACKEND_ENV,
        HUMAN_IDENTITIES_INTEGRITY_KEY_ENV,
    ):
        os.environ.pop(key, None)


def _set_backend(backend: str, path: str, *, key: str = "") -> None:
    _clear_identity_env()
    if backend == BACKEND_FILE:
        os.environ[HUMAN_IDENTITIES_FILE_ENV] = path
    else:
        os.environ[HUMAN_IDENTITIES_DB_ENV] = path
        os.environ[HUMAN_IDENTITIES_BACKEND_ENV] = BACKEND_SQLITE
    if key:
        os.environ[HUMAN_IDENTITIES_INTEGRITY_KEY_ENV] = key


def _seed_file(path: str, rows: list[dict]) -> None:
    document = {"humans": rows}
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(document, handle, ensure_ascii=False, indent=2)


def _seed_sqlite(db_path: str, rows: list[dict], *, key: str) -> SqliteHumanIdentityStore:
    store = SqliteHumanIdentityStore(db_path)
    for row in rows:
        store.upsert(row)  # auto-tags when key is configured
    return store


def _probe_seed_escalation(backend: str, tmp: str) -> None:
    """A human row whose principal is agent-shaped must be refused on seed."""
    store_path = os.path.join(tmp, f"r15b_seed_{backend}")
    if backend == BACKEND_FILE:
        _set_backend(BACKEND_FILE, store_path)
        # principal == an 8-hex agent id -- the exact shape R-15b exploited.
        _seed_file(
            store_path,
            [{"principal": "a1b2c3d4", "permissions": ["admin"], "scope": "L0"}],
        )
    else:
        _set_backend(BACKEND_SQLITE, store_path)
        _seed_sqlite(
            store_path,
            [{"principal": "a1b2c3d4", "permissions": ["admin"], "scope": "L0"}],
            key="",
        )

    mgr = IdentityManager()
    namespaces = mgr.describe_identity_namespaces()

    # The attacking principal must NOT have become a human identity.
    by_principal = mgr.get_identity_by_principal("a1b2c3d4")
    refused = any(
        "a1b2c3d4" in r for r in namespaces.get("registry_refusals", [])
    )
    _record(
        by_principal is None or not is_human_identity(by_principal),
        f"[{backend}] agent-shaped human principal 'a1b2c3d4' was NOT admitted "
        f"as a human",
    )
    _record(
        refused,
        f"[{backend}] the refusal of 'a1b2c3d4' is recorded in "
        f"registry_refusals (no silent overwrite)",
    )


def _probe_create_over_agent_slot(backend: str, tmp: str) -> None:
    """Creating a human over an existing agent's principal must be refused."""
    store_path = os.path.join(tmp, f"r15b_create_{backend}")
    _set_backend(backend, store_path)

    mgr = IdentityManager()
    agent = mgr.create_identity(principal="victim_agent", permissions={"read"})
    assert agent is not None, "precondition: agent must create"
    agent_id = agent.id

    # Now try to register a human with the SAME principal the agent owns.
    human = mgr.create_human_identity(principal="victim_agent", permissions={"admin"})
    _record(
        human is None,
        f"[{backend}] create_human_identity over an existing agent principal "
        f"is refused (returns None)",
    )
    # The slot must still belong to the agent, not a human.
    incumbent = mgr.get_identity(agent_id)
    _record(
        incumbent is not None and not is_human_identity(incumbent),
        f"[{backend}] the original agent slot is intact (not overwritten by a "
        f"human)",
    )
    # And a human must not resolve through the agent's principal.
    resolved = mgr.get_identity_by_principal("victim_agent")
    _record(
        resolved is None or not is_human_identity(resolved),
        f"[{backend}] no human resolves through the agent's principal",
    )


def _probe_integrity_fail_closed(backend: str, tmp: str) -> None:
    """With the key set, an unauthenticated row is refused; a tagged row is admitted."""
    key = "probe-integrity-secret"
    store_path = os.path.join(tmp, f"r15b_integrity_{backend}")

    # --- tampered / unauthenticated row ---------------------------------
    if backend == BACKEND_FILE:
        _set_backend(BACKEND_FILE, store_path, key=key)
        # Row present but with NO integrity tag, and a bogus tag variant.
        _seed_file(
            store_path,
            [
                {
                    "principal": "tampered_boss",
                    "permissions": ["admin"],
                    "scope": "L0",
                    # NOTE: no "integrity" field -> must be refused
                },
            ],
        )
    else:
        _set_backend(BACKEND_SQLITE, store_path, key=key)
        store = _seed_sqlite(
            store_path,
            [{"principal": "tampered_boss", "permissions": ["admin"], "scope": "L0"}],
            key=key,
        )
        # Corrupt the tag after the fact to simulate a hand-edited row.
        with store._connect() as conn:  # noqa: SLF001 - probe-only
            conn.execute(
                "UPDATE human_identities SET integrity='bogus:deadbeef' "
                "WHERE principal='tampered_boss'"
            )
            conn.commit()

    mgr = IdentityManager()
    namespaces = mgr.describe_identity_namespaces()
    tampered_identity = mgr.get_identity_by_principal("tampered_boss")
    # The integrity rejection is recorded by the store at load time, surfaced
    # through describe_identity_namespaces()["registry_integrity"]["last_load"].
    last_load = namespaces.get("registry_integrity", {}).get("last_load", {})
    rejected = "tampered_boss" in (last_load.get("rejected", []) or [])
    _record(
        tampered_identity is None or not is_human_identity(tampered_identity),
        f"[{backend}] unauthenticated row 'tampered_boss' NOT admitted",
    )
    _record(
        rejected,
        f"[{backend}] unauthenticated row 'tampered_boss' reported as rejected "
        f"(fail-closed)",
    )

    # --- correctly tagged row IS admitted --------------------------------
    if backend == BACKEND_FILE:
        # Re-seed: keep only a correctly tagged row.
        _set_backend(BACKEND_FILE, store_path, key=key)
        tagged = {
            "principal": "real_boss",
            "permissions": ["admin"],
            "scope": "L0",
        }
        tagged["integrity"] = compute_row_tag(tagged, key)
        _seed_file(store_path, [tagged])
    else:
        # The previous sqlite store already has tampered_boss; add a clean one.
        _set_backend(BACKEND_SQLITE, store_path, key=key)
        store = SqliteHumanIdentityStore(store_path)
        store.upsert(
            {"principal": "real_boss", "permissions": ["admin"], "scope": "L0"}
        )

    mgr2 = IdentityManager()
    real = mgr2.get_identity_by_principal("real_boss")
    _record(
        real is not None and is_human_identity(real),
        f"[{backend}] correctly tagged row 'real_boss' IS admitted as human",
    )


def _probe_legitimate_human_without_key(backend: str, tmp: str) -> None:
    """Without the key (opt-in default), a well-formed human is still admitted."""
    store_path = os.path.join(tmp, f"r15b_legacy_{backend}")
    if backend == BACKEND_FILE:
        _set_backend(BACKEND_FILE, store_path)
        _seed_file(
            store_path,
            [{"principal": "legacy_boss", "permissions": ["admin"], "scope": "L0"}],
        )
    else:
        _set_backend(BACKEND_SQLITE, store_path)
        _seed_sqlite(
            store_path,
            [{"principal": "legacy_boss", "permissions": ["admin"], "scope": "L0"}],
            key="",
        )

    mgr = IdentityManager()
    legacy = mgr.get_identity_by_principal("legacy_boss")
    _record(
        legacy is not None and is_human_identity(legacy),
        f"[{backend}] legacy (no-key) human 'legacy_boss' still admitted -- "
        f"opt-in integrity does not break registration",
    )
    # And the registry reports its posture HONESTLY (boss decision 2026-09-22):
    # a registry in use with humans but no key must be DEGRADED / UNVERIFIED,
    # never presented as fully sovereign.
    integrity_report = mgr.describe_identity_namespaces().get(
        "registry_integrity", {}
    )
    _record(
        integrity_report.get("integrity_enforced") is False,
        f"[{backend}] registry honestly reports integrity_enforced=False when "
        f"no key is configured",
    )
    _record(
        integrity_report.get("integrity_state") == "degraded_unverified",
        f"[{backend}] registry in use WITHOUT a key reports "
        f"integrity_state=degraded_unverified (not 'fully sovereign')",
    )


def main() -> int:
    print("R-15b identity trust-root runtime probe")
    print("  (real IdentityManager + real on-disk store, both backends)\n")
    tmp = tempfile.mkdtemp(prefix="r15b_probe_")
    try:
        for backend in (BACKEND_FILE, BACKEND_SQLITE):
            print(f"-- backend: {backend} --")
            _probe_seed_escalation(backend, tmp)
            _probe_create_over_agent_slot(backend, tmp)
            _probe_integrity_fail_closed(backend, tmp)
            _probe_legitimate_human_without_key(backend, tmp)
    finally:
        _clear_identity_env()

    passed = sum(1 for ok, _ in _RESULTS if ok)
    total = len(_RESULTS)
    print(f"\n{passed}/{total} assertions passed")
    failed = [label for ok, label in _RESULTS if not ok]
    if failed:
        print("FAILED:")
        for label in failed:
            print(f"  - {label}")
        return 1
    print("R-15b escalation path is CONTAINED (runtime-verified).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
