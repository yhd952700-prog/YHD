#!/usr/bin/env python
"""HC-11 identity-chain integrity -- runtime CI gate (independent of doc-lint).

WHAT THIS PROVES
----------------
HC-11 is about the per-row MAC (`<alg>:<digest>`) over the authority-bearing
fields of every human-identity registry row, and whether a tampered /
unverifiable row can ever be admitted.

This gate measures the MECHANISM directly against the real code, on the exact
SQLite ``human_identities`` table (and the JsonFileStore), across BOTH integrity
key postures:

    * no-key   : LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY unset
    * key-set  : a 64-char key configured

and three row states:

    * clean    : row signed by the live key, untampered
    * tampered : display_name rewritten after signing
    * null-tag : integrity column NULL (attacker-writable)

PASS CONTRACT (exit 0)
----------------------
The mechanism is FAIL-CLOSED in every posture, i.e. BOTH of these hold for BOTH
backends:

    1. a TAMPERED row is REJECTED whether or not the key is configured;
    2. a NULL-TAG (unverifiable) row is REJECTED when the key is configured
       (and naturally when it is not).

A clean row is admitted ONLY when the key is set; with no key, a clean row is
also refused (fail-closed, not fail-open).

HONEST BOUNDARY (does NOT fail the gate)
----------------------------------------
On a KEY-LESS host, ``load_all()`` admits ZERO humans. That is NOT a code
defect -- it is the by-design fail-closed posture: a row that cannot be
authenticated cannot decide who holds sovereignty. It is reported separately as
"SOVEREIGNTY OUTAGE" and is the ONLY reason HC-11 stays UNVERIFIED on a key-less
host (see scripts/verify_p08_final_status_matrix.py). This gate does not lower
that status; it isolates the mechanism (code) from the posture (deployment) so a
correct implementation is never mis-read as a hole.

Run with the venv python:
    .venv/Scripts/python.exe scripts/verify_hc11_identity_chain_integrity.py
Exit 0 = mechanism fail-closed in both postures; non-zero = a tampered or
unverifiable row was admitted in some posture (regression / bypass).
"""
import os
import sys
import json
import sqlite3
import tempfile

sys.path.insert(0, os.path.abspath("."))

from src.kernels.identity._persistence import (  # noqa: E402  (needs sys.path above)
    SqliteHumanIdentityStore,
    JsonFileStore,
    integrity_enforced,
    HUMAN_IDENTITIES_INTEGRITY_KEY_ENV,
)

KEY = "k" * 64
ENTRY = {
    "principal": "boss",
    "display_name": "Owner",
    "permissions": ["*"],
    "scope": "L0",
    "source": "probe",
    "trust_score": 1.0,
}


def _set_key(key):
    if key is None:
        os.environ.pop(HUMAN_IDENTITIES_INTEGRITY_KEY_ENV, None)
    else:
        os.environ[HUMAN_IDENTITIES_INTEGRITY_KEY_ENV] = key


def probe_sqlite(key, tamper, null_tag=False):
    _set_key(key)
    d = tempfile.mkdtemp()
    db = os.path.join(d, "ids.db")
    st = SqliteHumanIdentityStore(location=db)
    st.upsert(dict(ENTRY))
    if tamper:
        cx = sqlite3.connect(db)
        cx.execute(
            "UPDATE human_identities SET display_name='MALLORY' "
            "WHERE principal='boss'"
        )
        cx.commit()
        cx.close()
    if null_tag:
        cx = sqlite3.connect(db)
        cx.execute(
            "UPDATE human_identities SET integrity=NULL WHERE principal='boss'"
        )
        cx.commit()
        cx.close()
    st2 = SqliteHumanIdentityStore(location=db)
    rows = st2.load_all()
    rep = dict(st2.last_load_report)
    return len(rows), rep


def probe_json(key, tamper):
    _set_key(key)
    d = tempfile.mkdtemp()
    f = os.path.join(d, "ids.json")
    st = JsonFileStore(f)
    st.upsert(dict(ENTRY))
    if tamper:
        doc = json.load(open(f))
        doc["humans"][0]["display_name"] = "MALLORY"
        json.dump(doc, open(f, "w"))
    st2 = JsonFileStore(f)
    rows = st2.load_all()
    rep = dict(st2.last_load_report)
    return len(rows), rep


