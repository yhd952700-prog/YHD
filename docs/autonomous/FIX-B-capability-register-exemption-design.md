# Fix B — Bounded `capability.register` Exemption: Research & Design

**Author:** sec-impl (Chief Security Architect)
**Status:** Preliminary research / design (ASSIGNMENT 2). Not yet merged to source.
**Hard constraint:** Must NOT weaken the core security boundary. Production default
MUST remain fail-closed. The exemption, when enabled, is narrowly scoped, recorded,
and fail-closed in every unspecified case.

---

## 0. Problem statement (why Fix B exists)

`capability.register` is itself a governed action:

```python
# src/kernels/capability/__init__.py:116
@kernel_action("capability.register")
def register(self, capability: CapabilityEntry) -> bool: ...
```

So every capability registration passes through the cross-cutting decorator's
mandatory-evidence gate (CRIT-1C Layer 2, `src/kernels/_crosscutting.py:850-878`):

- If the action's risk tier is `_evidence_mandatory` **and** no audit channel is
  available → raise `PolicyDeniedError` (never runs).
- If the pre-execution audit write fails for any reason → raise `PolicyDeniedError`.

The global registry is built by `get_capability_registry()` →
`_register_builtin_capabilities()` → `registry.register(...)` for each of the 12
kernel builtins **plus** `python_compute`. Therefore:

> If the audit evidence channel is unavailable at registry-construction time
> (test/CI, or early boot before the audit writer is armed), **every** builtin
> registration is denied → the registry is empty → every capability-scoped action
> fails with "Capability not found" → the system cannot boot and the test suite
> cannot run.

This is a **liveness/availability failure caused by a fail-closed control applied at
the wrong layer for a *meta/infrastructure* action.** Fix B asks: can we grant a
**bounded, explicitly-configured** exemption for `capability.register` that does
**not** weaken the boundary that governs real, externally-invokable actions?

**Boundary that must stay intact:** the mandatory-evidence gate continues to govern
*every* action except, possibly, the single meta-action `capability.register` under
a narrow, recorded, default-off carve-out. Registration is not a sovereign or
human-decision action; it is bookkeeping that the rest of the system depends on.

---

## 1. Threat Model

Structured as **attacker → motive → path → harm → likelihood**.

### T-1 — Malicious capability registration evading the evidence chain
- **Attacker:** any principal able to reach `capability.register` (or influence its
  inputs).
- **Motive:** establish a privilege-escalation primitive that is invisible to the
  audit trail.
- **Path:** with the exemption enabled and the pre-execution audit write removed,
  register `kernel.superuser` (scope L7) or re-register an existing capability with
  a widened scope, without a blocking audit row.
- **Harm:** a capability the rest of the gate trusts (e.g. `security_enforcement`,
  `policy_engine`) is shadowed/overridden; downstream invocations of that capability
  are authorized based on a capability that was never evidenced.
- **Likelihood:** LOW if the exemption's *bounds* (§7A) restrict it to builtin
  kernel-namespace ids only. MEDIUM-HIGH if the bounds are permissive or if the
  allowlist is editable at runtime.

### T-2 — Exemption as an evidence-laundering channel
- **Attacker:** a principal who wants to perform a dangerous action but needs it to
  look clean.
- **Motive:** register a benign-named capability that maps to a dangerous tool, then
  invoke it; the *invocation* is audited but the *registration* (which created the
  dangerous mapping) is not.
- **Harm:** the audit trail shows the action but not the capability definition that
  made it possible; forensic reconstruction is incomplete.
- **Likelihood:** LOW under T-1 bounds; the mitigation is **always record the
  registration, never skip the record** (§4).

### T-3 — Production flag tampering
- **Attacker:** operator / supply-chain / config-injection.
- **Motive:** silently flip the system to the exempt (weaker) posture.
- **Path:** set `CAPABILITY_REGISTER_EVIDENCE_EXEMPT=1` (or equivalent) in the
  deployment environment.
- **Harm:** the boundary is weakened in production without review.
- **Likelihood:** depends on deployment hardening; mitigated by §8 (default OFF,
  flag-source allowlist, startup assertion that production uses the closed posture).

### T-4 — Audit-outage abuse window
- **Attacker:** observes that audit is down and the exemption is enabled.
- **Motive:** register during the blind window.
- **Path:** exemption ON + audit down → register proceeds without blocking write.
- **Harm:** a registration event exists only in the volatile pending buffer; if the
  node dies before reconciliation, the event is lost.
- **Likelihood:** LOW if §3 reconciliation is durable and surfaced.

---

## 2. Attack Surface (what the exemption opens)

