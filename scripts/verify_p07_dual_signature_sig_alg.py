#!/usr/bin/env python
"""Runtime probe: prove P0-7 B2 dual-signature evidence records ``sig_alg``.

PHASE 3.6 / P0-7 (B2): every dual-signature evidence record -- a human's
sovereign override assertion and an independent verifier's confirmation -- must
explicitly record the SIGNATURE ALGORITHM that produced it, and verification
must DISPATCH ON that recorded algorithm. An unknown / unsupported algorithm
must fail-closed; the build must NEVER fall back to a default algorithm.

This probe:

  1. Generates a REAL ``HumanOverrideAssertion`` + ``VerifierConfirmation`` with
     real RSA-3072 signatures (``sig_alg = "RS256-RSA3072"``) and PERSISTS them.
  2. Re-reads the persisted evidence INDEPENDENTLY (a fresh store instance, same
     on-disk DB) and verifies it -- proving ``sig_alg`` round-trips and drives
     verification from disk, not from an in-memory object.
  3. Asserts the boss's core invariant:
     ``sig_alg == actual verification algorithm`` for BOTH records.
  4. Fail-closed / NO-FALLBACK: a record signed by the real algorithm but whose
     declared ``sig_alg`` is an unknown identifier must be REJECTED (not verified
     with the default) -- proving the identifier is load-bearing, not decorative.
  5. Tamper detection: mutating a persisted claim must break verification.

Exit codes:
  0  every assertion passed
  1  a security assertion FAILED (regression -- must not ship)
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_TMP = tempfile.mkdtemp(prefix="p07_probe_")
_DB = os.path.join(_TMP, "dual_signature_evidence.db")
os.environ["DUAL_SIGNATURE_DB_PATH"] = _DB

from src.kernels.audit.dual_signature import (  # noqa: E402
    DualSignatureStore,
    SignatureRecord,
    create_dual_signature_evidence,
    verify_signature_record,
)

_RESULTS: list[tuple[bool, str]] = []


def _record(ok: bool, label: str) -> None:
    _RESULTS.append((ok, label))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")


def main() -> int:
    print("P0-7 B2 dual-signature evidence `sig_alg` runtime probe")
    print("  (real RSA-3072 signatures + real on-disk persistence + independent "
          "re-read)\n")

    principal_id = "human:bob"
    action = "capability.retire"
    reason = "emergency rotation of leaked signing key"
    verifier_id = "verifier:carol"
    correlation_id = "corr-p07-1"

    # --- (1) Generate + persist REAL dual-signature evidence -----------------
    store_a = DualSignatureStore(_DB)
    bundle = create_dual_signature_evidence(
        principal_id=principal_id,
        action=action,
        reason=reason,
        verifier_id=verifier_id,
        correlation_id=correlation_id,
        store=store_a,
    )
    store_a.close()
    _record(True, "HumanOverrideAssertion + VerifierConfirmation generated and "
                  "persisted with sig_alg='RS256-RSA3072'")

    # --- (2) Independent re-read from disk (fresh store instance) -----------
    store_b = DualSignatureStore(_DB)
    human = store_b.load(bundle.human_record.record_id)
    verifier = store_b.load(bundle.verifier_record.record_id)
    store_b.close()
    _record(
        human is not None and verifier is not None,
        "persisted evidence re-read INDEPENDENTLY from disk (fresh store)",
    )
    _record(
        human.sig_alg == "RS256-RSA3072" and verifier.sig_alg == "RS256-RSA3072",
        "re-read records carry sig_alg == 'RS256-RSA3072' (round-trips on disk)",
    )

    # --- (3) sig_alg == actual verification algorithm (boss core invariant) -
    h_ok, h_actual = verify_signature_record(human, bundle.human_public_pem)
    v_ok, v_actual = verify_signature_record(verifier, bundle.verifier_public_pem)
    _record(h_ok, "HumanOverrideAssertion verifies (real RSA-3072)")
    _record(v_ok, "VerifierConfirmation verifies (real RSA-3072)")
    _record(
        human.sig_alg == h_actual,
        f"human sig_alg ({human.sig_alg!r}) == actual verify algorithm "
        f"({h_actual!r})",
    )
    _record(
        verifier.sig_alg == v_actual,
        f"verifier sig_alg ({verifier.sig_alg!r}) == actual verify algorithm "
        f"({v_actual!r})",
    )

    # --- (4) Fail-closed / NO DEFAULT FALLBACK ------------------------------
    # A record whose BYTES are a genuine RSA-3072 signature but whose declared
    # sig_alg is unknown must be REJECTED. If the build fell back to the default
    # algorithm it would verify -- so a pass here is the proof of the bug, and a
    # fail here is the proof of correctness.
    forged = SignatureRecord(
        record_id="forged-unknown-alg",
        role=human.role,
        sig_alg="RS999-UNKNOWN",
        key_id=human.key_id,
        claims=human.claims,
        signature=human.signature,   # genuine RS256-RSA3072 bytes
        signed_at=human.signed_at,
    )
    f_ok, f_actual = verify_signature_record(forged, bundle.human_public_pem)
    _record(
        (not f_ok) and f_actual == "unknown",
        "unknown sig_alg REJECTED (no fallback to default algorithm) -- "
        "identifier is load-bearing, not decorative",
    )

    # A record with a corrupted signature under the KNOWN algorithm must also
    # fail (does not silently pass).
    tampered_sig = SignatureRecord(
        record_id="tampered-sig",
        role=human.role,
        sig_alg=human.sig_alg,
        key_id=human.key_id,
        claims=human.claims,
        signature="AAAA" + human.signature[4:],
        signed_at=human.signed_at,
    )
    t_ok, t_actual = verify_signature_record(tampered_sig, bundle.human_public_pem)
    _record(
        (not t_ok) and t_actual == "RS256-RSA3072",
        "corrupted signature under known alg REJECTED (algorithm preserved, "
        "verdict honest)",
    )

    # --- (5) Tamper detection on persisted claim ----------------------------
    tampered_claims = dict(human.claims)
    tampered_claims["principal_id"] = "human:eve"  # attacker rewrote the subject
    tampered_record = SignatureRecord(
        record_id=human.record_id,
        role=human.role,
        sig_alg=human.sig_alg,
        key_id=human.key_id,
        claims=tampered_claims,
        signature=human.signature,   # still the original genuine signature
        signed_at=human.signed_at,
    )
    c_ok, _ = verify_signature_record(tampered_record, bundle.human_public_pem)
    _record(
        not c_ok,
        "mutated persisted claim BREAKS verification (tamper detected)",
    )

    passed = sum(1 for ok, _ in _RESULTS if ok)
    total = len(_RESULTS)
    print(f"\n{passed}/{total} assertions passed")
    failed = [label for ok, label in _RESULTS if not ok]
    if failed:
        print("FAILED:")
        for label in failed:
            print(f"  - {label}")
        return 1
    print("P0-7 B2 Dual-Signature `sig_alg` is CONTAINED (every evidence record "
          "states its signature algorithm; verification dispatches on it; unknown "
          "algorithms fail-closed with no default fallback; tampering detected).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
