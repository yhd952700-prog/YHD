"""runtime state schema baseline

Revision ID: 006
Revises: 005
Create Date: 2026-11-04

Why this revision exists
------------------------
Migrations 001-005 declare the ORM-owned tables (``src/integrations/orm_models.py``).
Everything else the product persists was created *ad hoc* at runtime with
``CREATE TABLE IF NOT EXISTS`` inside the store that owns it (audit chain,
memory, conversations, identity, model registry, personal context, checkpoints,
provider metrics, execution journal, executor fence, revoked tokens, leases,
knowledge vectors).

That worked, but it made the real schema undiscoverable: ``alembic upgrade head``
produced a database that was *not* the schema the process actually uses, and
schema drift in those stores was invisible to every gate. This revision is the
baseline that declares them where migrations can be inspected and rolled back
from.

Honest boundary (do not overstate this):
  * These stores keep their own SQLite files (``audit_store.db``,
    ``memory_store.db``, ``conversation_store.db``, ...). This baseline declares
    the schema, not a relocation of those files -- a fresh deployment can now
    pre-create each store's tables from the migration graph, and the runtime
    ``CREATE TABLE IF NOT EXISTS`` remains a self-healing fallback (the audit
    store's write path is frozen while HC-01 is UNVERIFIED, so it must keep its
    in-transaction DDL).
  * The DDL below is copied verbatim from the owning modules on purpose. Holding
    a reference to a private ``_SCHEMA`` constant would couple a migration to
    refactors of live code; migrations are historical records and must not
    change meaning when a store is edited.

Data safety
-----------
``upgrade`` is additive and idempotent: it skips any table that already exists.
Every table it *does* create is recorded in ``alembic_baseline_tables``, and
``downgrade`` drops only those. A store file that already contains data can
therefore be pointed at this revision without losing a row -- the pre-existing
tables are never claimed (so never dropped).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Records which tables this revision actually created. ``downgrade`` reads it
#: so it never drops a table that the runtime (or an operator) created first.
_MARKER_TABLE = "alembic_baseline_tables"

_TABLE_DDL: Sequence[tuple[str, str]] = (
    # ---- audit chain (src/kernels/audit/__init__.py) -----------------------
    (
        "audit_events",
        """CREATE TABLE IF NOT EXISTS audit_events (
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
            seq INTEGER NOT NULL DEFAULT 0,
            hash_alg TEXT NOT NULL DEFAULT 'sha256',
            created_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
        )""",
    ),
    (
        "chain_state",
        """CREATE TABLE IF NOT EXISTS chain_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            last_seq INTEGER NOT NULL,
            last_hash TEXT
        )""",
    ),
    (
        "writer_lease",
        """CREATE TABLE IF NOT EXISTS writer_lease (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            token INTEGER NOT NULL,
            owner TEXT,
            acquired_at REAL,
            expires_at REAL,
            writer_id TEXT,
            writer_epoch INTEGER NOT NULL DEFAULT 0,
            boot_gen INTEGER,
            released_at REAL
        )""",
    ),
    (
        "verification_checkpoints",
        """CREATE TABLE IF NOT EXISTS verification_checkpoints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            start_seq INTEGER NOT NULL,
            end_seq INTEGER NOT NULL,
            start_link_hash TEXT,
            end_link_hash TEXT NOT NULL,
            end_event_hash TEXT NOT NULL,
            event_count INTEGER NOT NULL,
            verified_at REAL NOT NULL,
            method TEXT NOT NULL,
            UNIQUE(start_seq, end_seq)
        )""",
    ),
    (
        "dual_signature_evidence",
        """CREATE TABLE IF NOT EXISTS dual_signature_evidence (
            record_id  TEXT PRIMARY KEY,
            role       TEXT NOT NULL,
            sig_alg    TEXT NOT NULL,
            key_id     TEXT NOT NULL,
            claims_json TEXT NOT NULL,
            signature  TEXT NOT NULL,
            signed_at  TEXT NOT NULL
        )""",
    ),
    # ---- long-term memory (src/kernels/memory/store.py) --------------------
    (
        "memory_entries",
        """CREATE TABLE IF NOT EXISTS memory_entries (
            key_hash        TEXT PRIMARY KEY,
            entry_id        TEXT NOT NULL,
            key             TEXT NOT NULL,
            value           TEXT NOT NULL,
            tier            TEXT NOT NULL,
            scope           TEXT NOT NULL,
            created_at      TEXT NOT NULL,
            expires_at      TEXT,
            tags            TEXT,
            correlation_id  TEXT NOT NULL,
            access_count    INTEGER NOT NULL DEFAULT 0,
            last_accessed   TEXT,
            provenance      TEXT
        )""",
    ),
    # ---- conversations / identity / model registry -------------------------
    (
        "conversation_turns",
        """CREATE TABLE IF NOT EXISTS conversation_turns (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            principal_id TEXT NOT NULL,
            turn INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
        )""",
    ),
    (
        "human_identities",
        """CREATE TABLE IF NOT EXISTS human_identities (
            principal     TEXT PRIMARY KEY,
            display_name  TEXT,
            permissions   TEXT NOT NULL DEFAULT '[]',
            scope         TEXT NOT NULL DEFAULT 'L0',
            registered_at TEXT NOT NULL DEFAULT '',
            source        TEXT NOT NULL DEFAULT '',
            trust_score   REAL,
            integrity     TEXT
        )""",
    ),
    (
        "ai_model_configs",
        """CREATE TABLE IF NOT EXISTS ai_model_configs (
            id            TEXT PRIMARY KEY,
            name          TEXT NOT NULL UNIQUE,
            provider      TEXT NOT NULL,
            model         TEXT NOT NULL,
            base_url      TEXT,
            api_key_enc   TEXT,
            timeout       REAL NOT NULL DEFAULT 30.0,
            is_default    INTEGER NOT NULL DEFAULT 0,
            enabled       INTEGER NOT NULL DEFAULT 1,
            created_at    TEXT NOT NULL,
            updated_at    TEXT NOT NULL
        )""",
    ),
    # ---- personal context (src/ai/personal_context.py) ---------------------
    (
        "profile_preferences",
        """CREATE TABLE IF NOT EXISTS profile_preferences (
            principal_id TEXT NOT NULL,
            key TEXT NOT NULL,
            value_json TEXT NOT NULL,
            confidence REAL NOT NULL DEFAULT 0.5,
            source TEXT NOT NULL DEFAULT 'explicit',
            updated_at REAL NOT NULL,
            PRIMARY KEY (principal_id, key)
        )""",
    ),
    (
        "profile_facts",
        """CREATE TABLE IF NOT EXISTS profile_facts (
            principal_id TEXT NOT NULL,
            key TEXT NOT NULL,
            value_json TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'explicit',
            updated_at REAL NOT NULL,
            PRIMARY KEY (principal_id, key)
        )""",
    ),
    (
        "profile_lists",
        """CREATE TABLE IF NOT EXISTS profile_lists (
            principal_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            item TEXT NOT NULL,
            PRIMARY KEY (principal_id, kind, item)
        )""",
    ),
    (
        "profile_relationships",
        """CREATE TABLE IF NOT EXISTS profile_relationships (
            principal_id TEXT NOT NULL,
            who TEXT NOT NULL,
            relation TEXT NOT NULL,
            PRIMARY KEY (principal_id, who)
        )""",
    ),
    (
        "profile_meta",
        """CREATE TABLE IF NOT EXISTS profile_meta (
            principal_id TEXT NOT NULL,
            key TEXT NOT NULL,
            value_json TEXT NOT NULL,
            PRIMARY KEY (principal_id, key)
        )""",
    ),
    # ---- workflow checkpoints / provider metrics / knowledge ---------------
    (
        "checkpoints",
        """CREATE TABLE IF NOT EXISTS checkpoints (
            checkpoint_id TEXT PRIMARY KEY,
            workflow_id TEXT NOT NULL,
            node_id TEXT,
            edge_id TEXT,
            checkpoint_type TEXT NOT NULL DEFAULT 'node',
            workflow_state TEXT NOT NULL,
            input_data TEXT,
            output_data TEXT,
            node_stack TEXT,
            current_node TEXT,
            remaining_edges TEXT,
            done INTEGER DEFAULT 0,
            error TEXT,
            created_at REAL NOT NULL,
            expires_at REAL,
            status TEXT NOT NULL DEFAULT 'active',
            description TEXT,
            tags TEXT
        )""",
    ),
    (
        "provider_metric_samples",
        """CREATE TABLE IF NOT EXISTS provider_metric_samples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            latency_ms REAL NOT NULL,
            success_rate REAL NOT NULL
        )""",
    ),
    (
        "knowledge_vectors",
        """CREATE TABLE IF NOT EXISTS knowledge_vectors (
            doc_id TEXT PRIMARY KEY,
            embedding TEXT NOT NULL,
            metadata TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""",
    ),
    # ---- execution journal / fence / revoked tokens ------------------------
    (
        "journal_events",
        """CREATE TABLE IF NOT EXISTS journal_events (
            seq           INTEGER PRIMARY KEY AUTOINCREMENT,
            execution_id  TEXT NOT NULL,
            event_type    TEXT NOT NULL,
            task_name     TEXT,
            ts            TEXT NOT NULL,
            payload       TEXT
        )""",
    ),
    (
        "executor_fence",
        """CREATE TABLE IF NOT EXISTS executor_fence (
            executor_id        TEXT PRIMARY KEY,
            token              INTEGER NOT NULL,
            epoch              INTEGER NOT NULL DEFAULT 0,
            owner              TEXT,
            acquired_at        REAL,
            expires_at         REAL,
            last_heartbeat_at  REAL,
            boot_gen           INTEGER,
            granted_capabilities TEXT,
            released_at        REAL
        )""",
    ),
    (
        "fence_epoch",
        """CREATE TABLE IF NOT EXISTS fence_epoch (
            id            INTEGER PRIMARY KEY CHECK (id = 1),
            global_token_seq INTEGER NOT NULL DEFAULT 0,
            global_epoch     INTEGER NOT NULL DEFAULT 0
        )""",
    ),
    (
        "executor_fence_consumed",
        """CREATE TABLE IF NOT EXISTS executor_fence_consumed (
            executor_id   TEXT NOT NULL,
            token         INTEGER NOT NULL,
            correlation_id TEXT NOT NULL,
            PRIMARY KEY (executor_id, token, correlation_id)
        )""",
    ),
    (
        "revoked_tokens",
        """CREATE TABLE IF NOT EXISTS revoked_tokens (
            jti TEXT NOT NULL,
            kind TEXT NOT NULL,
            subject TEXT,
            expires_at REAL NOT NULL,
            PRIMARY KEY (jti, kind)
        )""",
    ),
    # ---- distribution coordination (src/distribution/coordination.py) -----
    (
        "leases",
        """CREATE TABLE IF NOT EXISTS leases (
            name                 TEXT PRIMARY KEY,
            holder               TEXT,
            token                INTEGER,
            acquired_at          REAL,
            expires_at           REAL,
            released_at          REAL,
            epoch                INTEGER NOT NULL DEFAULT 0,
            era                  INTEGER,
            granted_capabilities TEXT
        )""",
    ),
    (
        "meta",
        """CREATE TABLE IF NOT EXISTS meta (
            id           INTEGER PRIMARY KEY CHECK (id = 1),
            global_token INTEGER NOT NULL DEFAULT 0
        )""",
    ),
    (
        "consumed",
        """CREATE TABLE IF NOT EXISTS consumed (
            lease_name     TEXT NOT NULL,
            token          INTEGER NOT NULL,
            correlation_id TEXT NOT NULL,
            PRIMARY KEY (lease_name, token, correlation_id)
        )""",
    ),
)

_INDEX_DDL: Sequence[tuple[str, str, str]] = (
    ("idx_principal", "audit_events", "principal_id"),
    ("idx_scope", "audit_events", "scope"),
    ("idx_timestamp", "audit_events", "timestamp"),
    ("idx_correlation", "audit_events", "correlation_id"),
    ("idx_ckpt_end", "verification_checkpoints", "end_seq"),
    ("idx_memory_tier", "memory_entries", "tier"),
    ("idx_memory_scope", "memory_entries", "scope"),
    ("idx_memory_key", "memory_entries", "key"),
    ("idx_conv_principal", "conversation_turns", "principal_id, turn, seq"),
    ("idx_journal_exec", "journal_events", "execution_id, seq"),
    ("idx_revoked_expires", "revoked_tokens", "expires_at"),
)


def _existing_tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    bind = op.get_bind()
    existing = _existing_tables()

    op.execute(sa.text(
        f"CREATE TABLE IF NOT EXISTS {_MARKER_TABLE} (table_name TEXT PRIMARY KEY)"
    ))

    created: set[str] = set()
    for name, ddl in _TABLE_DDL:
        if name in existing:
            # Pre-existing (runtime-created or operator-created): leave it alone
            # and do NOT claim ownership -- downgrade must not delete its data.
            continue
        op.execute(sa.text(ddl))
        created.add(name)
        bind.execute(
            sa.text(f"INSERT INTO {_MARKER_TABLE} (table_name) VALUES (:name)"),
            {"name": name},
        )

    for name, table, columns in _INDEX_DDL:
        # Indexes are safe to (re)create: IF NOT EXISTS, and SQLite drops them
        # with their table, so no ownership bookkeeping is required. A table that
        # pre-existed with an older column set (e.g. a legacy store file) must
        # not fail the migration -- skip the index instead of guessing.
        if table in existing or table in created:
            present_columns = {
                col["name"]
                for col in sa.inspect(bind).get_columns(table)
            }
            if any(col.strip() not in present_columns
                   for col in columns.split(",")):
                continue
        op.execute(sa.text(
            f"CREATE INDEX IF NOT EXISTS {name} ON {table}({columns})"
        ))


def downgrade() -> None:
    bind = op.get_bind()
    if _MARKER_TABLE not in _existing_tables():
        return

    owned = [
        row[0]
        for row in bind.execute(
            sa.text(f"SELECT table_name FROM {_MARKER_TABLE}")
        ).fetchall()
    ]
    for name in owned:
        op.execute(sa.text(f'DROP TABLE IF EXISTS "{name}"'))
    op.execute(sa.text(f"DROP TABLE IF EXISTS {_MARKER_TABLE}"))
