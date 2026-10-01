# LIUHAO — PRODUCT READINESS GATE (P1–P10)

> Generated: 2026-10-01 · Branch `p36` · Evidence date: 2026-10-01
>
> **Honesty discipline (non-negotiable):** each gate is one of
> `PASS` / `FAIL` / `BLOCKED` / `NOT YET TESTED`.
> **"Code exists" is never evidence.** A gate is `PASS` only when a real user
> outcome was produced and observed. `NOT YET TESTED` means we have not run it —
> it is not a softer `PASS`.
>
> This is the **product** gate. It is deliberately separate from the engineering
> gate (`scripts/readiness_report.json`, C1–C12). Strong engineering gates do
> **not** imply a working product.

## Verdict summary

| | |
|---|---|
| **PASS** | **0** |
| **FAIL** | **9** |
| **BLOCKED** | 0 |
| **NOT YET TESTED** | 1 |
| **PRODUCT ACCEPTANCE** | **NOT READY** |

The single most important fact in this document: **the one end-to-end user
workflow we were able to actually run returned `SUCCESS` without doing the
work.** A file-creation goal reported `completed` + `SUCCESS` and the file was
never created. See P6.

---

## P1 — AI Employee — **FAIL**

**Requires:** a real, persistent roster of AI employees with lifecycle (hire /
pause / resume), each with identity and capabilities, visible and manageable by
the user.

**Evidence:**
- `src/gateway/ai_management.py:141-156` `_ensure_employee()` **hardcodes** the
  only employee: `Employee(name="liuhao-default", agent_count=3,
  agent_types=["planner","executor","critic"])`. Synthesised, not persisted.
- No `EmployeeStore` / `employee_store` exists anywhere (repo-wide grep).
- `_goals` is an **in-process dict** (`ai_management.py:124`) — lost on restart,
  not shared across workers.
- The UI's "My AI Employees" is a capability-registry viewer: `roster.py:289`
  computes `employees = kernels + layers = 28`. The product therefore displays
  **"28 AI employees" when there are zero employees.**

**Biggest gap:** there is no employee persistence layer at all.
**Flips to PASS when:** a real employee can be created, persists across restart,
can be paused/resumed from the UI, and appears by name — not as a count of
kernel modules.

---

## P2 — Task Planning — **FAIL**

**Requires:** a high-level user goal decomposed into concrete, tool-shaped tasks
that an executor can actually run.

**Evidence:**
- `GoalDecomposer.decompose` (`src/kernels/execution/__init__.py:190`) is a
  **keyword table**; its own comment states that "in production would use LLM".
- The real LLM-backed planner `src/ai/goal_task_graph.py` (846 lines) is an
  **orphan — zero references anywhere in `src/`.**
- Decomposition passes `{"goal": <the whole sentence>}` to capabilities, not
  tool-shaped inputs (`code` / `path` / `content` / `expression`). An executor
  receiving this cannot act on it.

**Biggest gap:** no planner is wired into any user path.
**Flips to PASS when:** a real goal is decomposed into executable tasks with
tool-shaped inputs and those tasks actually run.

---

## P3 — Multi-Agent Collaboration — **FAIL**

**Requires:** more than one AI employee working a shared goal, with results
combined.

**Evidence:** `src/ai/collaboration.py` and the related orchestration modules
have **zero callers** in `src/` (orphaned). No user path reaches them. Nothing
was executed, so this cannot be `NOT YET TESTED` — the capability is
demonstrably unreachable.

**Biggest gap:** collaboration exists as code with no caller.
**Flips to PASS when:** a goal is genuinely worked by ≥2 agents and the combined
result is observable end to end.

---

## P4 — WorldInterface — **FAIL**

**Requires:** agents observe and act on the real world through registered,
permission-checked adapters.

**Evidence:**
- `src/ai/world_interface.py` implements the §37 contract (observe / validate /
  authorize / execute / verify) with `FilesystemAdapter` (`:58`) and
  `ShellAdapter` (`:108`).
- **Zero `register_adapter` calls exist** anywhere in `src/` or `apps/`. No
  adapter is ever registered, so the interface is unreachable in practice.
- No sandbox boundary was demonstrated for either adapter.

**Biggest gap:** adapters are implemented but never wired.
**Flips to PASS when:** at least one adapter is registered and a real sandboxed
action (read/write a file, run a command) executes under an enforced permission
check.

---

## P5 — Human Sovereignty UX — **FAIL**

**Requires:** the human can see, approve, and stop autonomous action — and the
system fails closed when they do not.

**Evidence:**
- `@kernel_action` is **record-only by default** (`enforce=False` at all 43
  sites). Measured live: `kernel_action=capability.register outcome=success
  policy=deny risk=HIGH` — policy said deny, the action ran, and `success` was
  written to the chain. **"Policy controlled" ≠ "policy enforced".**
- The executor fence is real but **disarmed by default** (`LIUHAO_EXECUTOR_FENCE`
  unset); when armed it **dies on boot** (`identity.create_identity` → 0/24
  checks, RC=1).
