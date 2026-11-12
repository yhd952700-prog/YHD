# ADR: Audit Chain-Head Notary (U51 — trustless inclusion proofs)

- **Status:** ACCEPTED (primitive + CI gate implemented; live HC-01 integration deferred, gated on D22)
- **Date:** 2026-09-30
- **Owner:** security-identity (`sec-impl`) + os-systems (`os-impl`)
- **Supersedes / relates:** builds on `root_of_trust.py` (U53) and `trusted_timestamp.py` (U53 evidence grade); pairs with the frozen HC-01 disposition.

## 1. Problem

U40/U53 give the audit chain head a *signed* (Root of Trust) and *timestamped*
(RFC 3161) attestation, proving **attribution** and **time**. They do **not**
prove **position**: an external auditor holding a signed head cannot cheaply prove
that head is part of the canonical, contiguous historical sequence without
replaying the entire chain. This is U51's root cause — *"no single value
committing its own history ⇒ cheap notarization/inclusion-proof infeasible."*

Without it, the strongest claim an auditor can make today is "this head was
signed by the authority" — not "this head is the Nth committed head and cannot
be silently reordered / dropped." A tampered operator could present a validly
signed *fork* and an auditor would have no cheap way to detect that the head is
absent from the canonical timeline.

## 2. Decision

Introduce a **chain-head notary**: an append-only **Merkle accumulator** over the
sequence of signed-head digests.

- Every U53 attestation yields a 32-byte `head_digest = sha256(canonical(record))`
  (`head_digest_of`). The notary appends it as a Merkle leaf.
- Periodically the notary **publishes a root** = Merkle root over all leaves so
  far. In production that root is itself RoT-attested (`provenance`) and
  persisted inside the append transaction, so the root is anchored to the
  authority and time too.
- An auditor holds any historical signed head → requests an **inclusion proof**
  (Merkle path) → verifies it against the published root **without trusting the
  operator and without downloading the whole chain**. This is U51's "cheap
  inclusion proof."
- Because each published root commits (via Merkle structure) to all prior heads,
  every head now *commits its own history*: the root cause of U51 is removed.

## 3. Fail-closed guarantees (non-negotiable)

- Inclusion verification recomputes the root from leaf + siblings and compares
  to the **trusted published root**; any tampered leaf, sibling, index, or wrong
  root ⇒ `False`. A forged/tampered `InclusionProof` that binds to an
  unpublished root is rejected (`resolve_trusted_root` returns `None`).
- A published root is **always recomputed** by the notary from its leaves; an
  externally-supplied root is never trusted.
- `save`/`load` are fail-closed: on load the notary recomputes every recorded
  root from the re-derived leaves and **refuses (raises)** to load a file whose
  recorded root disagrees — a corrupted or forged notary file cannot be silently
  trusted.
- Leaves must be 32-byte sha256 digests; a malformed leaf is rejected.
- An **uncheckpointed** head cannot be proven (`prove_inclusion` raises) — the
  notary never claims inclusion of a head it has not committed to a published
  root.

## 4. Evidence (proven, not assumed)

- `tests/kernels/audit/test_inclusion_proof.py` — 14 unit tests: Merkle
  determinism / order-sensitivity, single-leaf self-root, valid proof round-trip
  for first/middle/last leaves, tampered-leaf / tampered-sibling / wrong-root /
  forged-index rejection, uncheckpointed refusal, `verify_self_consistency`
  detects a forged root, tampered-persisted-file refusal on load, clean
  save/load round-trip, U53+U51 composition (`head_digest_of` of a real signed
  attestation), unknown-root rejection. **14 passed.**
- CI self-test `scripts/verify_u51_inclusion_proof.py` (wired into `ci.yml`
  `guardrails` job): exercises the same fail-closed contract end-to-end and
  exits non-zero on any failure. Passes `tests/test_guardrail_scripts.py`
  meta-guardrail (bootstrap + non-zero-exit + CI-wired). Exit 0.
- flake8 clean (CI口径: `--max-line-length=100 --select=E,F,W --ignore=E501,W503`).

## 5. Rejected alternatives

- **Replay-the-whole-chain verification:** honest but not "cheap" — O(N) I/O and
  parsing for every audit query; defeats the purpose of U51.
- **A single rolling signature over all history:** does not give *per-head*
  inclusion proofs and orphans old heads on rotation; the Merkle approach gives
  O(log N) proofs and composes with the rotation-aware `KeyRing`.
- **External public-log anchoring as the *only* mechanism:** correct end-state,
  but it requires an operator-independent log the project does not yet run. The
  internal Merkle notary is the self-contained primitive; anchoring the published
  root into a transparency log is the next (deferred) hardening step.

## 6. Next steps (deferred, gated)

- **Live HC-01 notarisation:** persist notary roots inside the append
  transaction (a `chain_head_notary` table) and RoT-sign each published root.
  Gated on HC-01/D22 chain stabilisation — we do not checkpoint a broken head.
- **External anchoring:** publish each notary root into a public append-only /
  transparency log so inclusion proofs are verifiable against an
  operator-independent anchor.
- **TSA HA / multi-TSA quorum** for the timestamp authority (see forward-looking
  proactive discovery).