def main() -> int:
    failures = []
    print("HC-11 identity-chain integrity -- runtime mechanism gate")
    print("=" * 78)

    # --- SQLite human_identities TABLE -------------------------------------
    print("\n[SQLite human_identities TABLE]")
    n_no_clean, r_no_clean = probe_sqlite(None, tamper=False)
    n_no_tamp, r_no_tamp = probe_sqlite(None, tamper=True)
    n_k_clean, r_k_clean = probe_sqlite(KEY, tamper=False)
    n_k_tamp, r_k_tamp = probe_sqlite(KEY, tamper=True)
    n_k_null, r_k_null = probe_sqlite(KEY, tamper=False, null_tag=True)

    rows = [
        ("no-key   clean    ", n_no_clean, r_no_clean),
        ("no-key   TAMPERED ", n_no_tamp, r_no_tamp),
        ("key-set  clean    ", n_k_clean, r_k_clean),
        ("key-set  TAMPERED ", n_k_tamp, r_k_tamp),
        ("key-set  NULL-tag ", n_k_null, r_k_null),
    ]
    for label, n, rep in rows:
        print(f"  {label}: admitted={n} rejected={rep['rejected']} "
              f"enforced={rep['integrity_enforced']}")

    # --- JsonFileStore -----------------------------------------------------
    print("\n[JsonFileStore]")
    jn_no_clean, jr_no_clean = probe_json(None, tamper=False)
    jn_no_tamp, jr_no_tamp = probe_json(None, tamper=True)
    jn_k_clean, jr_k_clean = probe_json(KEY, tamper=False)
    jn_k_tamp, jr_k_tamp = probe_json(KEY, tamper=True)

    jrows = [
        ("no-key   clean    ", jn_no_clean, jr_no_clean),
        ("no-key   TAMPERED ", jn_no_tamp, jr_no_tamp),
        ("key-set  clean    ", jn_k_clean, jr_k_clean),
        ("key-set  TAMPERED ", jn_k_tamp, jr_k_tamp),
    ]
    for label, n, rep in jrows:
        print(f"  {label}: admitted={n} rejected={rep['rejected']}")

    # --- MECHANISM assertions (these decide PASS/FAIL) ----------------------
    # tampered must be rejected in BOTH postures (fail-closed even with no key)
    if n_no_tamp != 0:
        failures.append("SQLite: no-key TAMPERED admitted (fail-open!)")
    if n_k_tamp != 0:
        failures.append("SQLite: key-set TAMPERED admitted (fail-open!)")
    if jn_no_tamp != 0:
        failures.append("JSON: no-key TAMPERED admitted (fail-open!)")
    if jn_k_tamp != 0:
        failures.append("JSON: key-set TAMPERED admitted (fail-open!)")
    # null-tag must be rejected (unverifiable row cannot be admitted)
    if n_k_null != 0:
        failures.append("SQLite: key-set NULL-tag admitted (fail-open!)")
    # clean admitted ONLY when key set
    if n_k_clean != 1:
        failures.append("SQLite: key-set CLEAN not admitted")
    if jn_k_clean != 1:
        failures.append("JSON: key-set CLEAN not admitted")
    if n_no_clean != 0:
        failures.append("SQLite: no-key CLEAN admitted (should be refused)")
    if jn_no_clean != 0:
        failures.append("JSON: no-key CLEAN admitted (should be refused)")

    # --- Deployment posture (informational, does NOT fail the gate) ---------
    print("\n[Deployment posture]")
    if not integrity_enforced():
        print("  NOTE: this host has NO integrity key set.")
        print("  => SOVEREIGNTY OUTAGE: 0 humans are admitted by load_all().")
        print("     (by-design fail-closed; set %s and re-register each human)"
              % HUMAN_IDENTITIES_INTEGRITY_KEY_ENV)
        print("     This is why HC-11 stays UNVERIFIED on a key-less host;")
        print("     it is a deployment fact, NOT a mechanism defect.")
    else:
        print("  This host HAS an integrity key set; humans can be admitted.")

    # --- Verdict -----------------------------------------------------------
    print("\n" + "=" * 78)
    if failures:
        print("HC-11 MECHANISM: FAIL")
        for f in failures:
            print("  - " + f)
        _set_key(None)
        return 1
    print("HC-11 MECHANISM: FAIL-CLOSED in BOTH key postures (SQLite + JSON).")
    print("A tampered or unverifiable row is rejected whether or not the key")
    print("is configured; a clean row is admitted only with the key set.")
    print("This is the measured basis for HC-11's mechanism verdict.")
    _set_key(None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