- `POST /v1/policy/approvals` records approvals that are **inert** — measured to
  have zero enforcement effect.
- Policy decisions are **not audited** (`evaluate()` has no `@kernel_action`),
  and `unregister_rule()` can delete `default_deny` / `human_sovereignty`
  silently.
- HC-01 records **denials as successes** (30 of 34 records self-contradict), so
  the evidence a human would consult actively misleads them.

**Biggest gap:** sovereignty is recorded but not enforced, and the record lies.
**Flips to PASS when:** enforcement is armed by default, the fence survives
boot, an approval actually gates an action, and the chain tells the truth.

---

## P6 — End-to-End Execution — **FAIL** (proven false pass)

**Requires:** user submits a goal → real work happens → real artifact.

**Evidence (reproducible probe):**
```
POST /v1/goals  "Create a file /tmp/.../hello.txt containing 'hello world'"
  -> state: completed        evaluation: SUCCESS
  -> FILE CREATED?  False
```
- `src/gateway/ai_management.py:134` builds `AgentRuntime(scope="L1")` with **no
  `capability_executor`** — the production path is unwired.
- With no executor, `_simulate_capability` (`kernels/execution/__init__.py:451`)
  returns `{"status":"simulated"}` with **`ok=True`** (`:398-401`).
- The mechanism is good when wired: injecting
  `LCore(register_local_tools=True).capability_executor()` really executed
  `sum(range(1,101))` → `5050`, and honestly returned `success=False` for an
  unbacked capability.
- `agent_runtime.py:272-276` still reports `COMPLETED` even at score 0.0.

**Biggest gap:** unwired executor that reports success anyway.

**Severity ceiling — the worst defect in this report.** Every item in
cross-cutting #6 is *the audit record lying*. This one is **the return value
lying**: the caller receives a fabricated result and acts on it. That is one
grade worse, and it sets the ceiling for the whole P1–P10 assessment.
**Flips to PASS when:** the production path produces a real artifact (assert file
exists with expected content, not `state == "completed"`), and an unwired
executor **fails** instead of succeeding.

---

## P7 — Recovery — **NOT YET TESTED**

**Requires:** a failed or interrupted task is detected, recovered or reported —
and the user sees it.

**Evidence:** the kernel-level primitive IS real — `ExecutionJournal` +
`_adopt_journaled_progress` (`kernels/execution/__init__.py:729`) genuinely
resumed both tasks after a crash (measured), and audit-store corruption handling
quarantines and restores. But the **product-level** loop is not proven, and two
recovery paths are known-bad: `execute_replan` is a status-flip stub that never
re-executes, and `retry_dead_letter` returns `True` while failing again.

**Why not FAIL:** the underlying primitive demonstrably works; we simply have not
run a user-visible recovery scenario.
**Flips to PASS when:** an interrupted goal is resumed or honestly reported to the
user, end to end.

---

## P8 — Explainability / Audit UX — **FAIL**

**Requires:** a user can see, for any action, what happened, why, and prove it
was not altered.

**Evidence:**
- The **audit mechanism is genuinely REAL-USABLE**: append →
  `verify_integrity() = (True, 5)`; tamper row 3 via raw SQL → `(False, 5)`;
  corruption → rollback + quarantine (never delete) + restore + raise.
- **But there is no user-facing surface**: no HTTP endpoint and no CLI to query or
  verify the chain (only `src/gateway/dashboard.py:66` summary + internal
  `audit_query()`).
- The UI's audit section is labelled **"数据中心"** (data centre) — users will not
  look there for an audit trail.
- **Critically: the chain's content is not trustworthy.** Combined measured sample
  across both kernel audits: **41 of 48 HC-01 records (85%) assert
  `policy=deny` while `outcome` reads `success`/`intent`**; **zero** records had
  `enforced=True`.
  **Tamper-evidence is real; truthfulness is not. These are two separate claims
  and only the first is earned.** A chain that can prove it was *not altered*
  while its content is false is **more dangerous than an unprotected chain**,
  because it passes the integrity check and is then accepted as trustworthy
  evidence. Consequence: **HC-01 cannot currently be used for any compliance or
  evidentiary purpose.**
- External anchoring (`external_anchor.py`) is real RFC-6962 math, but the only
  shipped transport is `LocalReferenceLog`; `ExternalLogTransport.submit_root`
  raises `NotImplementedError` — **no real third-party transparency log.**

**Biggest gap:** no user-facing query/verify, and the record misstates outcomes.
**Flips to PASS when:** a user can query and independently verify the chain, and
`outcome` reflects what actually happened.

---

## P9 — Permissions / Identity UX — **FAIL**

**Requires:** the user can see and control what each AI employee is allowed to do.

**Evidence:**
- The **identity kernel is REAL-USABLE**: real enforcement of lifecycle, scope
  ceiling, principal uniqueness, and human/agent namespace disjointness;
  fail-closed registry integrity.
- But it is an **authorization registry, not authentication** — no credentials,
  no token issuance, no MFA, no key rotation in the kernel.
