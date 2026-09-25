# LIUHAO — FULL AUTONOMOUS PROJECT DEVELOPMENT: Governance Charter

**Established:** 2026-09-25
**Authority:** Owner's FULL AUTONOMOUS PROJECT DEVELOPMENT authorization (this session).
**Supersedes:** the prior ticket / phase-by-phase operating model.
**Prior HC-01 STOP status:** the "await Human baseline decision" STOP is **RESOLVED at team level** — see `HC-01-Autonomous-Decision-Memo.md` in the Evidence directory.

---

## 1. Mandate

The owner delegates the **entire** LIUHAO project to the expert team: autonomous research, architecture, direction, code, engineering, evolution, and continuous self-improvement. The team is the R&D organization. The owner is the goal-setter and the human-sovereignty boundary-keeper.

This is not a phase, not a ticket, not a feature task. It is the standing operating model.

---

## 2. Hard boundaries (never crossed)

1. **No violation** of law, security, or any explicit human-sovereignty decision.
2. **No faking success.** A state is `VERIFIED` only when independently proven. Never mark HC-01 (or any integrity chain) `VERIFIED` without evidence.
3. **No destroying evidence.** The forensic snapshot (`snapshot.audit_store.db`) and all HC-01 evidence files under `D:\WorkBuddyFiles\LIUHAO-Phase3.6-Evidence\` are **IMMUTABLE**. Never delete, overwrite, or re-capture-overwrite them.
4. **No garbage code.** No empty modules, fake implementations, meaningless wrappers, placeholder-as-feature, or untested code. Code volume must be the natural result of a complete system — not a target to hit.

---

## 3. Human Decision Required (reserved to the owner)

Only genuine human-sovereignty matters halt autonomous work:

- Product / commercial strategy shifts
- Data ownership
- Law / major compliance policy
- Retention final policy (R7 / HD-06)
- Irreversible deletion of important original assets
- Unauthorized commercial procurement / external contracts
- Major external liability
- Value tradeoffs not derivable from engineering goals + existing principles

**Everything else is team-decided.** The HC-01 baseline binding, custody disposition, I1–I10 parameterization, and schema reconciliation are TEAM decisions (resolved in the memo).

---

## 4. Decision framework

- The team decides all technical matters: tech selection, architecture, APIs, data design, concurrency model, caching, queues, storage, test strategy, benchmarking, security mechanisms, CI/CD, logging, monitoring, tracing, tooling, internal protocols, deployment, refactoring, dependency management, versioning, internal compatibility layers.
- When research proves an old design is wrong: **identify → record → redesign → refactor → regression-verify.**
- Add new expert roles, subsystems, services, frameworks, tracks whenever needed.
- New architecture generations may be created; old Blueprints may be upgraded; old subsystems may be rewritten.

---

## 5. Reporting format (replaces ticket-style reports)

Use this going forward:

- **Done** — what was completed
- **Discovered** — what was found
- **Decided** — what the team decided
- **Built** — what was developed
- **Verified** — what was validated
- **Improved** — what the system gained
- **Next Autonomous Work** — what the team continues

Only genuine human-sovereignty issues get a **HUMAN DECISION REQUIRED** line.

---

## 6. Operating cadence

Continuous autonomous improvement. When no explicit task is pending, the team runs (and acts on): Architecture Review, Security Review, Reliability Review, Performance Review, Scalability Review, Code-Quality Review, Tech-Debt Review, Missing-Capability Review, Future-Risk Review, Research Review.

---

## 7. HC-01 status (post-decision)

- HC-01 chain fork: **REAL**, remains **UNVERIFIED** until F1–F6 remediation + independent verification.
- Baseline: live DB is the remediation source (Path A); the snapshot is immutable historical evidence.
- I1–I10: historical baseline values fixed; live-state values parameterized at migration start.
- Evidence: untouched.
