# Governance-Legal Judgment — Retention / Erasure-Obligation vs Evidence-Preservation Conflict

> **Domain owner:** governance-legal-2 (法度明), privacy / retention / accountability.
> **Status:** INVESTIGATION + LEGAL JUDGMENT delivered. **Policy NOT decided** — the
> final erasure / retention policy is a **HUMAN DECISION (HD-06)**; the team must
> NOT auto-implement data-protection compliance or retention. This memo is the
> analysis + the honest-disclosure guard, not a policy.
> **Related:** `docs/autonomous/UNKNOWN-TO-OWNER.md` U36/U37/U52;
> `docs/autonomous/HUMAN-DECISION-HD06-U37.md`; Task #300.
> **Enforcement:** `tests/kernels/test_retention_erasure_conflict.py` keeps the
> disclosure honest and two-sided; `scripts/lint_audit_claims.py`
> (`DATA-SUBJECT-RIGHTS` rule) keeps outward claims honest.

---

## 1. The conflict, stated precisely

The retention subsystem (`src/kernels/retention/`) enforces a **hard invariant**:
an `IMMUTABLE_ORIGINAL` — the audit/evidence chain included — is **never deleted,
overwritten, or destroyed** by any policy, including `DELETE_AFTER_DAYS`. Only
`DERIVATIVE` copies may be purged (and only when not under a legal hold).

That invariant is simultaneously:

- **An evidence-preservation guarantee** — correct and defensible for audit
  integrity, legal holds, and regulatory retention mandates.
- **An erasure limitation** — for any original that is *personal data*, a
  data-subject erasure request **cannot be honored by deletion**.

These are the same fact seen from two legal directions. The code currently
discloses only the first. This memo restores the second, and gives the owner the
legal framing needed to set the final policy (HD-06).

---

## 2. The two legal poles

### Pole A — Deletion obligation (data subjects)
- **GDPR Art. 17(1)** right to erasure ("right to be forgotten"): the data
  subject may require erasure of personal data without undue delay, where one of
  the Art. 17(1)(a)–(f) grounds applies (e.g. consent withdrawn, purpose
  fulfilled).
- **GDPR Art. 17(2)** — *erasure also means notification*: the controller must
  take reasonable steps to inform *processors* (downstream handlers) so they too
  erase. Erasure is not a local delete; it is propagation.
- **CCPA/CPRA §1798.105(a)** — right to delete personal information collected
  from the consumer, subject to the §1798.105(d) exceptions.
- **PIPL (China) Art. 47** — deletion when processing purpose achieved, period
  expired, or consent withdrawn.

### Pole B — Evidence-preservation / non-deletion duty
- **Internal audit immutability** — HC-01 hard guard; `I1` no-delete / `I2`
  no-reorder invariants in `src/kernels/audit/recovery.py`; `GOVERNANCE §2.3`
  no-destroying-evidence.
- **Legal hold** — litigation / incident / regulatory preservation; a hold must
  survive ordinary operational events (restarts, deploys). *Caveat:* the current
  `LegalHoldRegistry` is **VOLATILE / latent** (disclosed in
  `test_retention_legal_hold_volatility.py`) — see §5.
- **Regulatory retention mandates** — e.g. financial / tax records fixed
  retention periods; some regimes require WORM (write-once-read-many) storage.
- **GDPR Art. 17(3) exemptions** — erasure does **not** apply where processing is
  necessary for (a) freedom of expression, (b) **legal obligation / public
  interest** (incl. public health), (c) **establishment, exercise or defence of
  legal claims**. An audit trail retained to defend legal claims or meet a legal
  retention duty falls under (b)/(c) — *but only for the data genuinely required
  for that purpose, and only for the prescribed period*.

---

## 3. The decisive clarification: crypto-shredding ≠ erasure (U52)

U52 already states the storage contradiction: "delete after N years" cannot be a
`DELETE` on an append-only chain — removing row *k* breaks every downstream
`prev_event_hash`. Three shapes work: (a) keep-forever, (b) retire whole shard
files, (c) **crypto-shredding** — encrypt `details` per tenant and destroy the
key.

The legal point that must not be lost:

> **After key destruction, the chain STILL proves that *an event of type X
> happened to principal Y at time T*.** Only the payload is unreadable.
> Crypto-shredding satisfies "unreadable"; it does **NOT** satisfy a GDPR Art. 17
> erasure obligation, which requires that the personal data is no longer
> *processed* / no longer attributable to the data subject. The *existence and
> attribution* of the event remain in the chain.

So if the owner believes "we crypto-shred, therefore we erase," that belief is
**load-bearing and may be wrong**. Crypto-shredding is a *confidentiality*
control, not an *erasure* control. This distinction is now embedded in the
retention docstrings (`lifecycle.py`, `manager.py`) and locked by the test guard.

---

## 4. Three-layer analysis (legal / ethical / best-practice)

### 法律义务 (Legal obligation) — what the law compels
- Do **not** assert GDPR/CCPA/PIPL compliance that does not exist. Today there is
  **no data-protection framework, no data-subject-rights mechanism, no finalized
  retention policy** (U36). Any outward "compliant / PII-protected / erasure
  supported" claim without a HUMAN DECISION / Frozen / 未实现 caveat is a **false
  external legal assurance** and is blocked by the `DATA-SUBJECT-RIGHTS` /
  `GDPR` / `CCPA` / `PII` / `PRIVACY` lint rules.
