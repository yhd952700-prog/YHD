# ADR-root-of-trust-hd05 — Provider-Neutral Root-of-Trust; Production Provider Reserved for Human Decision (HD-05)

| | |
|---|---|
| **Status** | LOCKED (decision D27; HD-05 reserved) |
| **Decision** | HD-05 (human-sovereign) — see `docs/autonomous/HUMAN-DECISION-BACKLOG.md` |
| **Date** | 2026-09-28 |
| **Owner** | Human (LIUHAO owner / boss) |
| **Supersedes** | — |
| **Related** | `src/security/evidence` (provider-neutral subsystem), `scripts/timestamp_authority.py` (DEV-ONLY mock), `docs/autonomous/HUMAN-DECISION-BACKLOG.md` HD-05 |

---

## 1. Context

The LIUHAO platform needs a trusted-timestamp / platform-root-of-trust mechanism
so that evidence artifacts (audit views, deployment digests, safeguard MANIFESTs)
carry a verifiable "this was produced at time T by authority A" proof. HD-05 in
the human-decision backlog reserves the *choice of the final provider* to the
human owner:

> "Which trusted-timestamp / hardware-root-of-trust provider?"
> Options: (A) local RFC3161-shaped mock; (B) public RFC 3161 TSA; (C) TPM/HSM
> hardware root; (D) multi-provider quorum.
> "Required owner action: pick provider (config) when ready."

Until that human decision lands, engineering ships a **provider-neutral
abstraction** with a safe, offline **local self-attested mock** as the default so
nothing blocks on an external/paid provider or an irreversible key ceremony.

Two failures of the previous phase-1 code must be corrected, and this ADR locks
the correction in:

1. **The local mock was implicitly presented as if it verified independently of
   its issuer.** It does not: the same key both stamps and verifies. That is
   self-attestation, which is fine for dev/test tamper-evidence but is NOT
   third-party trust. The code must make this explicit and refuse to present a
   self-attested token as production-grade / independently verified.

2. **A second, divergent mock existed in `scripts/timestamp_authority.py`** that
   described itself as "fully usable today" with a *different* (HMAC) schema from
   the evidence subsystem. That was an architecture/documentation lie: a divergent
   self-attested HMAC mock is not a production root of trust. It is now explicitly
   marked DEV-ONLY and divergent.

---

## 2. Decision

### 2.1 Split issuer from verifier + trust anchor

The subsystem now separates three concerns (see `src/security/evidence/interfaces.py`):

* **`TimestampIssuer`** — produces a `(digest, time)` proof. May be self-attested.
* **`TimestampVerifier`** — independently verifies a proof against a configured
  `TrustAnchor` (X.509 cert / public key).
* **`TrustAnchor`** — the configured root of trust used for independent verification.

Every `TimestampToken` carries two explicit, non-negotiable fields:

* `authority` — who attests the timestamp (e.g. `"local"`, a TSA host, or a
  configured anchor id).
* `self_attested` — `True` iff the issuer is the same entity that verifies. A
  self-attested token MUST NOT be presented as independently verified.

### 2.2 Local default is, and stays, self-attested and non-production

* `LocalRfc3161LikeProvider` (`src/security/evidence/local_provider.py`) stamps
  tokens with `self_attested=True` and `authority="local"` always.
* `factory.LOCAL_IS_PRODUCTION_GRADE = False`. `build_default_subsystem()` returns
  `production_grade=False` for the local provider, and `EvidenceVerifier` sets
  `VerificationResult.self_attested=True` (with a recorded check) for any
  self-attested bundle, so results can never be silently presented as production.
* No code path may claim the local mock is independently verified or
  production-grade. Doing so would be a violation of this ADR and of the HD-05
  hard prohibitions in `ADR-HOLD-SCOPE.md` ("no claiming interface-only TSA/TPM
  as externally-trusted evidence").

### 2.3 The RFC 3161 verification logic is real and offline-testable

* `Rfc3161TimestampProvider` (`src/security/evidence/rfc3161_provider.py`) now
  implements a **real RFC 3161 CMS `TimeStampToken`** build + verify path
  (`src/security/evidence/rfc3161_cms.py`): it parses CMS `SignedData`, verifies
  the signature against a configurable X.509 trust anchor, and checks the
  `messageImprint` (`sha256(data)`), the `nonce`, and `genTime` accuracy.
* The verification logic does **not** raise `NotImplementedError`. It is exercised
  offline (no live TSA) against a generated anchor in `tests/security/
  test_root_of_trust.py`.
* Issuance is offline-capable: if no signing material is supplied, the provider
  generates an ephemeral self-signed key+cert so the stack stays usable — but the
  resulting token is `self_attested=True` (because `tsa_url is None`). Contacting a
  *live* external TSA (HTTP round-trip) is intentionally NOT done here; that is a
  wiring detail for the human-chosen production provider.

### 2.4 `scripts/timestamp_authority.py` is DEV-ONLY and divergent

It is explicitly marked DEV-ONLY, self-attested, and schema-divergent from
`src/security/evidence`. It is not the production root-of-trust path and must not
be documented as such.

### 2.5 HD-05 remains RESERVED

Selecting the production provider (real RFC 3161 TSA / TPM / quorum), configuring
its trust root, and performing any irreversible key ceremony are **human
decisions**. The code stays provider-neutral and config-driven
(`LIUHAO_TSA_PROVIDER`); the default remains the local self-attested mock until the
owner chooses otherwise. Irreversible actions (paid/external account, hardware
provisioning, key ceremony) remain blocked behind the human decision.

---

## 3. Consequences

### Positive
* Root-of-trust honesty is enforced in the type system and in verification
  results: self-attested vs independently-verified is explicit, not implicit.
* The RFC 3161 verification math is real and proven offline, so the eventual
  production provider reuses verified building blocks.
* The divergent DEV-ONLY mock is no longer misrepresented as production.

### Negative / costs
* A self-attested local timestamp cannot be relied upon as third-party evidence;
  that is by design and will change only with a human-chosen provider.
* More fields (`authority`, `self_attested`) travel in every token and must be
  preserved by serialization/transport.

### Rollback / change
* To promote a provider to production-grade, a new human decision (HD-05 owner
  action) must (a) select the provider, (b) configure its trust anchor, and (c)
  flip `LOCAL_IS_PRODUCTION_GRADE` semantics by introducing a non-self-attested
  provider — not by editing the local mock to claim independence.

---

## 4. Acceptance / verification notes
* `tests/security/test_root_of_trust.py` MUST prove:
  * local tokens carry `self_attested=True` and `authority="local"`;
  * RFC 3161 CMS verify succeeds offline against a generated anchor, and fails on
    tampered data / missing anchor / provider mismatch (fail-closed);
  * the local mock is clearly non-production (`LOCAL_IS_PRODUCTION_GRADE is False`,
    `VerificationResult.self_attested is True` for a local bundle).
* `flake8` (CI口径: `--max-line-length=100 --select=E,F,W --ignore=E501,W503`)
  MUST pass on `src/security/evidence/` and `scripts/timestamp_authority.py`.
