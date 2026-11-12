# ADR: Audit Transparency-Log Anchor (U51 external anchor — operator-independent root commitment)

- **Status:** ACCEPTED (primitive + unit tests + CI gate implemented; live HC-01 integration deferred, gated on D22)
- **Date:** 2026-09-30
- **Owner:** security-identity (`sec-impl`) + os-systems (`os-impl`)
- **Supersedes / relates:** builds on `inclusion_proof.py` (U51 Merkle notary); pairs with the frozen HC-01 disposition.

## 1. Problem

U51's Merkle notary gives every historical signed head a trustless **inclusion
proof** against the notary's PUBLISHED ROOT. But the published root — and the
notary file that stores it — live entirely under the **audit operator's**
control. An inclusion proof only protects against the operator silently rewriting
the *leaves* between two checkpoints; it does **not** protect against the
operator rewriting the **published root itself** (and the surrounding notary
file) at the same time, because the root is trusted-by-itself. That is the
residual trust gap U51 leaves open.

A verifier who cannot distinguish "operator's honest root" from "operator's
rewritten root" has no external anchor to check against.

## 2. Decision

Introduce a **transparency-log anchor**: each published notary root is committed
to an **operator-independent, signed, append-only log**.

- The anchor log is signed by its OWN key (`anchor_priv`), **deliberately
  distinct** from the audit operator's Root-of-Trust key. This models an
  independent transparency authority.
- Every entry commits (via sha256) to: its sequence number, the previous entry's
  digest (`prev`), the notary root it anchors (`notary_root_hash`), and its own
  signature. The entry `digest` covers the full entry *including the signature*,
  so a re-signed forgery changes the next entry's expected `prev` and breaks the
  chain.
- `publish(notary_root_hash)` appends a signed, chained entry and returns an
  `AnchorReceipt` (the entry + its predecessor, for local verification).
- `verify_receipt` proves, against the anchor public key: the signature is valid,
  the entry's digest is internally consistent, the notary root matches, and the
  chain linkage to the presented predecessor (or genesis) holds.
- `verify_consistency` recomputes the whole chain from genesis and refuses any
  forgery (bad signature, tampered digest, broken `prev`, sequence gap/reorder).
- `save`/`load` are fail-closed: load re-verifies the entire chain and **refuses**
  a tampered or truncated log file.

Once a root is anchored, the operator cannot rewrite/drop/reorder it without
breaking the chain — detectable by any auditor holding the anchor public key.

## 3. Fail-closed guarantees (non-negotiable)

- A tampered `notary_root_hash` in a receipt ⇒ `verify_receipt` rejects.
- A forged/mutated signature ⇒ rejected (signature verification fails).
- An entry whose digest disagrees with its signed content (post-signing
  tampering) ⇒ rejected by `verify_receipt` and `verify_consistency`.
- A broken `prev` linkage or a sequence gap/reorder ⇒ `verify_consistency`
  rejects at the offending index.
- A tampered persisted log file ⇒ `load` raises `ValueError` (never silently
  trusted).
- A non-genesis receipt presented without its predecessor ⇒ rejected (cannot
  prove linkage locally).

## 4. Evidence (proven, not assumed)

- `tests/kernels/audit/test_transparency_anchor.py` — 10 tests: publish+verify
  round-trip, tampered-root rejected, forged-signature rejected, consistency
  detects tampered entry, consistency detects broken linkage, consistency detects
  sequence gap, non-genesis receipt requires predecessor, save/load round-trip,
  tampered-file load refused, and end-to-end composition with the U51 notary
  (notary publishes a root → anchor commits it → receipt verifies → tampered
  root rejected). **10 passed.**
- CI self-test `scripts/verify_u51_transparency_anchor.py` (wired into `ci.yml`
  `guardrails` job after the U51 step): re-exercises the same fail-closed
  contract end-to-end and exits non-zero on any failure. Passes
  `tests/test_guardrail_scripts.py` meta-guardrail (100 tests). Exit 0.
- Combined audit-primitive regression (U51 + U53-core + U53-RoT + U53-quorum +
  U51-anchor): **51 passed**, flake8 clean.

## 5. Rejected alternatives

- **Trust the notary's own root with no external anchor:** does not close the
  residual operator-rewrite gap — the reason this ADR exists.
- **Publish roots to a real public log immediately (e.g., a CT log / Bitcoin /
  OpenTimestamps):** correct end-state, but the project does not yet run (or want
  to depend on) such a service. The local, independent-key log is the
  self-contained primitive with an identical interface; production swaps the
  anchor key+transport for a real public log without code changes to the verifier.
- **Sign the notary root with the operator's RoT key as the "anchor":** that is
  not operator-independent — it is the same trust boundary the anchor is meant to
  escape. The anchor key is deliberately separate.

## 6. Next steps (deferred, gated)

- **Live HC-01 anchoring:** after HC-01 stabilises (D22), anchor each published
  notary root in the transparency log inside (or immediately after) the append
  transaction, and verify the anchor on every audit query. Point the anchor at a
  real public log (operator-independent transport) for production.
- **Live HC-01 notarisation + U53 quorum-timestamping** remain deferred on the
  same D22 gate.
