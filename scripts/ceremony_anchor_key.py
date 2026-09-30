#!/usr/bin/env python3
"""ceremony_anchor_key.py -- out-of-band key ceremony for the U51 REAL external anchor.

PROBLEM THIS CLOSES
-------------------
``scripts/verify_u51_external_anchor.py`` proves ``src/kernels/audit/external_anchor.py``
works, but its reference log key is GENERATED AT RUNTIME by the gate
(``_ref_log()`` calls ``generate_keypair()``). That contradicts the module's own
hard requirement: the trust anchor must be PINNED OUT-OF-BAND and NEVER generated
by the audit process. So ``external_anchor.py`` was proven-but-NOT-deployable:
nothing actually minted and pinned the external key.

This tool performs the missing CEREMONY: it mints the external anchor key ONCE,
pins the PUBLIC key to a config artifact (``ceremony_record.json``), and produces
a ceremony-attested anchored log (``anchored_log.jsonl``) signed by the pinned
key. The audit process later only CONSUMES the pinned public key -- it never
generates it. ``scripts/verify_u51_external_anchor_ceremony.py`` proves that
consumption path.

HONESTY
-------
* The ceremony record carries a self-signature over the ceremony claim, signed by
  the minted private key. This proves (a) a ceremony attested the key, and (b)
  possession of the private key at ceremony time. It is NOT a third-party
  attestation; it is the operator's own ceremony record.
* The private key is written to ``anchor_key.pem`` (mode 0600) and is GITIGNORED
  (``*.pem``). It is the ceremony-held secret; the audit runtime never needs it.
* This tool is OPERATIONAL, not a CI gate. It is run ONCE per environment by an
  operator. The CI gate consumes the committed public artifacts.
* This does NOT assert the live HC-01 chain is externally anchored in production
  -- that integration is the next step, gated on HC-01 stabilisation. It asserts
  the external-anchor key PROVISIONING workflow exists and is fail-closed.

Exit 0 = ceremony completed (or already present without --force). Exit 1 = error.
"""
from __future__ import annotations

import argparse
import base64
import datetime as _dt
import hashlib
import json
import os
import pathlib
import sys
import uuid

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))  # keep importable when run directly

from src.kernels.audit.dual_signature import (  # noqa: E402
    DEFAULT_SIGN_ALG,
    SIGN_ALGORITHMS,
    canonical_bytes,
    key_id_of,
)
from src.kernels.audit.external_anchor import (  # noqa: E402
    ExternalTransparencyAnchor,
    LocalReferenceLog,
)


DEFAULT_CEREMONY_DIR = REPO / "config" / "external_anchor"


def _demo_root(i: int) -> str:
    return hashlib.sha256(
        ("liuhao-u51-ceremony-demo-%d" % i).encode("utf-8")
    ).hexdigest()


def _ceremony_claim(
    record_id: str,
    sig_alg: str,
    key_id: str,
    public_pem: str,
    ceremony_at: str,
    notes: str,
) -> dict:
    return {
        "ceremony_id": record_id,
        "kind": "external-anchor-ceremony",
        "sig_alg": sig_alg,
        "key_id": key_id,
        "public_pem": public_pem,
        "ceremony_at": ceremony_at,
        "notes": notes,
    }


def run_ceremony(ceremony_dir: pathlib.Path, force: bool) -> int:
    ceremony_dir = pathlib.Path(ceremony_dir)
    ceremony_dir.mkdir(parents=True, exist_ok=True)
    record_path = ceremony_dir / "ceremony_record.json"
    key_path = ceremony_dir / "anchor_key.pem"
    anchor_path = ceremony_dir / "anchored_log.jsonl"

    if record_path.exists() and not force:
        print(
            "CEREMONY SKIP: %s already exists -- refusing to overwrite an "
            "existing ceremony record (pass --force to re-ceremony)." % record_path
        )
        return 0

    # 1. Mint the external anchor key OUT-OF-BAND (here: once, by this ceremony).
    priv_pem, pub_pem = SIGN_ALGORITHMS[DEFAULT_SIGN_ALG].generate_keypair()
    pub_text = pub_pem.decode("ascii")
    kid = key_id_of(pub_pem)

    # 2. Write the private key (ceremony-held secret; gitignored via *.pem).
    key_path.write_bytes(priv_pem)
    try:
        os.chmod(key_path, 0o600)
    except OSError:
        pass  # Windows: chmod best-effort; the file is gitignored regardless.

    # 3. Emit the ceremony record with a self-signature (possession proof).
    now = _dt.datetime.now(_dt.timezone.utc).isoformat()
    notes = (
        "out-of-band pinned external anchor key for the U51 REAL external "
        "transparency-log anchor (RFC 6962 + STH). Minted in a key ceremony; "
        "the audit runtime consumes only the public key."
    )
    record_id = "extanchor-ceremony-%s" % uuid.uuid4().hex
    claim = _ceremony_claim(record_id, DEFAULT_SIGN_ALG, kid, pub_text, now, notes)
    sig = SIGN_ALGORITHMS[DEFAULT_SIGN_ALG].sign(priv_pem, canonical_bytes(claim))
    record = dict(claim)
    record["ceremony_claim_signature"] = base64.b64encode(sig).decode("ascii")
    record_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")

    # 4. Produce a ceremony-attested anchored log (demo roots) signed by the key.
    anchor = ExternalTransparencyAnchor(
        external_pub_pem=pub_pem,
        transport=LocalReferenceLog(log_priv=priv_pem, log_pub=pub_pem),
        self_attested=True,
    )
    for i in range(3):
        anchor.publish(_demo_root(i))
    anchor.save(str(anchor_path))

    print(
        "CEREMONY OK: external anchor key minted and pinned out-of-band.\n"
        "  ceremony_record : %s (public key, committable)\n"
        "  anchor_key      : %s (PRIVATE, gitignored, ceremony-held)\n"
        "  anchored_log    : %s (signed by the pinned key, committable)\n"
        "  key_id          : %s\n"
        "The audit runtime consumes only the public key; run "
        "verify_u51_external_anchor_ceremony.py to prove the consumption path."
        % (record_path, key_path, anchor_path, kid)
    )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="U51 external anchor key ceremony")
    ap.add_argument(
        "--out-dir",
        default=str(DEFAULT_CEREMONY_DIR),
        help="directory to write the ceremony artifacts into",
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help="re-ceremony even if a ceremony record already exists",
    )
    args = ap.parse_args()
    try:
        return run_ceremony(pathlib.Path(args.out_dir), args.force)
    except Exception as exc:  # pragma: no cover - defensive
        print("CEREMONY FAIL: %s" % exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
