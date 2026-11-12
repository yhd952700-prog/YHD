# HUMAN-DECISION-BACKLOG — LIUHAO Phase 3.6+

**Mode:** FULL AUTONOMOUS PROJECT DEVELOPMENT is in effect (owner directive, 2026-09-25, §1–§17).
Engineering work proceeds WITHOUT per-decision owner sign-off. Only **irreversible Human-sovereign**
decisions are isolated here. A backlog entry is transparency, **not a gate** — it must never stop other
engineering (owner directive §2, §9, §16).

## Isolation principle
- STOP THE PROJECT is forbidden.
- Real Human Decisions are ISOLATED, not blocking.
- Build reversible, configurable, provider-agnostic interfaces.
- Keep the safest fail-closed default until the final policy is set.
- The backlog exists for the owner's visibility; the team proceeds on everything else.

## Items (sovereign — isolated, not blocking)

### HD-06 — Data retention final policy
- **Decision:** What is the legally-binding retention period / deletion rule for audit & identity history?
- **Why it matters:** Legal liability, GDPR/PII, regulatory hold. Sovereign (owner) call.
- **Current safe default:** Do NOT delete, overwrite, or destroy original immutable history. Immutable-history
  handling on by default.
- **Engineering that continues:** retention abstraction, policy interface, legal-hold interface, archival
  interface, cold-storage architecture, lifecycle metadata, capacity planning, migration compatibility,
  observability. All policy-configurable; final switch reserved.
- **Irreversible action still blocked:** Actually deleting/overwriting original immutable history per a final
  legal rule.
- **Options:** (A) time-based TTL; (B) indefinite legal-hold; (C) hybrid; (D) jurisdiction-specific profiles.
- **Impact:** None on velocity — architecture is policy-neutral now.
- **Evidence:** owner directive §5.
- **Recommendation:** none (sovereign) — ship policy-neutral switch, default no-delete.
- **Required owner action:** set final retention profile (config only) when ready.

### HD-05 — TSA / TPM (trusted timestamp / platform root-of-trust) provider selection
- **Decision:** Which trusted-timestamp / hardware-root-of-trust provider?
- **Why:** Evidence admissibility, supply-chain trust. Sovereign (owner) call.
- **Current safe default:** Provider-neutral abstraction; mock / local adapter in dev/test; offline
  verification supported.
- **Engineering continues:** signing interface, timestamp interface, evidence adapter, key-lifecycle
  interface, verification interface, offline verification, failure handling, provider-compatibility tests.
- **Irreversible action blocked:** committing to a paid/external provider account or an irreversible key
  ceremony.
- **Options:** (A) local RFC3161-shaped mock; (B) public RFC3161 TSA; (C) TPM/HSM hardware root; (D) multi-provider quorum.
- **Impact:** None on velocity — provider is a config.
- **Evidence:** owner directive §6.
- **Recommendation:** none (sovereign) — ship provider-neutral, default local mock.
- **Required owner action:** pick provider (config) when ready.

### U1 — Autonomous agent host-command capability
- **Decision:** May an autonomous agent execute host commands?
- **Why:** Human sovereignty over what the machine does without a human in the loop. Sovereign (owner) call.
- **Current safe default:** **DENY.** Capability closed; no host-command path reachable by the autonomous actor.
- **Engineering continues:** capability model, policy engine, authorization pipeline, audit trail, sandbox,
  command broker, approval interface, simulation mode, test harness, future enablement gate.
- **Irreversible action blocked:** flipping the production default to ALLOW.
- **Options:** (A) permanent DENY; (B) approve-by-default-off with human-approval gate; (C) sandbox-only
  allowlist; (D) full allow (rejected by default).
- **Impact:** None on velocity — enabling later = policy/config change, not a rebuild.
- **Evidence:** owner directive §7; WorldInterface actor model (actor="autonomous" default-deny, blocks shell=True).
- **Recommendation:** keep DENY; build enablement gate so a future flip is a safe config change.
- **Required owner action:** decide enablement policy (config only) when ready.

