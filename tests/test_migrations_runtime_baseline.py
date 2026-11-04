"""Migration baseline for the *runtime-owned* schema (alembic revision 006).

Migrations 001-005 covered the ORM tables. Everything the product actually
persists at runtime -- the audit chain, memory, conversations, identity, model
registry, personal context, checkpoints, provider metrics, execution journal,
executor fence, revoked tokens, leases, knowledge vectors -- was created ad hoc
by ``CREATE TABLE IF NOT EXISTS`` inside its store. ``alembic upgrade head``
therefore never produced the schema the process uses.

These tests run the real alembic command against a throw-away SQLite file (never
a live store) and pin the three properties that make the baseline safe:

1. ``upgrade head`` creates every runtime table and records it as owned;
2. a table that already existed (with data) is *not* claimed, so
   ``downgrade`` cannot delete it;
3. ``downgrade base`` removes only the tables the revision created, leaving the
   pre-existing data intact.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
ALEMBIC_INI = REPO / "alembic.ini"
VERSIONS = REPO / "alembic" / "versions"

#: Every table the baseline must declare. Kept explicit (not derived from the
#: migration) so deleting a declaration from 006 fails here instead of silently
#: shrinking the baseline.
RUNTIME_TABLES = {
    "audit_events",
    "chain_state",
    "writer_lease",
    "verification_checkpoints",
    "dual_signature_evidence",
    "memory_entries",
    "conversation_turns",
    "human_identities",
    "ai_model_configs",
    "profile_preferences",
    "profile_facts",
    "profile_lists",
    "profile_relationships",
    "profile_meta",
    "checkpoints",
    "provider_metric_samples",
    "knowledge_vectors",
    "journal_events",
    "executor_fence",
    "fence_epoch",
    "executor_fence_consumed",
    "revoked_tokens",
    "leases",
    "meta",
    "consumed",
}


def _alembic_config(db_path: Path):
    from alembic.config import Config

    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(REPO / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path.as_posix()}")
    return cfg


@pytest.fixture()
def db_path(tmp_path, monkeypatch) -> Path:
    """Isolate alembic's target DB; env.py prefers DATABASE_URL over config."""
    path = tmp_path / "migration_scratch.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path.as_posix()}")
    return path


def _upgrade(db_path: Path, revision: str = "head") -> None:
    from alembic import command

    command.upgrade(_alembic_config(db_path), revision)


def _downgrade(db_path: Path, revision: str) -> None:
    from alembic import command

    command.downgrade(_alembic_config(db_path), revision)


def _tables(db_path: Path) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        return {row[0] for row in conn.execute(
            "select name from sqlite_master where type='table'")}


def _marker(db_path: Path) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        return {row[0] for row in conn.execute(
            "select table_name from alembic_baseline_tables")}


def test_upgrade_head_creates_the_runtime_schema_and_claims_it(db_path):
    _upgrade(db_path)

    present = _tables(db_path)
    missing = RUNTIME_TABLES - present
    assert not missing, f"revision 006 did not create: {sorted(missing)}"
    assert RUNTIME_TABLES <= _marker(db_path), (
        "every table 006 created must be recorded as owned, otherwise "
        "downgrade cannot tell it apart from a runtime-created table")


def test_revision_chain_has_exactly_one_head():
    revisions = {}
    for path in sorted(VERSIONS.glob("[0-9][0-9][0-9]_*.py")):
        text = path.read_text(encoding="utf-8")
        rev = re.search(r"revision:\s*str\s*=\s*['\"]([^'\"]+)['\"]", text)
        down = re.search(r"down_revision:\s*Union\[str,\s*None\]\s*=\s*['\"]([^'\"]+)['\"]", text)
        assert rev, f"{path.name} has no revision id"
        revisions[rev.group(1)] = down.group(1) if down else None

    assert "006" in revisions, "the runtime baseline revision is missing"
    downs = {down for down in revisions.values() if down}
    heads = set(revisions) - downs
    assert heads == {"006"}, (
        "alembic requires a single head for `upgrade head`; got %s" % sorted(heads))
    for rev, down in revisions.items():
        if down is not None:
            assert down in revisions, f"revision {rev} points at unknown parent {down}"


def test_preexisting_table_is_not_claimed_and_survives_downgrade(db_path):
    """A store file that already holds data must never lose it to a downgrade."""
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE memory_entries (key_hash TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO memory_entries (key_hash, value) VALUES ('k', 'v')")
        conn.commit()

    _upgrade(db_path)

    assert "memory_entries" not in _marker(db_path), (
        "006 claimed a table it did not create -- downgrade would drop real data")
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("select count(*) from memory_entries").fetchone()[0] == 1

    _downgrade(db_path, "base")

    present = _tables(db_path)
    assert "memory_entries" in present, "pre-existing table must not be dropped"
    assert "audit_events" not in present, "baseline-owned table should be dropped"
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("select key_hash, value from memory_entries").fetchall()
    assert rows == [("k", "v")], "pre-existing data must survive downgrade"


def test_downgrade_to_005_leaves_only_the_orm_schema(db_path):
    _upgrade(db_path)
    _downgrade(db_path, "005")

    assert not (RUNTIME_TABLES & _tables(db_path)), (
        "downgrading past 006 must remove the tables it created")
    assert "alembic_baseline_tables" not in _tables(db_path)


def test_upgrade_is_idempotent(db_path):
    _upgrade(db_path)
    _upgrade(db_path)  # alembic no-ops at head; must not raise

    assert RUNTIME_TABLES <= _tables(db_path)


def test_docker_image_ships_the_migration_graph_and_applies_it_before_start():
    dockerfile = (REPO / "Dockerfile").read_text(encoding="utf-8")

    assert "COPY --chown=app:app alembic.ini ./alembic.ini" in dockerfile
    assert "COPY --chown=app:app alembic/ ./alembic/" in dockerfile

    cmd = re.search(r'CMD\s*\[\s*(.+?)\s*\]\s*$', dockerfile, re.S)
    assert cmd, "Dockerfile has no CMD"
    assert "alembic" in cmd.group(1) and "upgrade" in cmd.group(1), (
        "the container must apply migrations before serving; got %s" % cmd.group(1))
    assert "src.gateway" in cmd.group(1)