- The erasure obligation and the evidence-preservation duty **co-exist**. Neither
  cancels the other. The lawful reconciliation is: erase where an Art. 17(3)
  exemption does **not** apply (per-subject crypto-shred of erasable copies, or
  structured deletion of derivative PII), and **document** the exemption basis +
  retention period for the non-erasable residue.
- **Cross-border**: if PII crosses borders (the LIUHAO GLOBAL / foreign-trade
  surface implies it), GDPR Chapter V / PIPL cross-border transfer rules apply.
  There is currently **no cross-border / jurisdiction policy** (U36). Separate
  finding; flagged, not decided here.

### 伦理义务 (Ethical obligation) — what is owed to the person
- **Minimize** PII in the immutable layer. An audit chain that records *principal
  identities + actions* inevitably holds personal data about the humans the OS
  serves/acts for. The ethical default is: store the minimum attribution needed
  for accountability, and keep the bulk of subject content in per-subject-
  encrypted, erasable copies.
- **Do not mislead.** A system that can never erase a person's data, yet presents
  itself as "privacy-protected," violates the subject's reasonable expectations.
  Honest disclosure (both directions) is the minimum ethical floor — which is
  exactly what the docstring + test guard enforces.

### 最佳实践 (Best practice) — how mature systems resolve it
- **Per-subject encryption** for erasable copies, so erasure = key destruction
  that *actually* removes the subject's readable data (acknowledging §3: the
  audit attribution residue still proves *that* something happened — manage that
  residue under a documented Art. 17(3)(c) legal-claims exemption).
- **Shard key = legal decision.** Per-tenant sharding makes a legal hold on one
  tenant trivial and crypto-shred scoped; correlation-id sharding makes both
  nearly impossible. The shard-key choice (ADR-audit-storage-generation-2 §4.2.1)
  is a **legal decision wearing an engineering costume** (U52) — owner call.
- **Data-subject-rights interface** that, on `request_erasure(subject_id)`,
  either (i) crypto-shreds the subject's keys, or (ii) records the request as a
  permanent, audited acknowledgement (the request itself is logged) while
  preserving originals for the legally-required period, with the exemption basis
  recorded.

---

## 5. Honest-disclosure obligations (concrete, in-force now)

1. **Two-sided retention disclosure** — the "never delete originals" invariant
   must be stated as *both* an evidence-preservation guarantee *and* an erasure
   limitation. ✅ Implemented in `lifecycle.py` (`RecordClass`) and `manager.py`
   module docstrings; locked by `test_retention_erasure_conflict.py`.
2. **No silent erasure surface** — `RetentionManager` exposes no
   `erase`/`forget`/`delete_subject`/`request_erasure` method. ✅ Locked by the
   test guard (absence + forced-decision on future addition).
3. **No uncaveated outward erasure claim** — blocked by the `DATA-SUBJECT-RIGHTS`
   lint rule; `--all` scan currently PASS (114 md files). ✅ Locked.
4. **Legal-hold volatility stays disclosed** — the hold is VOLATILE / latent and
   the limitation must not be removed until the registry is durable. ✅ Locked by
   `test_retention_legal_hold_volatility.py`.
5. **"Crypto-shred ≠ erasure"** must be placed in front of the owner as part of
   HD-06, not buried. ✅ Now in the retention docstrings + this memo.

---

## 6. Recommended framework (owner-decision-ready — NOT auto-implemented)

| Decision | Owner choice | Why it is a legal call |
|---|---|---|
| Erasure mechanism | per-subject crypto-shred OR documented Art. 17(3) exemption for residue | determines what "erasure" legally means here |
| Shard key | per-tenant vs correlation-id | decides legal-hold feasibility + crypto-shred scope (U52) |
| Retention windows | per data class (identity / audit-evidence / telemetry) | irreversible deletion of original assets |
| Legal-hold durability | persist the registry (today VOLATILE) | evidence survives deploys |
| Cross-border posture | jurisdiction policy + transfer mechanism | GDPR Ch. V / PIPL |
| Liability regime | who bears liability for autonomous actions (U37) | external contracts/liability reserved to owner |

**Team position:** the team builds whatever policy the owner sets; it does **not**
pick the policy. Until HD-06 is decided, the honest posture stands: audit =
telemetry / evidence-in-progress (HC-01 runtime UNVERIFIED), **no asserted
compliance**, **no asserted liability transfer**, **no asserted erasure**.

---

## 7. What was changed vs what was left to the owner

- **Changed (disclosure only, no policy, no logic):** `lifecycle.py` and
  `manager.py` docstrings now state the erasure limitation and name HD-06/U36;
  new test `test_retention_erasure_conflict.py` locks the disclosure and the
  no-silent-erasure-surface invariant.
- **Left to owner (HUMAN DECISION):** the actual erasure mechanism, retention
  windows, shard key, legal-hold durability, cross-border posture, liability
  regime. These are sovereignty/legal boundaries, not engineering tasks.