### U37 — Legal liability for autonomous actions
- **Decision:** Who bears legal liability when an autonomous agent acts?
- **Why:** legal exposure; product promise of Human Sovereignty.
- **Current safe default:** every autonomous action is fail-closed, audit-logged, and gated; no action reaches
  production without enforced evidence.
- **Engineering continues:** audit trail, evidence chain, policy enforcement, human-override path.
- **Irreversible action blocked:** none at the engineering layer (liability is a legal/contract matter).
- **Options:** (A) owner-liability; (B) agent-liability; (C) shared; (D) insured.
- **Evidence:** owner directive general; UNKNOWN-TO-OWNER.md U37.
- **Recommendation:** none (sovereign) — keep fail-closed + full audit so liability is attributable.
- **Required owner action:** legal/contract decision.

## Previously "LOCKED" decisions — now executed autonomously
Under the FULL AUTONOMOUS mandate these engineering decisions are owned by the team:
- **D19** (HC-09/HC-10 stay VOLATILE) — implemented (commit 1580e3ee). Continues: optional policy-neutral
  persistence adapter; volatile default preserved.
- **D23 / D24** (sovereignty_grant unification + enforcement hardening) — implemented (os-impl).
- **D25** (public-claim doc fixes R1–R7) — implemented (gov-impl). Continues: doc-lint guard evolves;
  **lint ≠ legal compliance** (owner directive §8).

## Engineering workstreams NOT blocked (active)
- **Companion-Wiring Workstream** — fix the 65 broader-suite failures at real root cause
  (`src/kernels/_crosscutting.py:878` audit-channel companion wiring + capability registration), no test
  masking, no fail-closed weakening.
- **Fix B prerequisite research/design** (capability.register bounded carve-out) — keep fail-closed; multiple
  safe paths prototyped behind a default-off flag.
- **HC-01** reliability/verification continues (COMPLIANT).
- **HD-06 / HD-05 / U1** architectures built policy-neutral (above).
- Reliability, Security, Platform, Database, Agent architecture, Performance, Testing, Blueprint evolution
  continue per owner directive §16.

## Status note
The prior `# HOLD` on normal engineering is superseded by the owner's FULL AUTONOMOUS mandate. The repo
branch `p36` remains the working branch. Genuinely irreversible Human-sovereign actions (final retention
deletion, final provider commitment, flipping host-command to ALLOW, deciding legal liability) remain
isolated here and are the only things that require the owner's explicit, irreversible action.

## Built this cycle (2026-09-25, os-impl) — HD-06 & U1
Both architectures are implemented on branch `p36` (staged-pending per autonomous workflow; no push).
Safe fail-closed defaults are wired and unit-tested (58 new tests green).

### HD-06 — retention (built, policy-neutral)
- Reserved switch: `LIUHAO_RETENTION_POLICY` (env). Default mode = `NO_DELETE`.
- Hard guarantee: `RetentionManager` never deletes/overwrites/destroys `IMMUTABLE_ORIGINAL`; the guard
  fires (`immutable_protected` counter) even if an aggressive policy asks to delete an original.
- Provider-neutral storage: local-FS cold storage now; `ObjectStorageArchiveTarget` adapter-ready
  (fail-closed if no backend injected). No legal retention period decided in code.
- **Sovereign decision still required:** the final retention period / deletion rule — set `LIUHAO_RETENTION_POLICY`
  and (if `DELETE_AFTER_DAYS`) the day count when ready. Default stays no-delete until then.

### U1 — host-command (built, default-DENY)
- Reserved switch: `LIUHAO_HOST_COMMAND_ENABLED` (env). Default = OFF (DENY).
- Broker pipeline: (1) enablement gate OFF→DENY; (2) capability policy (empty catalog = deny-all);
  (3) human-sovereign approval escalation (one-shot grant, `granted_id_for` correlation);
  (4) execute (or simulate / ERROR if no executor).
- `WorldInterface` actor="autonomous" already flips default to DENY and blocks `shell=True`.
- **Sovereign decision still required:** whether (and which capabilities) to ever flip
  `LIUHAO_HOST_COMMAND_ENABLED` to ON. Until then production stays DENY; a future flip is a config
  change, not a rebuild.
