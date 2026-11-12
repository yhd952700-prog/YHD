"""ADR-HOLD-SCOPE §4 — the two acceptance tests the ADR MANDATES but which did
not exist before this file.

``docs/adr/ADR-HOLD-SCOPE.md`` §4 ("Acceptance / verification notes") states, in
imperative form:

  1. "A test MUST exist proving that, with ``HOLD`` active, an automated
     deploy/release is blocked, **while** a ``human_sovereignty_override`` is
     still permitted and appends (never edits) the audit chain."
  2. "A test MUST exist proving that D22-style read-only tooling runs
     uninterrupted under ``HOLD``."

Before this file *neither* existed:

  * ``tests/test_check_hold_gate.py`` proves the gate's own truth table
    (``LIUHAO_HOLD=1`` -> non-zero) but never exercises the override channel;
  * ``tests/scripts/test_derive_audit_view.py`` proves the D22 tool on a
    synthetic source but never under an active ``LIUHAO_HOLD``;
  * ``tests/scripts/test_verify_critical5_hold_arming.py`` grades CI wiring,
    not the freeze's interaction with the human-sovereignty channel.

So §2.4's sentence "Human override still passes … MUST still permit an explicit
``human_sovereignty_override`` to proceed" was an ASSERTION with no
machine-verifiable basis — the exact sovereignty-washing shape this domain
exists to remove ("声称尊重主权但无机器可验证机制"). The security-kernel twin of
this defect class (SEC-5: a bare boolean override) is already closed; this file
closes the *governance* half by giving the §2.4/§4 requirement an executable
basis.

These are BEHAVIOUR tests. They FAIL if the invariant breaks:

  * gate stopped blocking under HOLD                 -> test_hold_active_blocks_release RED
  * a HOLD that swallows the override (no append)     -> test_override_still_appends_under_hold RED
  * history rewritten instead of appended             -> test_override_still_appends_under_hold RED
  * read-only view started consulting HOLD            -> test_read_only_audit_tooling_runs_under_hold RED

Scope honesty (stated, not hidden): the *release-backend* side of §4 bullet 1 —
"an automated deploy/release is blocked" — is proven at the machine-enforceable
boundary that actually exists, which per ADR §2.1/§2.4 is ``check_hold_gate.py``
(the CI release gate). There is no separate deploy-time check in ``src/`` (the
ADR says so explicitly), so this file does not claim to have run a real GitHub
release job; it proves the control that the release job consults refuses to let
the job proceed.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
GATE = REPO_ROOT / "scripts" / "check_hold_gate.py"
DERIVE = REPO_ROOT / "scripts" / "derive_audit_view.py"

#: Concrete target for the sovereignty grant (target-binding is mandatory --
#: see src/kernels/_sovereignty.py and tests/kernels/test_sovereignty_target_binding.py).
_TARGET = "kernel-target:hold-override"


# --------------------------------------------------------------------------- #
# Fixtures / loaders
# --------------------------------------------------------------------------- #
def _load_script_module(tag: str, path: Path):
    """Import a ``scripts/*.py`` file by path so its real functions are driven."""
    spec = importlib.util.spec_from_file_location(tag, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[tag] = mod  # required before exec_module so decorators resolve
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def hold_active(monkeypatch):
    """Put the whole process under an ACTIVE HOLD (``LIUHAO_HOLD=1``).

    A value of ``1`` is truthy for ``check_hold_gate.evaluate`` and therefore
    means "release blocked" -- the freeze is genuinely in force for the duration
    of the test. ``monkeypatch`` restores the environment afterwards.
    """
    monkeypatch.setenv("LIUHAO_HOLD", "1")
    return "1"


@pytest.fixture
def human(monkeypatch):
    """An isolated IdentityManager holding exactly one ACTIVE human identity.

    The override must be attributable to a *verified* human (OD-010 / C-7), so
    the identity kernel is patched to this isolated manager -- the same pattern
    the existing sovereignty tests use.
    """
    from src.kernels.identity import IdentityManager, IdentityScope, IdentityStatus

    mgr = IdentityManager()
    who = mgr.create_identity(
        "human-hold-override", scope=IdentityScope.L0, metadata={"kind": "human"}
    )
    assert who.status == IdentityStatus.ACTIVE
    monkeypatch.setattr("src.kernels.identity.get_identity_manager", lambda: mgr)
    from src.kernels import _sovereignty as sov

    sov.clear_grants()
    yield mgr, who
    sov.clear_grants()


# --------------------------------------------------------------------------- #
# §4 bullet 1a — with HOLD active an automated release is BLOCKED
# --------------------------------------------------------------------------- #
def test_hold_active_blocks_release(hold_active):
    """The release gate must refuse to let an automated release proceed.

    Driven two ways on purpose: the real subprocess (what CI runs) and the pure,
    importable decision function (so a broken subprocess wrapper cannot hide a
    wrong verdict).
    """
    proc = subprocess.run(
        [sys.executable, str(GATE)],
        env={**os.environ, "LIUHAO_HOLD": "1"},
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0, (
        f"release was NOT blocked under an active HOLD: rc={proc.returncode}, "
        f"stdout={proc.stdout!r}"
    )
    assert "HOLD active" in proc.stdout, proc.stdout
    assert "release blocked" in proc.stdout, proc.stdout

    gate = _load_script_module("check_hold_gate_ut", GATE)
    active, msg = gate.evaluate("1")
    assert active is True
    assert "release blocked" in msg


# --------------------------------------------------------------------------- #
# §4 bullet 1b — with HOLD active the human override still APPENDS (never edits)
# --------------------------------------------------------------------------- #
def test_override_still_appends_under_hold(hold_active, human):
    """A ``human_sovereignty_override`` must still work while HOLD is active,
    and it must APPEND to the audit chain -- never edit, delete or reorder it.

    The proof has three independent legs, so a regression cannot slip through by
    satisfying only one:

      (1) the override executes (a real grant is issued and revoked);
      (2) the audit chain still verifies AND grew by at least the two lifecycle
          events the override writes (append happened);
      (3) no pre-existing event's stored hash changed (history was NOT rewritten).
    """
    from src.kernels import _sovereignty as sov
    from src.kernels.audit import AuditEventType, audit_query, get_audit_store

    store = get_audit_store()

    # -- before ---------------------------------------------------------------
    ok_before, total_before = store.verify_integrity()
    assert ok_before, "precondition: the chain must verify before the test"

    # Anchor on a concrete pre-existing event by its event_id (seq-agnostic),
    # so the "never edits" leg does not depend on any seq-arithmetic assumption.
    newest_before = audit_query(limit=1, reverse=True)
    anchor = newest_before[0] if newest_before else None

    # -- the override, WHILE HOLD IS ACTIVE -----------------------------------
    _mgr, who = human
    grant = sov.issue_grant(
        who.id, ["capability.retire"], reason="override under active HOLD", target=_TARGET
    )
    revoked = sov.revoke_grant(grant.grant_id, revoked_by=who.id, reason="done")
    assert grant.grant_id and revoked.is_revoked is True

    # -- after ----------------------------------------------------------------
    ok_after, total_after = store.verify_integrity()

    # (2) appended, and the chain is still sound
    assert ok_after, "the override broke chain integrity -- it must APPEND, not rewrite"
    assert total_after >= total_before + 2, (
        f"HOLD swallowed (or suppressed) the override: total went "
        f"{total_before} -> {total_after}; the issued+revoked lifecycle must "
        f"append at least 2 HUMAN_SOVEREIGNTY_OVERRIDE events"
    )

    # (3) the prior prefix is byte-identical -- history was never rewritten
    if anchor is not None:
        refetched = store.get_event(anchor["event_id"])
        assert refetched is not None, (
            "a pre-existing event disappeared while the override ran: history "
            "was DELETED, not appended to"
        )
        assert refetched["event_hash"] == anchor["event_hash"], (
            "a pre-existing event's stored hash changed while the override ran: "
            "history was EDITED, not appended to"
        )

    # (1) the override produced real, attributable audit events
    lifecycle = [
        e
        for e in audit_query(
            event_type=AuditEventType.HUMAN_SOVEREIGNTY_OVERRIDE, limit=80, reverse=True
        )
        if (e.get("details") or {}).get("grant_id") == grant.grant_id
    ]
    assert lifecycle, "the override wrote NO audit event while HOLD was active"
    assert any(e.get("outcome") == "granted" for e in lifecycle)
    assert any(e.get("outcome") == "revoked" for e in lifecycle)


# --------------------------------------------------------------------------- #
# §4 bullet 2 — D22-style read-only tooling runs UNINTERRUPTED under HOLD
# --------------------------------------------------------------------------- #
def _synthetic_audit_db(path: Path, derive_mod) -> Path:
    """A minimal, valid audit chain on disk (2 order-provable events).

    Mirrors the schema the authoritative writer produces and the hash the D22
    tool recomputes (``canonical_content_hash``), so the tool sees a well-formed
    source. Nothing here touches the real ``audit_store.db``.
    """
    conn = sqlite3.connect(str(path))
    conn.execute(
        """CREATE TABLE audit_events (
            event_id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            principal_id TEXT NOT NULL,
            scope TEXT NOT NULL,
            timestamp REAL NOT NULL,
            correlation_id TEXT NOT NULL,
            outcome TEXT NOT NULL,
            details TEXT,
            event_hash TEXT NOT NULL,
            prev_event_hash TEXT,
            created_at REAL NOT NULL,
            seq INTEGER NOT NULL,
            hash_alg TEXT NOT NULL DEFAULT 'sha256'
        )"""
    )
    rows = []
    for i, sec in enumerate((3001, 3002)):
        rec = {
            "event_id": f"state-{sec}",
            "event_type": "state_change",
            "principal_id": "sys",
            "scope": "L1",
            "timestamp": float(sec) + 0.5,
            "correlation_id": f"corr-state-{sec}",
            "outcome": "ok",
            "details": None,
            "prev_event_hash": None,
            "created_at": float(sec),
            "seq": i + 1,
            "hash_alg": "sha256",
        }
        rec["event_hash"] = derive_mod.canonical_content_hash(rec)
        rows.append(rec)
    conn.executemany(
        "INSERT INTO audit_events "
        "(event_id,event_type,principal_id,scope,timestamp,correlation_id,"
        "outcome,details,event_hash,prev_event_hash,created_at,seq,hash_alg) "
        "VALUES (:event_id,:event_type,:principal_id,:scope,:timestamp,"
        ":correlation_id,:outcome,:details,:event_hash,:prev_event_hash,"
        ":created_at,:seq,:hash_alg)",
        rows,
    )
    conn.commit()
    conn.close()
    return path


def test_read_only_audit_tooling_runs_under_hold(hold_active, tmp_path):
    """The D22 read-only derived view must not be gated by the freeze.

    Reporting/forensic tooling is explicitly OUT of HOLD's scope (ADR §2.2 N2/N3)
    -- it only reads and never mutates the record of record, and it is often
    needed *during* a freeze. If a freeze ever swallowed it, the freeze itself
    would become unobservable. This drives the real tool under an active HOLD.
    """
    derive = _load_script_module("derive_audit_view_ut", DERIVE)
    src = _synthetic_audit_db(tmp_path / "synthetic_audit_store.db", derive)
    out = tmp_path / "out"

    rc = derive.main(["--source", str(src), "--out", str(out)])
    assert rc == 0, (
        "D22 read-only tooling was INTERRUPTED while HOLD is active -- the "
        "freeze must never gate read-only forensic/reporting tooling (ADR §2.2)"
    )

    summary = json.loads((out / derive.PARTITION_FILENAME).read_text(encoding="utf-8"))[
        "summary"
    ]
    assert summary["view_status"] == "COMPLETE", summary
    assert summary["pre_existing_sidecars"] == []
