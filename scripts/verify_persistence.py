"""
Cross-session persistence proof for liuhao_ai_os.db:
1. Write a row via one engine/session.
2. Dispose it completely.
3. Open a brand-new engine + session (new connection) and read the row back.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session as _S  # noqa: E402
from src.integrations.orm_models import APIKey, MemoryItem  # noqa: E402

DB_URL = os.environ.get("DATABASE_URL", "sqlite:///./liuhao_ai_os.db")

# --- Session 1: write ---
e1 = create_engine(DB_URL, connect_args={"check_same_thread": False})
s1 = e1.connect()
sess1 = _S(bind=s1)
k = APIKey(name="persist-probe", key_hash="probe-hash", encrypted_key="x", scopes='["read"]', status="active")
m = MemoryItem(content="persist-content", tier="semantic", user_id="probe-user")
sess1.add_all([k, m])
sess1.commit()
kid = k.id
mid = m.id
sess1.close()
s1.close()
e1.dispose()  # fully release connection -> simulates app restart

# --- Session 2: read from a fresh engine (new connection) ---
e2 = create_engine(DB_URL, connect_args={"check_same_thread": False})
s2 = e2.connect()
sess2 = _S(bind=s2)
k2 = sess2.get(APIKey, kid)
m2 = sess2.get(MemoryItem, mid)
print("=== CROSS-SESSION PERSISTENCE ===")
print("APIKey read back:", k2.name if k2 else None, "| scopes:", k2.scopes if k2 else None)
print("MemoryItem read back:", m2.content if m2 else None, "| user_id:", m2.user_id if m2 else None)
ok = k2 is not None and k2.name == "persist-probe" and m2 is not None and m2.content == "persist-content"
print("RESULT:", "PERSISTED OK" if ok else "FAILED")

# cleanup -- tolerate a failed read-back so the exit status carries the verdict
# instead of an AttributeError (see tests/test_guardrail_scripts.py).
if k2 is not None:
    sess2.delete(k2)
if m2 is not None:
    sess2.delete(m2)
sess2.commit()
sess2.close()
s2.close()
e2.dispose()

sys.exit(0 if ok else 1)