- Default config leaves both persistence env vars unset ⇒ human registrations are
  **memory-only and vanish on restart**.
- `grant_permission`'s scope-ceiling refusal is recorded on HC-01 as
  `outcome=success` — a false success on the authoritative chain.
- The UI exposes exactly **4 write actions** total (send chat, record approval,
  revoke approval, log out). None manages permissions.

**Biggest gap:** identity is never surfaced to the user, and its refusals are
recorded as successes.
**Flips to PASS when:** permissions are visible/manageable in the UI and refusals
are recorded truthfully.

---

## P10 — Real User Workflow — **FAIL**

**Requires:** a real user can accomplish a real job through the product.

**Evidence:** the only proven end-to-end workflow returns `SUCCESS` without
performing the work (P6). The entire UI supports 4 write actions. There is no way
to assign a task, receive a file, or create a project. `Tasks`, `Projects`,
`Files` and `Apps` have **no UI at all** despite backend routes existing
(`POST /v1/goals`, `/v1/workflows`, `workspace.py`, `PluginRegistry`) and
frontend contract types already declared but unconsumed
(`apps/console/console/src/lib/contracts.ts:206-266`).

**Biggest gap:** the product cannot complete a real job.
**Flips to PASS when:** a named realistic user job is completed end to end with a
verifiable artifact.

---

## Cross-cutting blockers (each caps multiple gates)

1. **No kernel exports real metrics.** `goal_decompositions_total`,
   `goal_tasks_generated`, `task_execution_total`
   (`src/observability/metrics.py:155-183`) are declared with **zero call sites**.
2. **No WorldInterface adapter is registered** (P4).
3. **Policy recorded but not enforced** by default (P5).
4. **No persistence** across capability, context, evaluation, event, resource,
   security, trust — state resets on restart; revoked trust "resurrects".
5. **No identity/permission check** on writes in memory, network, resource,
   security, trust, and execution (execution checks capability scope only, never
   identity).
6. **The audit chain records refusals as successes.** Publish **both** figures
   with the explanation — either one alone is attackable:
   - **Row level: 41 of 48 HC-01 records (85%)** carry `policy_decision=deny`
     while `outcome` reads `success`/`intent`.
   - **Action level: 27 of 33 actions (82%)** were denied yet recorded as success.
   - *Why they differ (explained, not fudged):* HIGH-risk actions write **two**
     rows (mandatory-evidence `intent` + `success`); MEDIUM/LOW write one. HIGH is
     therefore double-counted at row level.
   - **Cleanest single piece of evidence — lead with this:** `resource.allocate`
     invoked deliberately as an ordinary business operation returned success,
     executed for real, and the chain recorded `policy=deny / outcome=success`.
     One action, one row, unambiguous. The 13× `capability.register` per boot
     (= 26 rows per process start) is the **multiplier**, not the proof —
     bootstrap can be waved off as "init noise"; an ordinary allocation cannot.
   - **Worst structural asymmetry:** MEDIUM-risk denials leave a **single isolated
     `success` row with no paired `intent`** — nothing on the chain even hints a
     decision was refused. MEDIUM is exactly the tier covering identity creation,
     quota allocation, route changes and replan approval
     (`identity.create_identity`, `resource.create_quota`, `network.add_route`,
     `evaluation.approve_replan` / `execute_replan`). So *"who was created, who got
     resources, how the topology changed, who approved the replan"* is recorded
     entirely as uncontested single-row successes with no internal evidence of
     contradiction.
   - `capability.register` sits in `EXEMPT_ACTIONS` (`_enforcement.py:86`) so it
     can never be armed — permanent noise by design. Only `identity` uses
     `mark_action_denied` at all — **3** effective sites
     (`identity/__init__.py:606/:920/:933`) — and it misses its own
     `grant_permission` scope-overflow branch (`:1056-1069`), which writes a
     `result="denied"` `AuditEntry` into an in-process list that **never reaches
     the chain**, then returns `False` without declaring the denial.
   - `policy_enforced=False` on **every** row measured (0/34 and 0/14 across the
     two audits). Never read "Policy Controlled" as "policy enforced".

## Known over-claims that must be corrected externally

- `capability-registry.yaml:216` claims 6 network protocol adapters (A2A, MCP,
  gRPC, HTTP, WS, Internal); **only 3 exist** — A2A/MCP/gRPC `get_adapter()`
  return `None`.
- Security kernel "Vault Transit integration" is **dead** — `VAULT_AVAILABLE`
  has zero consumers in `src/`.
- Plugin `load_plugin()` **does not exist**; `PluginInterface` is absent;
  `plugins/registry_index.json` shows historical `active_plugins: 0`.
- G9 was labelled `VERIFIED` when only the upgrade/rollback path was proven —
  corrected to **NOT VERIFIED** for image build/boot (no docker daemon).

## Re-run / regenerate

Evidence for each gate lives with the audit records cited above. This document is
regenerated whenever a gate's underlying evidence changes; a gate moves
`FAIL → PASS` only on new measured evidence, never on code presence.