| # | Surface | Exposure | Mitigation |
|---|---------|----------|------------|
| S-1 | `capability.register` entry point | Who may call it? Is it itself gated by an outer capability/actor check? | Keep the *outer* authorization on `capability.register` intact; the exemption only removes the *inner* blocking audit write, not the outer authz. |
| S-2 | Config flag source | Can it be set at runtime / via env / via untrusted config? | Single source of truth; read once at boot; rejected if set after startup; production asserts closed. |
| S-3 | Bounds predicate (allowlist) | If the namespace/id allowlist is too wide, the exemption becomes a wide hole. | Allowlist is a **compile-time constant**, not runtime-editable (§7A). |
| S-4 | Audit-write fallback | Does the exempt path silently drop the record, or write a degraded one? | Never drop; write a tagged, best-effort record + durable pending buffer (§4, §3). |
| S-5 | Flag value parsing | Unknown/garbage flag value → fail-open? | Unknown → treat as OFF (fail-closed). |

---

## 3. Recovery when audit is *truly* down

When the exemption is enabled and the audit backend is unavailable:

1. Registration **proceeds** (by design — this is the whole point of the bounded
   carve-out).
2. The registration event is written to a **durable, ordered pending-evidence
   buffer** (append-only file or a side SQLite table `pending_exempt_events`),
   tagged `exemption=capability_register_bounded`, carrying the full capability
   entry + actor fingerprint + correlation id.
3. A **reconciliation worker** (runs on audit-writer recovery and on a watchdog
   timer) replays the pending buffer into the real hash-chain audit store, marking
   each row `reconciled=True` and preserving original timestamps.
4. If the node dies before reconciliation, the pending buffer survives on disk; on
   restart, reconciliation replays it. **No event is silently lost.**
5. **Fail-safe:** if audit is down AND the exemption is OFF (default) → registration
   is blocked (current behavior, correct). The exemption only changes behavior when
   explicitly enabled.

---

## 4. Audit behavior under the exemption (the core principle)

> **Exempt from fail-closed *blocking*, never exempt from being *recorded*.**

Under the exemption:
- The pre-execution **blocking** audit write is removed for `capability.register`
  only.
- The registration is still recorded — but as a **non-blocking, tagged** event:
  - best-effort immediate write if the channel is up;
  - otherwise into the durable pending buffer (§3).
- Every such row carries `exemption=capability_register_bounded` so a reviewer can
  see at a glance that the blocking gate was not applied.
- The registered capability's **own** invocations remain fully governed — registering
  `kernel.superuser` does not make `kernel.superuser` invocations exempt; they still
  hit the gate.

This is the key distinction from "weakening the boundary": the boundary still
*observes and records*; it merely does not *prevent* this one meta-action when the
operator has explicitly opted in.

---

## 5. Regression plan

Tests that must exist and stay green:

- **R-1 (default posture unchanged):** with the flag OFF (default), `capability.register`
  with no audit channel → still `PolicyDeniedError`. *(No behavior change.)*
- **R-2 (exempt + audit up):** flag ON, channel up → register succeeds AND exactly
  one audit row exists, tagged `exemption=capability_register_bounded`.
- **R-3 (exempt + audit down):** flag ON, channel down → register succeeds; row is
  in the pending buffer; after channel recovery, reconciliation moves it into the
  hash-chain with `reconciled=True`.
- **R-4 (bounds enforced):** flag ON but the capability is NOT in the allowlist
  (e.g. a `core.*` or attacker-chosen id) → still `PolicyDeniedError` (bounds hold).
- **R-5 (flag tamper in prod):** a startup assertion fails the boot if production
  config enables the exemption → forces an explicit, reviewed decision.
- **R-6 (no silent drop):** a metric/counter `exempted_unaudited_events` is
  non-zero only while audit is down; alert fires; never resets to zero without a
  reconciliation.

---

## 6. Baseline (current behavior — do not regress)

Current (pre-Fix-B):
- `capability.register` is `@kernel_action("capability.register")`, subject to the
  mandatory-evidence gate.
- No audit channel → registration denied → builtins not registered → registry empty.
- `get_capability_registry()` returns an empty/partial registry until the channel is
  available.
- The C-6 guard `scripts/verify_armed_actions_are_inert.py` is independent and must
  stay GREEN.

Fix B must keep R-1 and the C-6 guard GREEN; it only adds an *opt-in* path.

---

## 7. Alternative designs (≥2)

### Design A — Config-gated bounded exemption (CHOSEN / safest)
- Flag `CAPABILITY_REGISTER_EVIDENCE_EXEMPT` (default **off**).
- When ON, `capability.register` for **allowlisted** capabilities (builtin
  `kernel.*` ids only, per a frozen constant list) skips the pre-execution
  *blocking* write but writes a non-blocking, tagged record + pending buffer.
- All other actions and all non-allowlisted registrations are unaffected.
- **Pros:** narrow, recorded, default-off, fail-closed in every unspecified case.
- **Cons:** requires a frozen allowlist; if a new builtin is added, the list must be
  updated (acceptable — builtins change rarely and go through review).

### Design B — Bootstrap-epoch carve-out (REJECTED as primary)
- Define a "bootstrap epoch": during early boot, register runs in
  `bootstrap_audit_buffer` mode; evidence is buffered and flushed once the audit
  writer is armed; afterwards the gate is fully enforced.
