# LIUHAO — PRODUCT READINESS GATE (P1–P10)

> Generated: 2026-10-01 (cycle 2) · Branch `p36` · Evidence date: 2026-10-01
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
>
> **Cycle-2 change (this update):** P4 (WorldInterface) and P6 (End-to-End
> Execution) move `FAIL → PASS` on **measured** evidence (see those sections).
> They were the two gates whose underlying mechanism was already real but
> unreachable/unwired; both are now exercised end to end and the artifacts are
> observed, not assumed. P5 (Human-Sovereignty UX) is **still FAIL** but several
> of its sub-defects were closed this cycle (see P5).

## Verdict summary

| | |
|---|---|
| **PASS** | **2** |
| **FAIL** | **7** |
| **BLOCKED** | 0 |
| **NOT YET TESTED** | 1 |
| **PRODUCT ACCEPTANCE** | **NOT READY** |

The single most important fact in this document: **the one end-to-end user
workflow that previously returned `SUCCESS` without doing the work is now fixed
and proven** — a "create file" goal, run through the production wiring, actually
writes the file to disk with the expected content and an unwired executor fails
instead of lying. That was the worst defect in the report (P6). It is now PASS,
measured. The product is still **NOT READY** because P1–P3, P5, P8–P10 remain
open (no employee persistence, no wired planner, no enforced approvals, no
user-facing audit/permission surface, no real user workflow completed through
the UI).

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

## P4 — WorldInterface — **PASS** (cycle 2, measured)

**Requires:** agents observe and act on the real world through registered,
permission-checked adapters.

**Evidence (this cycle — measured):**
- `src/ai/world_interface.py` implements the §37 contract (observe / validate /
  authorize / execute / verify). `FilesystemAdapter`
  (`src/ai/world_interface.py:58`) is now **actually wired**: the `file_write`
  local tool (`src/ai/tools_local.py:_make_file_write`) constructs a
  `WorldInterface(adapters=[FilesystemAdapter(root=...)], authorize=<default-DENY
  lambda>, actor="autonomous")` — the first real adapter registration in the
  codebase.
- **End-to-end, observed:** `scripts/verify_p6_e2e_file_creation.py` wires the same
  executor the gateway now injects (`LCore(register_local_tools=True)
  .capability_executor()`) into an `AgentRuntime`, runs the goal
  *"create a file named report.txt containing 'hello world'"*, and **asserts the
  file exists on disk with content `'hello world'`** — a real artifact, not a
  state flag.
- **Permission check is ENFORCED, not merely present:** the same script attempts
  `file_write` with path `../../escape.txt`; the action returns
  `success=False` with `WorkspaceViolation: 路径越界` — the default-DENY
  authorize + `resolve_in_workspace` containment boundary refuses the escape.
  `filesystem_adapter.execute` logs the violation.

**Caveat (kept honest):** this is an *authorization + workspace-containment*
boundary, not a process-level OS sandbox. The `ShellAdapter` is still not
registered, so "run a command" is not yet demonstrated. The gate is PASS for the
file-write path only.
**Flips to FAIL if:** the containment boundary is found bypassable, or the
adapter registration is removed.

---

## P5 — Human Sovereignty UX — **FAIL** (improved in cycle 2, still FAIL)

**Requires:** the human can see, approve, and stop autonomous action — and the
system fails closed when they do not.

**Evidence (unchanged blockers):**
- `@kernel_action` is **still record-only by default** (`enforce=False`).
  Measured live in cycle 1: `kernel_action=capability.register outcome=success
  policy=deny risk=HIGH` — policy said deny, the action ran, `success` written.
  **"Policy controlled" ≠ "policy enforced".** Not changed this cycle.
- The executor fence is **still disarmed by default** (`LIUHAO_EXECUTOR_FENCE`
  unset); when armed it still dies on boot (`identity.create_identity` → 0/24
  checks, RC=1). Not changed this cycle.
- `POST /v1/policy/approvals` still records **inert** approvals — zero
  enforcement effect. Not changed this cycle.
- HC-01 still records denials as successes across the broader action set (the
  85% self-contradiction from cycle 1 is not fully resolved — only
  `resource.allocate` was fixed this cycle, see below).

