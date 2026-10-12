"""Registry refusals must be **provable** on the authoritative audit chain (Q2.2).

U15 / U40 record the honest limitation that ``IdentityManager._audit_log`` is an
in-memory list: it dies with the process, has no production reader, and is not
part of the hash chain. The refusals that actually matter -- a registry row the
kernel *refuses to admit* (a duplicate principal, or a bare reserved prefix) --
are therefore additionally written to the REAL hash-chained store through
``_record_denial_on_the_chain``.

"Written" is only half the claim, and it is the half that is easy to fake. A
denial that has been written is not yet a denial that can be **proven**. This
file pins the three independent properties that make a refusal provable:

  1. **Retrievable by content** on the authoritative store (``audit_query``), not
     merely present in a local list -- the denial names the acting principal, the
     action, the reason, and the registry it came from.
  2. **Covered by a verifying chain**: after the refusal the chain still verifies
     (``verify_audit_integrity`` -> ``ok``), so the denial sits inside an
     integrity-checked sequence rather than beside it.
  3. **Tamper-evident**: editing the stored denial row (the exact move an
     adversary makes to erase a refusal -- flip ``denied`` to ``success``) is
     DETECTED by the integrity check.

If any of these three regress, a refusal silently becomes unprovable again --
the evidentiary gap U15/U40 warn about, this time for the denial path.

The fixtures below are seeded through the authenticated path (HC-11 / U6): a row
only counts if it carries a valid tag under the configured key, so a test that
"seeds a human" is proving the *shipping load path* admits what it should, not
that unauthenticated rows are welcome (see ``test_human_identity_c7.py``).
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict

import pytest

from src.kernels.audit import (
    AuditEventType,
    audit_query,
    get_audit_store,
    verify_audit_integrity,
)
from src.kernels.identity import (
    HUMAN_IDENTITIES_FILE_ENV,
    HUMAN_IDENTITIES_INTEGRITY_KEY_ENV,
    IdentityManager,
    is_human_identity,
)
from src.kernels.identity._persistence import FIELD_INTEGRITY, compute_row_tag


#: Key used only by these fixtures; the store must authenticate each row against
#: it before the kernel will decide who it admits.
_TEST_KEY = "denial-chain-fixture-integrity-key"


def _stamp(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Return ``entry`` carrying a valid row tag under :data:`_TEST_KEY`."""
    tagged = dict(entry)
    tagged[FIELD_INTEGRITY] = compute_row_tag(tagged, _TEST_KEY)
    return tagged


def _write_registry(tmp_path: Path, rows) -> Path:
    path = tmp_path / "humans.json"
    path.write_text(json.dumps({"humans": rows}, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def duplicate_registry(monkeypatch, tmp_path) -> Path:
    """A registry offering the SAME human twice: the second row must be refused.

    This is the realistic shape named in the register -- "the store claimed more
    humans than the kernel admitted" -- and it forces the *duplicate* refusal
    branch, which is the one that writes to the authoritative chain.
    """
    path = _write_registry(
        tmp_path,
        [_stamp({"principal": "dup"}), _stamp({"principal": "dup"})],
    )
    monkeypatch.setenv(HUMAN_IDENTITIES_INTEGRITY_KEY_ENV, _TEST_KEY)
    monkeypatch.setenv(HUMAN_IDENTITIES_FILE_ENV, str(path))
    return path


def _latest_denial(principal: str):
    """Newest ACCESS_DENIED event for ``principal`` on the authoritative store."""
    events = audit_query(
        event_type=AuditEventType.ACCESS_DENIED,
        principal_id=principal,
        outcome="denied",
        reverse=True,
    )
    return events[0] if events else None


class TestARefusedRegistryRowIsProvable:
    def test_the_kernel_admits_one_and_refuses_the_duplicate(self, duplicate_registry):
        mgr = IdentityManager()
        admitted = [i for i in mgr.list_identities() if is_human_identity(i)]
        assert len(admitted) == 1, "exactly one of the two identical rows is admitted"
        assert mgr.describe_identity_namespaces()["registry_refusals"], (
            "the refusal must be observable, not silent"
        )

    def test_the_refusal_reaches_the_authoritative_chain_by_content(
        self, duplicate_registry
    ):
        # 1. retrievable by content -- the core provability property.
        IdentityManager()
        ev = _latest_denial("dup")
        assert ev is not None, (
            "the registry refusal is NOT on the authoritative audit chain -- "
            "a refusal recorded only in the in-memory list is, evidentially, "
            "not recorded"
        )
        det = ev["details"]
        assert det["action"] == "identity.register"
        assert "already registered" in det["reason"]
        # A denial must name the registry it came from, so the record is
        # self-contained evidence (which backend, which location).
        assert det.get("registry_backend"), "denial missing its registry backend"
        assert "registry_location" in det

    def test_the_chain_containing_the_refusal_verifies(self, duplicate_registry):
        # 2. the denial sits inside an integrity-checked sequence.
        IdentityManager()
        ok, total = verify_audit_integrity()
        assert total >= 1, "the refusal consumed a slot on the chain"
        assert ok is True, (
            "the chain carrying a denial failed integrity -- the refusal is "
            "present but not provable"
        )

    def test_a_principal_with_no_refusal_yields_no_denial_event(
        self, duplicate_registry
    ):
        # Negative control: the "reaches the chain" assertion above must be
        # measuring *this* refusal, not merely "the store has some event". A
        # principal the kernel never had to refuse has, and must have, no
        # denial event -- if this stops holding, the test above is vacuous.
        IdentityManager()
        assert _latest_denial("never-offered-to-the-registry") is None


class TestADenialCannotBeSilentlyErased:
    def test_flipping_the_denial_to_success_is_detected(self, duplicate_registry):
        # 3. tamper-evidence: the exact adversary move (rewrite the refusal as a
        # success so no one sees the rejection) must be caught, not hidden.
        IdentityManager()
        store = get_audit_store()
        ev = _latest_denial("dup")
        assert ev is not None

        # Confirm the chain is healthy BEFORE the tamper, so the failure below
        # is attributable to the edit and nothing else.
        ok_before, _ = verify_audit_integrity()
        assert ok_before is True

        con = sqlite3.connect(str(store._db_path))
        try:
            cur = con.execute(
                "UPDATE audit_events SET outcome = 'success' "
                "WHERE event_id = ? AND outcome = 'denied'",
                (ev["event_id"],),
            )
            assert cur.rowcount == 1, "the denial row was not found to tamper with"
            con.commit()
        finally:
            con.close()

        ok_after, _ = verify_audit_integrity()
        assert ok_after is False, (
            "a rewritten denial row still verifies -- the refusal is NOT "
            "tamper-evident and cannot be relied on as evidence"
        )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
