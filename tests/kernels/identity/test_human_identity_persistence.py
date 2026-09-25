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
    HUMAN_IDENTITIES_INTEGRITY_KEY_ENV,
    IdentityManager,
    METADATA_DISPLAY_NAME_KEY,
    is_human_identity,
)
from src.kernels.identity._persistence import (
    BACKEND_FILE,
    BACKEND_SQLITE,
    FIELD_DISPLAY_NAME,
    FIELD_INTEGRITY,
    FIELD_PERMISSIONS,
    FIELD_PRINCIPAL,
    FIELD_REGISTERED_AT,
    FIELD_SOURCE,
    FIELD_SCOPE,
    FIELD_TRUST_SCORE,
    JsonFileStore,
    SqliteHumanIdentityStore,
    DEFAULT_MAC_ALG,
    _decode_permissions,
    compute_row_tag,
    describe_human_identity_store,
    resolve_human_identity_store,
    tag_matches,
)


def _tagged(entry: "dict") -> "dict":
    """Stamp a seed row with the enforced test key so the store admits it.

    Mirrors what a registration tool would write: without a valid
    ``integrity`` tag the row is refused fail-closed (HC-11 / U6), so seed
    fixtures that expect admission must carry the tag.
    """
    entry[FIELD_INTEGRITY] = compute_row_tag(entry, TEST_INTEGRITY_KEY)
    return entry

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


# HC-11 / U6: the integrity key is now ENFORCED -- a registry without it refuses
# every row (fail-closed). These store-layer tests exercise the supported
# (key-configured) admission path, so they run with a fixed test key set. Tests
# that must observe the no-key fail-closed behaviour delete it explicitly.
TEST_INTEGRITY_KEY = "test-integrity-key"


@pytest.fixture(autouse=True)
def enforce_integrity_key(monkeypatch):
    monkeypatch.setenv(HUMAN_IDENTITIES_INTEGRITY_KEY_ENV, TEST_INTEGRITY_KEY)


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

    def test_a_bare_list_without_a_tag_is_refused(self, json_path):
        # Hand-written seed files in the wild use both shapes. With the integrity
        # key enforced (HC-11 / U6) a row carrying no authentication tag cannot be
        # verified, so it is refused -- the parse still happens, but the row is
        # not admitted into the sovereign set.
        with open(json_path, "w", encoding="utf-8") as handle:
            json.dump([{FIELD_PRINCIPAL: "alice"}], handle)
        store = JsonFileStore(json_path)
        assert store.load_all() == []
        assert store.last_load_report["rejected"] == ["alice"]

    def test_a_malformed_document_yields_no_humans(self, json_path, caplog):
        with open(json_path, "w", encoding="utf-8") as handle:
            handle.write("{not json at all")
        assert JsonFileStore(json_path).load_all() == []

    def test_a_non_object_non_list_document_yields_no_humans(self, json_path):
        with open(json_path, "w", encoding="utf-8") as handle:
            handle.write('"just a string"')
        assert JsonFileStore(json_path).load_all() == []


class TestFailClosedWithoutIntegrityKey:
    """HC-11 / U6: a registry with no integrity key refuses every row.

    The previous behaviour (``tag_matches`` returned ``True`` when the key was
    absent) silently admitted any attacker-writable row into the sovereign set.
    The contract now is fail-closed: no key -> zero admitted humans, the refused
    rows reported in ``rejected``. These tests pin that contract directly and
    make sure the "no key" branch can never drift back to "admit unverified".
    """

    def test_no_key_configured_refuses_a_tagged_row(self, monkeypatch, json_path):
        # Delete the key the autouse fixture set -- this test must observe the
        # un-configured deployment.
        monkeypatch.delenv(HUMAN_IDENTITIES_INTEGRITY_KEY_ENV, raising=False)
        # Even a perfectly good, correctly computed tag is refused: without the
        # key, verification CANNOT be performed, so admission is impossible.
        alice = _tagged({FIELD_PRINCIPAL: "alice"})
        with open(json_path, "w", encoding="utf-8") as handle:
            json.dump([alice], handle)
        store = JsonFileStore(json_path)
        assert store.load_all() == []
        assert store.last_load_report["rejected"] == ["alice"]
        assert store.last_load_report["integrity_enforced"] is False

    def test_no_key_configured_refuses_an_untagged_row(self, monkeypatch, json_path):
        monkeypatch.delenv(HUMAN_IDENTITIES_INTEGRITY_KEY_ENV, raising=False)
        with open(json_path, "w", encoding="utf-8") as handle:
            json.dump([{FIELD_PRINCIPAL: "alice"}], handle)
        store = JsonFileStore(json_path)
        assert store.load_all() == []
        assert store.last_load_report["rejected"] == ["alice"]

    def test_tag_matches_is_fail_closed_without_key(self):
        entry = {FIELD_PRINCIPAL: "alice"}
        # The "no key" branch must refuse regardless of whether a tag is present
        # (a missing tag, or a perfectly good one), because absence of a key
        # means absence of the ability to verify -- not "verified".
        assert tag_matches(entry, "", None) is False
        assert tag_matches(entry, "", f"{DEFAULT_MAC_ALG}:deadbeef") is False