**Evidence (closed sub-defects this cycle — measured):**
- **Policy decisions are now audited.** `policy.evaluate` carries
  `@kernel_action("policy.evaluate", policy=False)` (the `policy=False` avoids
  self-adjudication nesting). Previously `evaluate()` had no `@kernel_action`.
- **Sentinel protection.** `unregister_rule()` now refuses to delete
  `default_deny` / `human_sovereignty` and records the refusal as
  `outcome=denied` (`src/kernels/policy/__init__.py`). Proven by
  `scripts/verify_p6_real_execution.py` → `POLICY_SENTINEL_PROTECTED: True`.
- **One denial now tells the truth.** `resource.allocate`'s three deny paths
  call `mark_action_denied(...)`, so an ordinary allocation refused by quota is
  recorded `outcome=denied` instead of `success`. Proven by
  `scripts/verify_p6_real_execution.py` → `RESOURCE_ALLOCATE_DENIED_RECORDED: True`
  and `DECORATOR_MECHANISM_OK: True` (denied/success/failure three-state
  verified).

**Biggest gap:** enforcement is not armed by default, approvals remain inert,
and the chain still lies for most denial paths.
**Flips to PASS when:** enforcement is armed by default, the fence survives
boot, an approval actually gates an action, and the chain tells the truth across
all denial paths.

---

## P6 — End-to-End Execution — **PASS** (cycle 2, measured)

**Requires:** user submits a goal → real work happens → real artifact.

**What was wrong (cycle 1, proven):** the production gateway built
`AgentRuntime(scope="L1")` with **no `capability_executor`**; the unwired kernel
silently simulated and returned `status=simulated, ok=True`, so a file-creation
goal reported `completed` + `SUCCESS` while the file was never created. The
return value lied. The mechanism itself was always capable — wiring a real
executor made it execute for real.

**What changed (this cycle — measured):**
- **Fail-closed, not silent.** `src/kernels/execution/__init__.py` now fails
  closed: with no executor wired and simulation not explicitly opted in
  (`LIUHAO_ALLOW_SIMULATED_EXECUTION`), `ActionExecutor.execute` returns
  `status="unwired", success=False` — it can no longer masquerade as success.
  Verified by `scripts/verify_p6_real_execution.py` → `FAIL_CLOSED_OK: True` and by
  the regression test `tests/kernels/execution/test_execution.py::
  test_unwired_executor_fails_closed`.
- **Production wiring repaired.** `src/gateway/ai_management.py:_ensure_runtime`
  now injects `LCore(register_local_tools=True).capability_executor()` into the
  `AgentRuntime` — the same real executor the mechanism proof always used.

**Evidence (this cycle — measured, not assumed):**
```
scripts/verify_p6_e2e_file_creation.py
  goal: 'create a file named report.txt containing "hello world"'
    run through AgentRuntime + LCore.capability_executor() (production wiring)
    state           = COMPLETED      (real)
    file exists     = True
    file content    = 'hello world'  (=== expected)   <-- real artifact on disk
    escape attempt  = success=False / WorkspaceViolation  (P4 enforcement)
  RESULT: PASS
```
A realistic "create file" goal, executed through the exact production path,
**actually writes the file to disk with the expected content**. Not a state flag.

**Caveat (kept honest):** the proof exercises the execution pipeline + `file_write`
tool directly (same wiring the HTTP gateway uses); the full `POST /v1/goals`
HTTP round-trip was not separately re-run this cycle. The unwired-executor
failure mode is proven; the wired path is proven at the pipeline level.
**Flips to FAIL if:** the HTTP gateway path is found to still simulate, or the
wired executor regresses.

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
2. **WorldInterface adapter now registered and exercised** (P4 → PASS, cycle 2):
   `FilesystemAdapter` is wired via the `file_write` tool's `WorldInterface`.
   `ShellAdapter` is still never registered.
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
     **Cycle-2 update:** `resource.allocate` is now fixed — its three deny paths
     call `mark_action_denied`, so an ordinary allocation refused by quota is
     recorded `outcome=denied`. The 8% (row-level) / 18% (action-level) drop
     from this one fix is real but does not close the gate; the MEDIUM-risk
     single-row-success asymmetry below is the remaining structural defect.
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
