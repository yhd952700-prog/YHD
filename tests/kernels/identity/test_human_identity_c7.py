"""Policy C-7: "verified human" is a positive allowlist, not a reverse exclusion.

The finding: ``_is_verified_human`` used to accept any ACTIVE identity whose
``metadata["kind"] != "service"``. The built-in ``system`` account carries no
metadata, so it passed as a *verified human* and could hold human sovereignty
-- the audit recorded a machine as the approver of a CRITICAL action.

These tests fix the new contract in place. If the predicate ever goes back to
a reverse exclusion, ``test_an_unmarked_identity_is_not_human`` fails.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from src.kernels.identity import (
    HUMAN_IDENTITIES_FILE_ENV,
    HUMAN_KIND,
    IdentityManager,
    IdentityScope,
    IdentityStatus,
    METADATA_KIND_KEY,
    is_human_identity,
)
from src.kernels import _sovereignty as sov


@pytest.fixture
def mgr():
    """A manager with no seeded humans (env var is cleared)."""
    return IdentityManager()


class TestIsHumanIdentityIsAnAllowlist:
    def test_a_registered_human_passes(self, mgr):
        person = mgr.create_human_identity("alice")
        assert is_human_identity(person) is True
        assert person.metadata[METADATA_KIND_KEY] == HUMAN_KIND

    def test_none_is_not_human(self):
        assert is_human_identity(None) is False

    def test_an_unmarked_identity_is_not_human(self, mgr):
        # The exact C-7 hole: no kind marker at all. The OLD predicate
        # ("kind != service") returned True here; the new one must not.
        ident = mgr.create_identity("unmarked-robot", scope=IdentityScope.L0)
        assert ident.metadata == {}
        assert is_human_identity(ident) is False

    def test_a_service_identity_is_not_human(self, mgr):
        svc = mgr.create_identity(
            "robot", scope=IdentityScope.L1, metadata={METADATA_KIND_KEY: "service"}
        )
        assert is_human_identity(svc) is False

    def test_a_suspended_human_is_not_human(self, mgr):
        person = mgr.create_human_identity("bob")
        person.status = IdentityStatus.SUSPENDED
        assert is_human_identity(person) is False

    def test_the_builtin_system_identity_is_not_human(self, mgr):
        system = mgr.get_identity("system")
        assert system is not None
        assert system.metadata == {}
        assert is_human_identity(system) is False


class TestCreateHumanIdentity:
    def test_it_stamps_the_only_marker_the_verifier_accepts(self, mgr):
        person = mgr.create_human_identity("carol")
        assert person.metadata[METADATA_KIND_KEY] == HUMAN_KIND
        assert is_human_identity(person) is True

    def test_humans_are_scope_l0(self, mgr):
        # IdentityScope.L0 is documented as "Human only".
        assert mgr.create_human_identity("dave").scope is IdentityScope.L0

    def test_it_records_a_display_name(self, mgr):
        person = mgr.create_human_identity("erin", display_name="ERIN")
        assert person.metadata["display_name"] == "ERIN"

    def test_registering_twice_does_not_create_a_second_identity(self, mgr):
        first = mgr.create_human_identity("frank")
        assert mgr.create_human_identity("frank") is None
        assert mgr.get_identity_by_principal("frank").id == first.id

    def test_a_caller_cannot_forget_the_marker(self, mgr):
        # create_identity is still available but must not produce a human.
        plain = mgr.create_identity("grace", scope=IdentityScope.L0)
        assert is_human_identity(plain) is False


class TestSovereigntyRefusesMachines:
    def test_the_builtin_system_identity_cannot_hold_sovereignty(self, monkeypatch):
        mgr = IdentityManager()
        monkeypatch.setattr("src.kernels.identity.get_identity_manager", lambda: mgr)
        with pytest.raises(ValueError, match="not a registered human"):
            sov._validated_principal("system")

    def test_a_service_identity_still_gets_its_own_specific_error(self, monkeypatch):
        mgr = IdentityManager()
        mgr.create_identity(
            "svc", scope=IdentityScope.L1, metadata={METADATA_KIND_KEY: "service"}
        )
        monkeypatch.setattr("src.kernels.identity.get_identity_manager", lambda: mgr)
        with pytest.raises(ValueError, match="service identity"):
            sov._validated_principal("svc")

    def test_a_registered_human_is_accepted(self, monkeypatch):
        mgr = IdentityManager()
        mgr.create_human_identity("heidi")
        monkeypatch.setattr("src.kernels.identity.get_identity_manager", lambda: mgr)
        assert sov._validated_principal("heidi") == "heidi"


class TestSeedingMakesRegistrationSurviveRestart:
    """IdentityManager is in-memory, so registration needs a seed file."""

    def _write(self, tmp_path: Path, payload) -> Path:
        path = tmp_path / "humans.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def test_no_env_var_means_no_humans(self, monkeypatch, tmp_path):
        monkeypatch.delenv(HUMAN_IDENTITIES_FILE_ENV, raising=False)
        mgr = IdentityManager()
        assert [
            i for i in mgr.list_identities() if is_human_identity(i)
        ] == []

    def test_seeded_humans_are_loaded(self, monkeypatch, tmp_path):
        # REGRESSION: seeding runs inside __init__, before the global singleton
        # is published. Building them through create_identity() (a
        # @kernel_action) called back into get_identity_manager() and recursed
        # without bound -- the process hung. Seeded humans are therefore built
        # directly. If this test ever times out, that recursion is back.
        path = self._write(
            tmp_path,
            {
                "humans": [
                    {"principal": "ivan", "display_name": "Ivan"},
                    {"principal": "judy", "permissions": ["approve"]},
                ]
            },
        )
        monkeypatch.setenv(HUMAN_IDENTITIES_FILE_ENV, str(path))
        mgr = IdentityManager()
        ivan = mgr.get_identity_by_principal("ivan")
        judy = mgr.get_identity_by_principal("judy")
        assert is_human_identity(ivan) is True
        assert is_human_identity(judy) is True
        assert judy.permissions == {"approve"}
        # A seed file must not be able to smuggle in a machine as a human.
        assert is_human_identity(mgr.get_identity("system")) is False

    def test_a_bare_list_is_tolerated(self, monkeypatch, tmp_path):
        path = self._write(tmp_path, [{"principal": "ken"}])
        monkeypatch.setenv(HUMAN_IDENTITIES_FILE_ENV, str(path))
        assert is_human_identity(IdentityManager().get_identity_by_principal("ken"))

    def test_a_malformed_file_loads_nothing_and_does_not_crash(
        self, monkeypatch, tmp_path
    ):
        # Fail-closed and loud rather than fatal: a config problem must not
        # take down every kernel consumer.
        path = tmp_path / "broken.json"
        path.write_text("{not json", encoding="utf-8")
        monkeypatch.setenv(HUMAN_IDENTITIES_FILE_ENV, str(path))
        mgr = IdentityManager()
        assert [i for i in mgr.list_identities() if is_human_identity(i)] == []

    def test_a_missing_file_loads_nothing_and_does_not_crash(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setenv(HUMAN_IDENTITIES_FILE_ENV, str(tmp_path / "nope.json"))
        mgr = IdentityManager()
        assert [i for i in mgr.list_identities() if is_human_identity(i)] == []

    def test_entries_without_a_principal_are_skipped(self, monkeypatch, tmp_path):
        path = self._write(tmp_path, {"humans": [{"display_name": "no principal"}]})
        monkeypatch.setenv(HUMAN_IDENTITIES_FILE_ENV, str(path))
        mgr = IdentityManager()
        assert [i for i in mgr.list_identities() if is_human_identity(i)] == []


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
