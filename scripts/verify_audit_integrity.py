#!/usr/bin/env python3
"""verify_audit_integrity.py -- single fail-closed audit-integrity aggregate (P08/U51/U53).

The audit subsystem now has eight evidence-grade, fail-closed CI gates:
  P08  hash-chain sig/alg (HC-02..HC-08 + HC-11)
  U53  Root-of-Trust chain-head signing
  U53Q K-of-M quorum TSA (HA, openssl-gated)
  U51N chain-head notary / inclusion proof
  U51A operator-independent transparency anchor
  U51X REAL external anchor (RFC 6962 + STH)
  U51C external anchor key ceremony (pinned out-of-band key)
  FENCE single-writer fence on the audit append path (Q3.5 / #99)

`independent_verification.py` (G10) re-derives C10-1..C10-4 but NOT these six --
it has no knowledge of U51/U53. This command closes that gap: it is the single
canonical, fail-closed audit-integrity aggregate. Each gate is re-derived in a
SEPARATE subprocess (builder-decoupled -- the builder cannot fake a gate it does
not run), any single gate failure fails the whole command, and the result carries
a tamper-evident `record_hash` (sha256 of the canonical record) so the run itself
can be independently attested.

Fail-closed: any gate rc != 0 => aggregate FAIL => non-zero exit.
A skipped openssl-gated gate (rc 0 but stdout flags a skip/note) is PASS-with-note,
never a silent PASS.

Exit 0 = all gates pass. Exit 2 = at least one gate failed.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))  # required by tests/test_guardrail_scripts.py

#: (id, human description, relative path to the gate script under REPO)
GATES: list[tuple[str, str, str]] = [
    ("P08", "hash-chain sig/alg (HC-02..HC-08 + HC-11)", "scripts/verify_p08_hash_chain_sig_alg.py"),
    ("U53", "Root-of-Trust chain-head signing", "scripts/verify_u53_root_of_trust.py"),
    ("U53Q", "K-of-M quorum TSA (HA, openssl-gated)", "scripts/verify_u53_tsa_quorum.py"),
    ("U51N", "chain-head notary / inclusion proof", "scripts/verify_u51_inclusion_proof.py"),
    ("U51A", "operator-independent transparency anchor", "scripts/verify_u51_transparency_anchor.py"),
    ("U51X", "REAL external anchor (RFC 6962 + STH)", "scripts/verify_u51_external_anchor.py"),
    ("U51C", "external anchor key ceremony (pinned out-of-band key)", "scripts/verify_u51_external_anchor_ceremony.py"),
    ("FENCE", "single-writer fence on audit append path", "scripts/verify_fence_single_writer.py"),
]

#: substrings (lowercased) that flag a PASS-with-note (skip / self-attested / etc.)
NOTE_MARKERS = ("skip", "self-attested", "self_attested", "not verified", "extrapolation")


def _run_gate(script_rel: str) -> dict:
    script = REPO / script_rel
    result: dict = {"script": script_rel, "status": "FAIL", "label": "FAIL", "evidence": ""}
    try:
        proc = subprocess.run(
            [sys.executable, str(script)],
            cwd=str(REPO),
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        result["evidence"] = "could not launch gate: %s" % exc
        return result
    out = (proc.stdout or "") + (proc.stderr or "")
    last_line = ""
    for line in reversed(out.splitlines()):
        if line.strip():
            last_line = line.strip()
            break
    if proc.returncode == 0:
        note = any(marker in out.lower() for marker in NOTE_MARKERS)
        result["status"] = "PASS"
        result["label"] = "PASS (NOTE)" if note else "PASS"
        result["evidence"] = last_line
    else:
        result["evidence"] = last_line or ("rc=%d" % proc.returncode)
    return result


def main() -> None:
    rows = [_run_gate(script) for _, _, script in GATES]
    n_fail = sum(1 for r in rows if r["status"] == "FAIL")
    n_pass = len(rows) - n_fail

    print("=== verify_audit_integrity: audit-integrity aggregate ===")
    for (gid, desc, _), r in zip(GATES, rows):
        print("[%s] %s: %s -- %s" % (gid, desc, r["label"], r["evidence"]))

    record = {
        "command": "verify_audit_integrity",
        "gates": [
            {"id": gid, "desc": desc, "status": r["status"], "evidence": r["evidence"]}
            for (gid, desc, _), r in zip(GATES, rows)
        ],
        "n_pass": n_pass,
        "n_fail": n_fail,
    }
    canonical = json.dumps(record, sort_keys=True, separators=(",", ":"))
    record_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    print("")
    print("PASS=%d FAIL=%d OVERALL=%s" % (n_pass, n_fail, "PASS" if n_fail == 0 else "FAIL"))
    print("record_hash=%s" % record_hash)

    if "--emit" in sys.argv[1:]:
        try:
            idx = sys.argv.index("--emit")
            emit_path = sys.argv[idx + 1]
            record["record_hash"] = record_hash
            Path(emit_path).write_text(json.dumps(record, indent=2), encoding="utf-8")
            print("record written to %s" % emit_path)
        except (IndexError, OSError) as exc:
            print("WARN: could not emit record: %s" % exc)

    if n_fail > 0:
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    sys.exit(main())
