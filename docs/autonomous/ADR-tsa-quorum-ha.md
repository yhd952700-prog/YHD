# ADR: TSA High-Availability / K-of-M Quorum (U53 HA — remove the single-TSA SPOF)

- **Status:** ACCEPTED (primitive + unit tests + CI gate implemented; live HC-01 integration deferred, gated on D22)
- **Date:** 2026-09-30
- **Owner:** security-identity (`sec-impl`) + os-systems (`os-impl`)
- **Supersedes / relates:** extends `trusted_timestamp.py` (U53 evidence-grade RFC 3161 timestamping) and `root_of_trust.py` (U53 Root-of-Trust); pairs with the frozen HC-01 disposition.

## 1. Problem

U53 evidence-grade timestamping binds every chain-head attestation to a trusted
time via an independent Timestamp Authority (RFC 3161). But the single-TSA path
pins trust to **one** TSA. That is a single point of failure on two axes:

- **Availability:** if that one TSA is unreachable, the producer cannot obtain a
  timestamp and (fail-closed) cannot attest the head — the audit pipeline stalls
  or is forced to skip timestamping.
- **Trust concentration:** a verifier that trusts a single TSA's cert accepts a
  timestamp from exactly one independent party. One compromised or coerced TSA
  silently defeats the time-binding guarantee.

The standing proactive-discovery mandate flagged "TSA high-availability /
multiple-TSA-quorum" as the remaining evidence-grade hardening for U53.

## 2. Decision

Introduce a **`QuorumTimestampAuthority`** that wraps N
`TrustedTimestampAuthority` members and requires **K-of-M** of them to agree.

- `request_token(data)` mints from **every reachable member**. A member that
  raises (outage, misconfiguration) is tolerated; the call succeeds as long as
  **≥ `quorum`** members still return a token. `require_full=True` makes the
  strict policy explicit: every member must succeed or the call fails.
- `verify_bundle(bundle, data)` accepts only if **≥ `threshold` DISTINCT
  members** verify their token against the *same* payload. Distinctness is
  enforced by `cert_id` (sha256 pin of each TSA's CA cert) — two tokens from the
  same TSA count once.
- The quorum set is carried on the U53 attestation as `tsa_bundle`
  (`TimestampTokenBundle`: `threshold`, `members`, per-member
  `TimestampToken.to_record()`). `verify_timestamp` in `root_of_trust.py`
  dispatches to quorum verification when an attestation carries a `tsa_bundle`
  and a `QuorumTimestampAuthority` is configured.
- Production wiring: point `QuorumTimestampAuthority` at N *independently
  operated* TSAs (different CAs/cert_ids), so the quorum is a real
  operator-diversity guarantee, not N instances of one operator.

## 3. Fail-closed guarantees (non-negotiable)

- **Below threshold ⇒ reject.** A bundle carrying fewer tokens than `threshold`
  fails (`verify_bundle` returns `False` before any crypto check).
- **Unknown / untrusted TSA token ⇒ ignored, never trusted.** A token whose
  `cert_id` is not in the configured member set is skipped (`_member_by_cert_id`
  returns `None`); it cannot inflate the verified count. A bundle consisting
  *only* of unknown-TSA tokens yields 0 verified members and is rejected.
- **Tampered payload ⇒ fails every member.** Verifying any token against data
  different from what it was minted over returns `False` via `openssl ts
  -verify`; the bundle therefore fails.
- **Member outage tolerated, optionally strict.** `request_token` never fails
  solely because one member is down, as long as `quorum` members succeed;
  `require_full=True` upgrades the policy so a down member fails the request.
- **Distinctness enforced.** Duplicated `cert_id`s count once, so a quorum of
  "3" cannot be satisfied by 3 tokens from one TSA.

## 4. Evidence (proven, not assumed)

- `tests/kernels/audit/test_timestamp_quorum.py` — 8 tests (openssl-guarded):
  mint+verify (3-member/quorum-2), below-threshold reject, unknown-TSA ignored,
  tampered-payload reject, member-outage tolerated, `require_full` fails on
  outage, bundle record round-trip, and end-to-end composition with the U53
  `RootOfTrustAttestation` (sign→verify→tamper-reject). **8 passed** (real
  `openssl ts` output; not fabricated).
- CI self-test `scripts/verify_u53_tsa_quorum.py` (wired into `ci.yml`
  `guardrails` job after the U53 step): re-exercises the same fail-closed
  contract end-to-end and exits non-zero on any failure. Passes
  `tests/test_guardrail_scripts.py` meta-guardrail (97 tests). Exit 0.
- Combined audit-primitive regression (U51 + U53-core + U53-RoT + U53-quorum):
  **41 passed**, flake8 clean.

## 5. Rejected alternatives

- **Single TSA with retry/backoff:** improves availability cosmetically but does
  nothing for trust concentration — one compromised TSA still defeats the
  guarantee. Quorum removes both axes.
- **Threshold over N tokens from the *same* TSA:** fake diversity; distinct
  `cert_id` enforcement makes the quorum meaningful.
- **Quorum over the RoT *signature* instead of the timestamp:** conflates
  attribution (who signed) with time-binding (when). The timestamp quorum is the
  right layer; the RoT-signature `KeyRing` already handles multi-key attribution.

## 6. Next steps (deferred, gated)

- **Live HC-01 quorum-timestamping:** configure `QuorumTimestampAuthority` with
  independently-operated TSAs and stamp each appended head. Gated on HC-01/D22
  chain stabilisation — we do not timestamp a broken head.
- **External anchoring:** publish each notary root into a public append-only /
  transparency log so inclusion proofs are verifiable against an
  operator-independent anchor (the remaining proactive-discovery item).
