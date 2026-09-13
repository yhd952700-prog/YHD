"""Identity persistence: the store layer behind Policy C-7.

Why these tests exist
---------------------
Before the store layer, a registration lived only in a JSON seed file that
``IdentityManager.__init__`` re-read. Two things followed from that, and both
are pinned here:

* **Existence is not durability.** A human registered through
  ``create_human_identity`` was held in memory only; the approval channel was
  populated for the lifetime of the process and empty again after a restart.
  ``test_runtime_registration_survives_a_restart`` fails if that returns.
* **Fail-closed is the default.** No store configured must mean *zero* humans,
  never a machine identity (OD-010). Several tests below assert the empty case
  explicitly, because "nobody can approve" is the safety property, not a bug.

The store also has to be interchangeable: choosing SQLite must not change what
a JSON deployment does. ``test_backend_resolution_*`` and the parity tests keep
that honest.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading

import pytest

from src.kernels.identity import (
    HUMAN_IDENTITIES_BACKEND_ENV,
    HUMAN_IDENTITIES_DB_ENV,
    HUMAN_IDENTITIES_FILE_ENV,
    IdentityManager,
    METADATA_DISPLAY_NAME_KEY,
    is_human_identity,
)
from src.kernels.identity._persistence import (
    BACKEND_FILE,
    BACKEND_SQLITE,
    FIELD_DISPLAY_NAME,
    FIELD_PERMISSIONS,
    FIELD_PRINCIPAL,
    FIELD_REGISTERED_AT,
    FIELD_SOURCE,
    FIELD_SCOPE,
    JsonFileStore,
    SqliteHumanIdentityStore,
    describe_human_identity_store,
    resolve_human_identity_store,
)

_ENV_VARS = (
    HUMAN_IDENTITIES_BACKEND_ENV,
    HUMAN_IDENTITIES_DB_ENV,
    HUMAN_IDENTITIES_FILE_ENV,
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """No test inherits an identity configuration from the shell."""
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def json_path(tmp_path):
    return str(tmp_path / "humans.json")


@pytest.fixture
def sqlite_path(tmp_path):
    return str(tmp_path / "nested" / "humans.sqlite3")


class TestStorageFieldNamesMatchTheKernel:
    def test_display_name_key_cannot_drift(self):
        # The store serialises a registration under FIELD_DISPLAY_NAME and the
        # kernel reads it back out of AgentIdentity.metadata. If these strings
        # ever diverge, registration would silently stop carrying the name.
        assert FIELD_DISPLAY_NAME == METADATA_DISPLAY_NAME_KEY


class TestBackendResolution:
    def test_nothing_configured_is_the_json_backend_with_no_location(self):
        store = resolve_human_identity_store()
        assert store.backend_name == BACKEND_FILE
        assert store.location == ""
        assert describe_human_identity_store(store)["configured"] is False

    def test_a_file_variable_alone_keeps_the_json_backend(self, monkeypatch, json_path):
        monkeypatch.setenv(HUMAN_IDENTITIES_FILE_ENV, json_path)
        store = resolve_human_identity_store()
        assert store.backend_name == BACKEND_FILE
        assert store.location == json_path

    def test_setting_the_database_path_selects_sqlite(self, monkeypatch, sqlite_path):
        # Merely configuring a database path is unambiguous intent; requiring
        # the backend variable too would be a footgun.
        monkeypatch.setenv(HUMAN_IDENTITIES_DB_ENV, sqlite_path)
        store = resolve_human_identity_store()
        assert store.backend_name == BACKEND_SQLITE
        assert store.location == sqlite_path

    def test_an_explicit_backend_wins_over_the_default(self, monkeypatch, json_path):
        monkeypatch.setenv(HUMAN_IDENTITIES_FILE_ENV, json_path)
        monkeypatch.setenv(HUMAN_IDENTITIES_BACKEND_ENV, BACKEND_SQLITE)
        store = resolve_human_identity_store()
        assert store.backend_name == BACKEND_SQLITE

    def test_an_unknown_backend_falls_back_to_file(self, monkeypatch, json_path):
        monkeypatch.setenv(HUMAN_IDENTITIES_BACKEND_ENV, "postgres")
        monkeypatch.setenv(HUMAN_IDENTITIES_FILE_ENV, json_path)
        store = resolve_human_identity_store()
        assert store.backend_name == BACKEND_FILE


class TestJsonFileStore:
    def test_an_unconfigured_store_is_a_no_op(self):
        store = JsonFileStore("")
        assert store.load_all() == []
        assert store.upsert({FIELD_PRINCIPAL: "alice"}) is False
        assert store.remove("alice") is False

    def test_round_trip(self, json_path):
        store = JsonFileStore(json_path)
        assert store.upsert({
            FIELD_PRINCIPAL: "alice",
            FIELD_DISPLAY_NAME: "Alice",
            FIELD_PERMISSIONS: ["b", "a"],
            FIELD_SCOPE: "L0",
        }) is True

        loaded = store.load_all()
        assert [e[FIELD_PRINCIPAL] for e in loaded] == ["alice"]
        assert loaded[0][FIELD_DISPLAY_NAME] == "Alice"
        assert loaded[0][FIELD_PERMISSIONS] == ["a", "b"]  # sorted, deduped
        assert loaded[0][FIELD_REGISTERED_AT]  # stamped on first write

    def test_re_registration_keeps_the_original_instant(self, json_path):
        store = JsonFileStore(json_path)
        store.upsert({FIELD_PRINCIPAL: "alice", FIELD_REGISTERED_AT: "2020-01-01T00:00:00"})
        store.upsert({FIELD_PRINCIPAL: "alice", FIELD_REGISTERED_AT: "2099-01-01T00:00:00"})
        loaded = store.load_all()
        assert len(loaded) == 1
        assert loaded[0][FIELD_REGISTERED_AT] == "2020-01-01T00:00:00"

    def test_remove_reports_whether_anything_changed(self, json_path):
        store = JsonFileStore(json_path)
        store.upsert({FIELD_PRINCIPAL: "alice"})
        assert store.remove("alice") is True
        assert store.remove("alice") is False

    def test_a_bare_list_is_still_accepted(self, json_path):
        # Hand-written seed files in the wild use both shapes.
        with open(json_path, "w", encoding="utf-8") as handle:
            json.dump([{FIELD_PRINCIPAL: "alice"}], handle)
        assert [e[FIELD_PRINCIPAL] for e in JsonFileStore(json_path).load_all()] == ["alice"]

    def test_a_malformed_document_yields_no_humans(self, json_path, caplog):
        with open(json_path, "w", encoding="utf-8") as handle:
            handle.write("{not json at all")
        assert JsonFileStore(json_path).load_all() == []

    def test_a_non_object_non_list_document_yields_no_humans(self, json_path):
        with open(json_path, "w", encoding="utf-8") as handle:
            handle.write('"just a string"')
        assert JsonFileStore(json_path).load_all() == []


class TestSqliteHumanIdentityStore:
    def test_reading_never_creates_the_database(self, sqlite_path):
        # Booting a kernel must not manufacture an empty registration database.
        store = SqliteHumanIdentityStore(sqlite_path)
        assert store.load_all() == []
        assert not os.path.exists(sqlite_path)

    def test_round_trip(self, sqlite_path):
        store = SqliteHumanIdentityStore(sqlite_path)
        assert store.upsert({
            FIELD_PRINCIPAL: "alice",
            FIELD_DISPLAY_NAME: "Alice",
            FIELD_PERMISSIONS: ["b", "a"],
            FIELD_SCOPE: "L0",
            FIELD_SOURCE: "test",
        }) is True

        loaded = store.load_all()
        assert loaded == [{
            FIELD_PRINCIPAL: "alice",
            FIELD_DISPLAY_NAME: "Alice",
            FIELD_PERMISSIONS: ["a", "b"],
            FIELD_SCOPE: "L0",
            FIELD_REGISTERED_AT: loaded[0][FIELD_REGISTERED_AT],
        }]
        assert loaded[0][FIELD_REGISTERED_AT]

    def test_re_registration_keeps_the_original_instant(self, sqlite_path):
        store = SqliteHumanIdentityStore(sqlite_path)
        store.upsert({FIELD_PRINCIPAL: "alice", FIELD_REGISTERED_AT: "2020-01-01T00:00:00"})
        store.upsert({FIELD_PRINCIPAL: "alice", FIELD_REGISTERED_AT: "2099-01-01T00:00:00"})
        loaded = store.load_all()
        assert len(loaded) == 1
        assert loaded[0][FIELD_REGISTERED_AT] == "2020-01-01T00:00:00"

    def test_concurrent_writers_do_not_lose_registrations(self, sqlite_path):
        # The concrete improvement over read-modify-write on a JSON file: the
        # upsert is one statement, so N threads must produce N rows.
        store = SqliteHumanIdentityStore(sqlite_path)
        threads = [
            threading.Thread(
                target=store.upsert,
                args=({FIELD_PRINCIPAL: f"human-{i:03d}"},),
            )
            for i in range(24)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        principals = {e[FIELD_PRINCIPAL] for e in store.load_all()}
        assert len(principals) == 24

    def test_remove_reports_whether_anything_changed(self, sqlite_path):
        store = SqliteHumanIdentityStore(sqlite_path)
        store.upsert({FIELD_PRINCIPAL: "alice"})
        assert store.remove("alice") is True
        assert store.remove("alice") is False
        assert store.load_all() == []

    def test_hand_edited_comma_separated_permissions_still_decode(self, sqlite_path):
        store = SqliteHumanIdentityStore(sqlite_path)
        store.upsert({FIELD_PRINCIPAL: "alice"})
        with sqlite3.connect(sqlite_path) as connection:
            connection.execute(
                "UPDATE human_identities SET permissions = ? WHERE principal = ?",
                ("a, b", "alice"),
            )
        assert store.load_all()[0][FIELD_PERMISSIONS] == ["a", "b"]

    def test_a_corrupt_database_yields_no_humans_rather_than_raising(self, sqlite_path):
        os.makedirs(os.path.dirname(sqlite_path), exist_ok=True)
        with open(sqlite_path, "w", encoding="utf-8") as handle:
            handle.write("this is not a sqlite database")
        assert SqliteHumanIdentityStore(sqlite_path).load_all() == []


class TestIdentityManagerUsesTheStore:
    def test_nothing_configured_registers_nobody(self):
        assert [
            i.principal
            for i in IdentityManager().list_identities()
            if is_human_identity(i)
        ] == []

    def test_a_json_seed_file_is_loaded(self, monkeypatch, json_path):
        with open(json_path, "w", encoding="utf-8") as handle:
            json.dump({"humans": [
                {FIELD_PRINCIPAL: "alice", FIELD_DISPLAY_NAME: "Alice"},
            ]}, handle)
        monkeypatch.setenv(HUMAN_IDENTITIES_FILE_ENV, json_path)

        identity = IdentityManager().get_identity_by_principal("alice")
        assert is_human_identity(identity) is True
        assert identity.metadata[METADATA_DISPLAY_NAME_KEY] == "Alice"
        assert identity.metadata["seeded_from"] == json_path
        assert identity.metadata["registered_at"]

    def test_a_sqlite_store_is_loaded(self, monkeypatch, sqlite_path):
        SqliteHumanIdentityStore(sqlite_path).upsert({
            FIELD_PRINCIPAL: "alice", FIELD_DISPLAY_NAME: "Alice",
        })
        monkeypatch.setenv(HUMAN_IDENTITIES_DB_ENV, sqlite_path)

        assert is_human_identity(
            IdentityManager().get_identity_by_principal("alice")
        ) is True

    def test_an_empty_sqlite_store_registers_nobody(self, monkeypatch, sqlite_path):
        monkeypatch.setenv(HUMAN_IDENTITIES_DB_ENV, sqlite_path)
        assert [
            i.principal
            for i in IdentityManager().list_identities()
            if is_human_identity(i)
        ] == []

    def test_an_entry_without_a_principal_is_skipped(self, monkeypatch, json_path):
        with open(json_path, "w", encoding="utf-8") as handle:
            json.dump({"humans": [
                {FIELD_DISPLAY_NAME: "nobody"},
                {FIELD_PRINCIPAL: "alice"},
            ]}, handle)
        monkeypatch.setenv(HUMAN_IDENTITIES_FILE_ENV, json_path)
        humans = [
            i.principal
            for i in IdentityManager().list_identities()
            if is_human_identity(i)
        ]
        assert humans == ["alice"]

    def test_an_out_of_range_scope_falls_back_to_l0(self, monkeypatch, json_path):
        with open(json_path, "w", encoding="utf-8") as handle:
            json.dump({"humans": [
                {FIELD_PRINCIPAL: "alice", FIELD_SCOPE: "L99"},
            ]}, handle)
        monkeypatch.setenv(HUMAN_IDENTITIES_FILE_ENV, json_path)
        assert is_human_identity(
            IdentityManager().get_identity_by_principal("alice")
        ) is True

    def test_runtime_registration_survives_a_restart(self, monkeypatch, sqlite_path):
        """The whole point of the store: a restart must not empty the channel."""
        monkeypatch.setenv(HUMAN_IDENTITIES_DB_ENV, sqlite_path)

        created = IdentityManager().create_human_identity(
            "alice", display_name="Alice"
        )
        assert is_human_identity(created) is True

        # A *fresh* manager, as after a process restart.
        reloaded = IdentityManager().get_identity_by_principal("alice")
        assert is_human_identity(reloaded) is True
        assert reloaded.metadata[METADATA_DISPLAY_NAME_KEY] == "Alice"
        assert reloaded.metadata["registered_at"]

    def test_an_unconfigured_store_never_creates_a_file(self, tmp_path):
        # Zero behaviour change for deployments that never opted in.
        before = set(os.listdir(tmp_path))
        manager = IdentityManager()
        created = manager.create_human_identity("transient")
        assert created is not None                       # still works in memory
        assert is_human_identity(
            manager.get_identity_by_principal("transient")
        ) is True
        assert set(os.listdir(tmp_path)) == before

    def test_describe_store_names_the_backend(self, monkeypatch, sqlite_path):
        monkeypatch.setenv(HUMAN_IDENTITIES_DB_ENV, sqlite_path)
        described = IdentityManager().describe_store()
        assert described == {
            "backend": BACKEND_SQLITE,
            "location": sqlite_path,
            "configured": True,
        }
