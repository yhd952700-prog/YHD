# HUMAN DECISION REQUIRED — HD-06 (retention final policy) & U37 (autonomous-action liability)

> **Status:** HUMAN DECISION REQUIRED — NOT auto-executed by the autonomous team.
> **Raised by:** gov-impl (governance-legal domain), 2026-09-26.
> **Related register entries:** U36 (missing PII / GDPR / CCPA compliance framework), U37 (undefined autonomous-action legal-liability / accountability framework), HD-06 (retention final policy).
> **Hard boundary honored:** the owner explicitly reserved data ownership, law/compliance policy, retention final policy, and external contracts/liability to themselves (EXECUTION-QUEUE §Standing). The team will not auto-decide any of these.

---

## HD-06 — Retention final policy

- **What:** The system persists human-identity and audit/operational records (SQLite under `src/kernels/identity`, `src/kernels/audit`), but the **final retention policy** — how long each record class lives, when/how it is erased, and legal-hold handling — is undecided.
- **Why it is a HUMAN DECISION:** retention touches data ownership, legal/compliance policy, and irreversible deletion of original assets — all explicitly reserved to the owner in EXECUTION-QUEUE §Standing.
- **Options for owner to choose:**
  1. Define retention windows **per data class** (human-identity vs audit-evidence vs operational telemetry).
  2. Define **erasure / right-to-be-forgotten** handling (urgent given PII in the pipeline + audit trail — see U36).
  3. Define **legal-hold / forensic-snapshot preservation** rules.
     - **Note:** the forensic snapshot `snapshot.audit_store.db` and all `D:\WorkBuddyFiles\LIUHAO-Phase3.6-Evidence\` files are **IMMUTABLE evidence** and must **never** be deleted or overwritten, even under a retention/erasure policy.
- **Team position:** the team can *implement* whatever policy the owner sets, but must **NOT** pick the policy.

---

## U37 — Legal liability / accountability for autonomous-agent actions

- **What:** LIUHAO agents execute bounded autonomous authority. There is **no framework assigning legal liability** when an autonomous action causes harm, nor any mapping of authorization/attribution records to external (human / contractual) responsibility.
- **Why it is a HUMAN DECISION:** `external contracts/liability` is explicitly a HUMAN DECISION REQUIRED boundary (EXECUTION-QUEUE §Standing). The owner's delegation of *engineering* to the team does **not** transfer *legal liability*.
- **Options for owner to choose:**
  1. Affirm the owner (individual/entity) bears ultimate liability for autonomous actions, and set a **human-oversight / approval threshold** for high-impact actions.
  2. Define the **contractual / insurance** posture for agent-caused harm.
  3. Decide whether/how the **HC-01 audit chain** is treated as legally sufficient evidence vs supplementary telemetry.
- **Team position:** the team builds the technical audit trail (HC-01) but **cannot declare the legal liability regime**. This must come from the owner.

---

## Combined recommendation

- Both items are sovereignty/legal boundaries. The team should **NOT** proceed to implement data-protection compliance, retention policy, or liability assignment on its own. Await owner decision before any of these move from `DISCOVERED` to `Built`/`Resolved`.
- Until decided, the team's honest posture stands: audit = telemetry / evidence-in-progress (HC-01 runtime UNVERIFIED), **no asserted compliance**, **no asserted liability transfer**.