class TestTamperDetection:
    """HC-11: a row whose authority-bearing fields were changed without a fresh
    tag must be refused -- this is the tamper-evidence the integrity tag exists
    for. ``_decode_permissions`` still tolerates comma syntax (covered by
    ``TestSqliteHumanIdentityStore``); what is no longer tolerated is a change
    that bypasses the authentication tag.
    """

    def test_a_permission_edit_without_a_fresh_tag_is_refused(self, sqlite_path):
        store = SqliteHumanIdentityStore(sqlite_path)
        store.upsert({FIELD_PRINCIPAL: "alice"})
        with sqlite3.connect(sqlite_path) as connection:
            # Hand-edit the permission set but leave the old tag in place.
            connection.execute(
                "UPDATE human_identities SET permissions = ? WHERE principal = ?",
                ('["b"]', "alice"),
            )
        # The stale tag no longer authenticates the edited row -> refused.
        assert store.load_all() == []
        assert store.last_load_report["rejected"] == ["alice"]

    def test_a_principal_spoof_without_a_fresh_tag_is_refused(self, sqlite_path):
        store = SqliteHumanIdentityStore(sqlite_path)
        store.upsert({FIELD_PRINCIPAL: "alice"})
        with sqlite3.connect(sqlite_path) as connection:
            # An attacker tries to clone alice's authentic tag onto a new,
            # attacker-chosen principal. The tag is keyed to the row contents,
            # so it cannot be reused across a changed principal.
            connection.execute(
                "INSERT INTO human_identities "
                "(principal, display_name, permissions, scope, registered_at, "
                " source, trust_score, integrity) "
                "SELECT 'mallory', display_name, permissions, scope, "
                " registered_at, source, trust_score, integrity "
                "FROM human_identities WHERE principal = 'alice'",
            )
        loaded = {e[FIELD_PRINCIPAL] for e in store.load_all()}
        # alice authenticates; mallory's copied tag does not -> only alice.
        assert loaded == {"alice"}
        assert store.last_load_report["rejected"] == ["mallory"]


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
        results = [None] * 24

        def register(index: int) -> None:
            results[index] = store.upsert(
                {FIELD_PRINCIPAL: f"human-{index:03d}"}
            )

        threads = [threading.Thread(target=register, args=(i,)) for i in range(24)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        # A lost registration must be LOUD, not a shrunken row count. `upsert`
        # signals failure by returning False, and this test previously ignored
        # the return values -- so a lossy write surfaced only as "23 rows".
        # Measured 2026-09-15: without the bounded retry this was 1-4 losses in
        # ~25-50% of runs, all "attempt to write a readonly database".
        assert results == [True] * 24, f"upserts reported failure: {results}"
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
            # A human hand-edits permissions in a DB browser to a
            # comma-separated string (the documented decode tolerance), then
            # re-stamps the row so it still authenticates under the enforced
            # integrity key (HC-11 / U6). A *silent* hand-edit -- one that does
            # not update the tag -- is now refused (see TestTamperDetection),
            # which is the whole point of the containment; this test only
            # preserves the "comma syntax still decodes" coverage.
            connection.execute(
                "UPDATE human_identities SET permissions = ? WHERE principal = ?",
                ("a, b", "alice"),
            )
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT principal, display_name, permissions, scope, trust_score "
                "FROM human_identities WHERE principal = ?", ("alice",)
            ).fetchone()
            entry = {
                FIELD_PRINCIPAL: row["principal"],
                FIELD_DISPLAY_NAME: row["display_name"],
                FIELD_PERMISSIONS: _decode_permissions(row["permissions"]),
                FIELD_SCOPE: row["scope"],
                FIELD_TRUST_SCORE: row["trust_score"],
            }
            connection.execute(
                "UPDATE human_identities SET integrity = ? WHERE principal = ?",
                (compute_row_tag(entry, TEST_INTEGRITY_KEY), "alice"),
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
        alice = _tagged({FIELD_PRINCIPAL: "alice", FIELD_DISPLAY_NAME: "Alice"})
        with open(json_path, "w", encoding="utf-8") as handle:
            json.dump({"humans": [alice]}, handle)
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
        alice = _tagged({FIELD_PRINCIPAL: "alice"})
        with open(json_path, "w", encoding="utf-8") as handle:
            json.dump({"humans": [
                {FIELD_DISPLAY_NAME: "nobody"},
                alice,
            ]}, handle)
        monkeypatch.setenv(HUMAN_IDENTITIES_FILE_ENV, json_path)
        humans = [
            i.principal
            for i in IdentityManager().list_identities()
            if is_human_identity(i)
        ]
        assert humans == ["alice"]

    def test_an_out_of_range_scope_falls_back_to_l0(self, monkeypatch, json_path):
        alice = _tagged({FIELD_PRINCIPAL: "alice", FIELD_SCOPE: "L99"})
        with open(json_path, "w", encoding="utf-8") as handle:
            json.dump({"humans": [alice]}, handle)
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
