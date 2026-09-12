"""
Verify ORM model (Base.metadata) vs a migrated SQLite DB schema.
Reports tables-only-in-model, tables-only-in-DB, and per-column type/nullable
divergences. Uses raw type strings so JSON vs TEXT mismatches are surfaced.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, inspect  # noqa: E402
from src.integrations.orm_models import Base  # noqa: E402

DB_URL = os.environ.get("DATABASE_URL", "sqlite:///./liuhao_ai_os.db")
engine = create_engine(DB_URL)
insp = inspect(engine)

model_tables = set(Base.metadata.tables.keys())
db_tables = set(insp.get_table_names())

print("=== DB URL:", DB_URL)
print("=== Model tables:", len(model_tables), "| DB tables:", len(db_tables))
print()

only_model = sorted(model_tables - db_tables)
only_db = sorted(db_tables - model_tables)
print("### Tables in MODEL but NOT in DB:", only_model)
print()
print("### Tables in DB but NOT in MODEL:", only_db)
print()

shared = sorted(model_tables & db_tables)
diverged = False
for tname in shared:
    model_cols = Base.metadata.tables[tname].columns
    try:
        db_cols = {c["name"]: c for c in insp.get_columns(tname)}
    except Exception as e:
        print(f"  [{tname}] ERROR inspecting: {e}")
        continue
    for mc in model_cols:
        if mc.name not in db_cols:
            diverged = True
            print(f"  [{tname}.{mc.name}] MODEL col missing in DB ({mc.type})")
            continue
        dbc = db_cols[mc.name]
        mt = str(mc.type).upper()
        dt = str(dbc["type"]).upper()
        if mt != dt:
            diverged = True
            print(f"  [{tname}.{mc.name}] TYPE mismatch model={mt} db={dt}")
        if mc.nullable != dbc["nullable"]:
            diverged = True
            print(f"  [{tname}.{mc.name}] NULLABLE mismatch model={mc.nullable} db={dbc['nullable']}")
    for dcname in db_cols:
        if dcname not in model_cols:
            diverged = True
            print(f"  [{tname}] DB col missing in MODEL: {dcname} ({db_cols[dcname]['type']})")

print()
print("=== DIVERGENCES FOUND:", "YES" if diverged else "NONE")
print("=== DONE ===")
# Gate: a divergence must fail the process. Without this, the script reports
# a problem that CI would never notice (see tests/test_guardrail_scripts.py).
sys.exit(1 if diverged else 0)
