"""Disaster-recovery backup: the restore path has to be able to FAIL.

These exist because the restore path used to be unable to: it opened the file,
dropped what it parsed, and reported ``success=True`` for anything with a
matching filename -- including a backup whose payload someone edited. A recovery
subsystem that always succeeds is indistinguishable from one that does nothing,
right up until the incident where you find out which it was.

Each test below therefore asserts the honest outcome, including the failure
cases, and asserts that the restored DATA comes back rather than just a flag.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

from src.sre.disaster.backup import (
    BackupManager,
    RecoveryManager,
    compute_integrity_hash,
)


@pytest.fixture
def managers(tmp_path):
    backup_dir = str(tmp_path / "backups")
    return BackupManager(backup_dir=backup_dir), RecoveryManager(
        backup_dir=backup_dir
    )


def _payload():
    return {"audit_seq": 317383, "actors": ["boss", "ceo"]}


def _snapshot():
    return {"sqlite": "audit_store.db", "sha256": "a" * 64}


def _write_raw(backup_dir: str, backup_id: str, document: bytes) -> str:
    os.makedirs(backup_dir, exist_ok=True)
    path = os.path.join(backup_dir, f"backup_{backup_id}.json")
    with open(path, "wb") as handle:
        handle.write(document)
    return path


class TestRoundTrip:
    def test_a_backup_restores_the_data_that_was_written(self, managers):
        backup_mgr, recovery_mgr = managers
        record = backup_mgr.create_backup(_payload(), _snapshot())

        outcome = recovery_mgr.restore(record.backup_id)

        assert outcome.success is True
        assert outcome.error is None
        assert outcome.payload == _payload()
        assert outcome.resource_snapshot == _snapshot()
        assert outcome.backup_id == record.backup_id

    def test_two_backups_of_the_same_data_are_independently_restorable(
        self, managers
    ):
        backup_mgr, recovery_mgr = managers
        first = backup_mgr.create_backup(_payload(), _snapshot())
        second = backup_mgr.create_backup(_payload(), _snapshot())

        assert first.backup_id != second.backup_id
        for backup_id in (first.backup_id, second.backup_id):
            assert recovery_mgr.restore(backup_id).success is True

    def test_the_stored_hash_is_the_one_the_verifier_computes(self, managers):
        backup_mgr, _ = managers
        record = backup_mgr.create_backup(_payload(), _snapshot())

        assert record.integrity_hash == compute_integrity_hash(
            _payload(), _snapshot()
        )
        path = os.path.join(
            backup_mgr.backup_dir, f"backup_{record.backup_id}.json"
        )
        with open(path, "r") as handle:
            assert json.load(handle)["integrity_hash"] == record.integrity_hash


class TestRestoreRefusesBadBackups:
    def test_a_tampered_payload_is_refused(self, managers):
        backup_mgr, recovery_mgr = managers
        record = backup_mgr.create_backup(_payload(), _snapshot())
        path = os.path.join(
            backup_mgr.backup_dir, f"backup_{record.backup_id}.json"
        )
        with open(path, "r") as handle:
            stored = json.load(handle)
        stored["payload"]["actors"] = ["attacker"]
        with open(path, "w") as handle:
            json.dump(stored, handle)

        outcome = recovery_mgr.restore(record.backup_id)

        assert outcome.success is False
        assert "mismatch" in (outcome.error or "").lower()
        # Refusing means refusing: nothing half-restored.
        assert outcome.payload is None

    def test_a_tampered_resource_snapshot_is_refused(self, managers):
        backup_mgr, recovery_mgr = managers
        record = backup_mgr.create_backup(_payload(), _snapshot())
        path = os.path.join(
            backup_mgr.backup_dir, f"backup_{record.backup_id}.json"
        )
        with open(path, "r") as handle:
            stored = json.load(handle)
        stored["resource_snapshot"]["sha256"] = "b" * 64
        with open(path, "w") as handle:
            json.dump(stored, handle)

        outcome = recovery_mgr.restore(record.backup_id)

        assert outcome.success is False
        assert outcome.payload is None

    def test_a_backup_with_no_hash_is_refused_rather_than_trusted(self, managers):
        backup_mgr, recovery_mgr = managers
        record = backup_mgr.create_backup(_payload(), _snapshot())
        path = os.path.join(
            backup_mgr.backup_dir, f"backup_{record.backup_id}.json"
        )
        with open(path, "r") as handle:
            stored = json.load(handle)
        del stored["integrity_hash"]
        with open(path, "w") as handle:
            json.dump(stored, handle)

        outcome = recovery_mgr.restore(record.backup_id)

        assert outcome.success is False
        assert "integrity hash" in (outcome.error or "").lower()

    def test_a_missing_backup_reports_which_path_it_looked_at(self, managers):
        _, recovery_mgr = managers

        outcome = recovery_mgr.restore("no-such-backup")

        assert outcome.success is False
        assert "not found" in (outcome.error or "").lower()
        assert "no-such-backup" in (outcome.error or "")

    def test_unparsable_json_is_reported_instead_of_raised(self, managers):
        backup_mgr, recovery_mgr = managers
        _write_raw(backup_mgr.backup_dir, "rotted", b"{not json")

        outcome = recovery_mgr.restore("rotted")

        assert outcome.success is False
        assert outcome.error

    def test_a_non_object_backup_is_refused(self, managers):
        backup_mgr, recovery_mgr = managers
        _write_raw(
            backup_mgr.backup_dir, "list-shaped", b'[{"payload": {}}]'
        )

        outcome = recovery_mgr.restore("list-shaped")

        assert outcome.success is False
        assert "not a JSON object" in (outcome.error or "")

    def test_a_truncated_file_is_refused(self, managers):
        backup_mgr, recovery_mgr = managers
        record = backup_mgr.create_backup(_payload(), _snapshot())
        path = os.path.join(
            backup_mgr.backup_dir, f"backup_{record.backup_id}.json"
        )
        with open(path, "rb") as handle:
            content = handle.read()
        with open(path, "wb") as handle:
            handle.write(content[: len(content) // 2])

        outcome = recovery_mgr.restore(record.backup_id)

        assert outcome.success is False
        assert outcome.payload is None


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