- **Rejected because:** the epoch is hard to bound. If an attacker (or a bug) keeps
  the system in "boot" state, the exemption persists indefinitely. Epoch exit must
  be provably one-way and monitored.

### Design C — Decouple register from the gate entirely (REJECTED)
- Make `capability.register` not a `@kernel_action` at all.
- **Rejected because:** removes *all* audit from registration — unacceptable;
  registration is exactly the event an attacker wants invisible.

### Design D — Lower `capability.register` risk to LOW/MEDIUM (REJECTED)
- Reclassify so the gate never treats it as mandatory-evidence.
- **Rejected because:** a privilege-affecting meta-action being LOW risk lets a
  malicious register evade mandatory evidence — directly weakens the boundary.

Design A is chosen: it is the only option that is *recorded-by-default, blocked-
only-when-opted-in, and bounded by a frozen allowlist*.

---

## 8. Fail-safe design

- **F-1 Default OFF.** Missing/garbage flag value → treated as OFF (fail-closed).
- **F-2 Allowlist frozen.** The permitted-capability set is a compile-time constant;
  cannot be widened at runtime without code change + restart.
- **F-3 Action-name pinned.** The exemption applies **only** to the literal action
  name `capability.register`. Any other action name is never exempt.
- **F-4 No scope downgrade.** The exemption does not lower the risk classification
  of the *registered* capability; its invocations remain governed.
- **F-5 Durable pending buffer.** Audit-down registrations survive restart and are
  reconciled; never silently dropped.
- **F-6 Loud blind-window.** While audit is down under the exemption, a counter
  `exempted_unaudited_events` is non-zero and alerted; reconciliation clears it.
- **F-7 Production assertion.** A boot check fails if production config enables the
  exemption, forcing a conscious, reviewed decision (isolated to
  `HUMAN-DECISION-BACKLOG.md` if needed).

---

## 9. Prototype (behind default-fail-closed flag — sketch, NOT yet in source)

```python
# Conceptual sketch only. Default EXEMPT=False => zero behavior change.
CAPABILITY_REGISTER_EVIDENCE_EXEMPT = os.environ.get(
    "CAPABILITY_REGISTER_EVIDENCE_EXEMPT", ""
).strip().lower() in ("1", "true", "yes")

# Frozen allowlist — builtin kernel capabilities only.
_REGISTER_EXEMPT_ALLOWLIST = {
    ("kernel", "context_compression"), ("kernel", "capability_registry"),
    ("kernel", "event_bus"), ("kernel", "execution_pipeline"),
    ("kernel", "resource_quotas"), ("kernel", "policy_engine"),
    ("kernel", "network_bus"), ("kernel", "trust_chain"),
    ("kernel", "evaluation_engine"), ("kernel", "agent_identity"),
    ("kernel", "multi_tier_memory"), ("kernel", "security_enforcement"),
    ("kernel", "python_compute"),
}

def _evidence_mandatory_for_register(action, ns, cap_id):
    """True only for the single meta-action, only when exempt+allowlisted."""
    if action != "capability.register":
        return True  # untouched
    if not CAPABILITY_REGISTER_EVIDENCE_EXEMPT:
        return True  # default: fully governed (no change)
    return (ns, cap_id) not in _REGISTER_EXEMPT_ALLOWLIST

# In the gate (src/kernels/_crosscutting.py:850), replace the unconditional
# `_evidence_mandatory(effective_risk)` test with
# `_evidence_mandatory_for_register(action, namespace, capability_id)`
# so that the blocking write is skipped ONLY for the exempt+allowlisted case,
# while a best-effort, tagged, non-blocking record + pending buffer is always written.
```

**Implementation note:** the actual edit must thread `(namespace, capability_id)`
into the gate. Until then this stays a sketch; the source change is deferred to a
reviewed PR after A1 lands and the suite is green, so we never touch the security
gate while the baseline is unstable.

---

## 10. Human-sovereign decisions to isolate

- Whether to EVER enable `CAPABILITY_REGISTER_EVIDENCE_EXEMPT` in any non-test
  environment (recommendation: never in production; test/CI only).
- The exact allowlist contents when a new builtin capability is added.
- Any future move from Design A to Design B (bootstrap epoch) — rejected here, but
  if reconsidered, requires owner sign-off.

→ Recorded in `docs/autonomous/HUMAN-DECISION-BACKLOG.md` if the owner wants them
formally queued.

---

## 11. Status summary

| Deliverable | Status |
|---|---|
| Threat model (T-1..T-4) | ✅ produced |
| Attack surface (S-1..S-5) | ✅ produced |
| Audit-down recovery | ✅ designed (durable pending buffer + reconciliation) |
| Audit behavior under exemption | ✅ defined (record-never-drop principle) |
| Regression plan (R-1..R-6) | ✅ defined |
| Baseline | ✅ captured (§6) |
| ≥2 alternative designs | ✅ A chosen, B/C/D considered/rejected |
| Fail-safe design (F-1..F-7) | ✅ defined |
| Prototype behind default-off flag | ⚠️ sketch only; source edit deferred until A1 green |
