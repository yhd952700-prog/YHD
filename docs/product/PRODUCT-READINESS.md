# LIUHAO — PRODUCT READINESS GATE (P1–P10)

> Generated: 2026-10-02 (cycle 3) · Branch `p36` · Evidence date: 2026-10-02
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
>
> **Cycle-3 change (2026-10-02):** the real-execution closed loop is now proven
> across the FULL chain by a single standalone verifier
> (`scripts/verify_real_execution_e2e.py`, REDIR to temp, HC-01 untouched): a
> natural-language file-write goal produces a REAL on-disk artifact — and
> `file_write` is the ONLY tool that writes to disk; a `python:` directive goal
> executes code via `python_compute` and returns a value but does NOT write a
> file; an unserved capability FAILS
> HONESTLY (no false success); the audit trail is populated (9/9 checks PASS).
> Two cross-cutting gaps closed: the observability alert loop is now wired
> (execution failures → real alerts to the store + console), and the `ShellAdapter`
> now has a fail-closed safety gate + threat model that keeps host-command
> execution out of the autonomous goal path by default. No product gate flipped to
> PASS this cycle; P4/P6 evidence is strengthened (see "Cycle 3" section).

> **Cycle-4 change (2026-10-02, autonomous hardening):** five more measured
> hardenings landed on `p36` and were integrated (all verifiers REDIR
> `AUDIT_DB_PATH` / `LIUHAO_WORKSPACE_ROOT` to temp; frozen HC-01 untouched):
> (1) the console now ships a real **operator control panel**
> (`apps/console/console/src/components/OperatorControls.tsx` + `lib/operator.ts`,
> wired into `pages/Directory.tsx`) with hire / create-goal / pause / resume /
> delete / stop / replan forms that call the proven REST endpoints — closing the
> P1 "no UI hire form / pause-resume buttons" and P5 "no human-facing stop panel"
> gaps at the *code* level (browser end-to-end click-through still outstanding);
> (2) `Verifier.verify` now **independently re-reads disk** for file-side effects
> instead of trusting the executor's returned dict (`scripts/
> verify_execution_independent_verification.py`, 5/5 PASS) — strengthens P6;
> (3) `WorldInterface` + `host_command` are now **genuinely enforced and honestly
> documented**: autonomous `FilesystemAdapter(root=None)` is fail-closed, shell
> execution is routed through the default-DENY `HostCommandBroker`, and the
> `capabilities=()` executor-fence *no-op* is honestly replaced by the real policy
> gate (`tests/ai/test_world_interface_safety_fix.py`, 6/6 PASS); (4) the
> hash-chain audit now carries the **goal's correlation id** for every kernel
> action (`src/kernels/_crosscutting.py` `KERNEL_ACTION_CORRELATION_ID`
> ContextVar, `scripts/verify_audit_goal_correlation.py`, 9/9 PASS) — closes the
> P8 "cannot prove this goal ran this action" traceability gap; (5) an honest
> end-to-end acceptance test (`scripts/verify_e2e_real_execution.py`, 13/13 PASS)
> re-confirms the full chain and documents the planner as **deterministic
> keyword/regex, not LLM** and only `python_compute` + `file_write` as REAL
> executors. **No product gate flipped to PASS this cycle** (UI controls are code-
> complete but not browser-proven; the approve-panel and a user-facing audit-
> query surface remain open) — see "Cycle 4" section.

> **Cycle-5 change (2026-10-02, HTTP closure + audit UX):** the single largest
> honesty gap in this document is now closed: **a real registered human can hand
> LIUHAO a real goal over real HTTP and get a real result** — proven by
> `scripts/verify_http_full_closure.py` (**31/31**, real `uvicorn` server, real
> bearer token, real artifact on disk, audit rows carrying the goal's correlation
> id, HTTP stop → `cancelled`, HTTP replan honest). Proving it **exposed four real
> defects that were previously invisible** because P6 had only ever been proven
> in-process (see "Cycle 5"). Also added the missing **user-facing audit surface**
> (`src/gateway/audit.py` — read-only `/v1/audit/events`, `/events/{seq}`,
> `/verify`, `/summary`) proven by `scripts/verify_audit_user_surface.py`
> (**31/31**) including a **real tamper test returning `ok=false`**
> (`content hash mismatch at seq=2`). **P8 flipped FAIL → PASS** (its stated
> flip criteria — "a user can query and independently verify the chain" — is now
> met with measured evidence). P6's old HTTP caveat is closed. No other gate
> flipped. Regression: **168 passed** (`tests/ai` + `tests/kernels/execution` +
> `tests/gateway`).
>
> > **Honest note on the P8 flip:** P8's written flip bar was *query + independent
> > verify + truthful outcome*, all now met and measured. Its title mentions "UX",
> > and the **rendered audit view is NOT yet built** — only the HTTP endpoints and
> > typed frontend client functions (`operator.ts`) are. We flip on the written
> > criteria and record the residual rather than either overclaiming or refusing
> > to credit proven work.

## Verdict summary

| | |
|---|---|
| **PASS** | **4** (P4, P6, P7, P8) |
| **FAIL** | **6** (P1, P2, P3, P5, P9, P10) |
| **BLOCKED** | 0 |
| **NOT YET TESTED** | 0 |
| **PRODUCT ACCEPTANCE** | **NOT READY** |

The single most important fact in this document: **the one end-to-end user
workflow that previously returned `SUCCESS` without doing the work is now fixed
and proven** — a "create file" goal, run through the production wiring, actually
writes the file to disk with the expected content and an unwired executor fails
instead of lying. That was the worst defect in the report (P6). It is now PASS,
measured. The product is still **NOT READY** because P1–P3, P5, P8–P10 remain
open (employee persistence now exists as a store but the UI still surfaces 28
synthetic modules as "employees", planner execution now audited but not yet
wired into a user-facing path, no enforced approvals, no user-facing
audit/permission surface, no real user workflow completed through the UI).

---

## P1 — AI Employee — **FAIL**

**Requires:** a real, persistent roster of AI employees with lifecycle (hire /
pause / resume), each with identity and capabilities, visible and manageable by
the user.

**Evidence:**
- `src/gateway/ai_management.py:141-156` `_ensure_employee()` **hardcodes** the
  only employee: `Employee(name="liuhao-default", agent_count=3,
  agent_types=["planner","executor","critic"])`. Synthesised, not persisted.
  This seed default is now kept **only for backward compat** (the gateway still
  seeds it on first run so `_ensure_employee` keeps working); it is no longer the
  only employee.
- **By-name lifecycle is now real (cycle P1 lifecycle work).** `AIStateManager`
  gained `hire_employee(name, agent_count, agent_types)` (idempotent: returns an
  `already_existed` marker instead of silently overwriting), `list_all_employees()`
  (iterates `EmployeeStore.list_employees()` — ALL stored employees, not just the
  default) and `remove_employee(name)` (refuses to delete the seed default
  `liuhao-default` so the gateway keeps booting). REST: `POST /v1/employees` (201 on
  create, 409 on duplicate name), `GET /v1/employees` (returns `employees[]` — the
  full real roster), `DELETE /v1/employees/{name}` (200 / 404 missing / 403 default).
  Agent ids are now **globally unique** (`<employee>-a<i>`, see `src/ai/employee.py`)
  so pause/resume/get-by-id resolve the right employee instead of colliding on
  `agent_0`. These routes share the same `require_human_principal` gate as the other
  `/v1` routes. Measured by `tests/gateway/test_employee_lifecycle.py` (PASS):
  hire → list → pause one of its agents → resume → remove → no longer listed; the
  seed default still seeds and deleting it returns 403; duplicate name returns 409.
  Data is REDIR'd to a temp dir via `LIUHAO_WORKSPACE_ROOT`.
- An `EmployeeStore` (`src/ai/employee_store.py`, commit 187291d6) now persists
  `Employee`/agents/tasks **and** the gateway's `_goals` across restart; the
  gateway seeds/loads the default employee from it. The roster now surfaces those
  REAL employees (commit 20415d5c): `dashboard_roster` returns `real_employees`
  from `EmployeeStore` and `totals.employees` counts only real employees — kernel
  modules + capability layers are no longer miscounted as 28 "employees" (they are
  preserved separately as `totals.registry_entries`). The console now also surfaces a
  dedicated hire form (`HireEmployeeForm`, `components/OperatorControls.tsx`) and
  per-agent pause/resume buttons (`AgentActionButtons`), both wired to the real
  lifecycle endpoints (`POST /v1/employees`, `POST /v1/employees/{id}/pause|resume`).
- `_goals` is an **in-process dict** (`ai_management.py:124`) — lost on restart,
  not shared across workers.
- The UI's "My AI Employees" previously miscounted kernel modules + capability
  layers as 28 "employees" (`total = kernels + layers`). This is now fixed
  (commit 20415d5c): the roster surfaces `real_employees` from `EmployeeStore`,
  `totals.employees` reflects the true persisted count, and the module count lives
  in `totals.registry_entries` as registry info, not employees.

**Biggest gap (corrected 2026-10-21):** the console *does* now have both the
dedicated **hire form** (`HireEmployeeForm` in `components/OperatorControls.tsx`,
rendered in `pages/Directory.tsx`, calls `operator.hireEmployee` → `POST
/v1/employees`) and per-agent **pause/resume buttons** (`AgentActionButtons`,
rendered per agent in `Directory.tsx`, calls `pauseAgent` / `resumeAgent` → the real
`POST /v1/employees/{id}/pause|resume` endpoints). Both are wired to the by-name
lifecycle endpoints integration-tested in `tests/gateway/test_employee_lifecycle.py`,
so the *UI* half of P1 is no longer the blocker. What genuinely remains
unverifiable in this environment: (1) a real **browser click-through** of the full
hire → pause → resume → remove flow (no headless browser available here), and (2) a
**cross-restart durability** integration test for `EmployeeStore` (durability is
by-design via atomic JSON writes, but not separately exercised across a process
restart in CI).
**Flips to PASS when:** a real employee can be created, persists across restart,
can be paused/resumed from the UI, and appears by name — not as a count of
kernel modules. **Status this cycle:** create/persist/list/pause/resume/remove is
real on both backend (tested) and the console UI (hire form + pause/resume buttons
wired). P1 stays **FAIL** only on the two items above — a browser click-through and
a cross-restart durability test — which require an environment with a browser / a
restart integration test, not a missing capability.

---

## P2 — Task Planning — **FAIL**

**Requires:** a high-level user goal decomposed into concrete, tool-shaped tasks
that an executor can actually run.

**Evidence:**
- `GoalDecomposer.decompose` (`src/kernels/execution/__init__.py:190`) is a
  **keyword table**; its own comment states that "in production would use LLM".
- The real LLM-backed planner `src/ai/goal_task_graph.py` is an **orphan — no
  caller outside its own module** (the gateway never invokes it). Its task
  execution is now routed through the `@kernel_action("ai.execute_planner_task")`
  audit (commit 2f7484d5), but it is still not invoked by any user-facing path.
- Decomposition passes `{"goal": <the whole sentence>}` to capabilities, not
  tool-shaped inputs (`code` / `path` / `content` / `expression`). An executor
  receiving this cannot act on it.

**Biggest gap:** no planner is wired into any user-facing path (its execution is
now audited, but nothing calls it).
**Flips to PASS when:** a real goal is decomposed into executable tasks with
tool-shaped inputs and those tasks actually run through a user path.

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
boundary, not a process-level OS sandbox. The `ShellAdapter` remains **not
registered** in the autonomous goal path (so "run a command" is still not
demonstrated there), but it is now protected by a **fail-closed safety gate**
(`src/ai/world_interface_shell_gate.py`) + a written threat model
(`docs/security/shell-adapter-threat-model.md`): an autonomous shell interface
cannot be constructed without an explicit human-arming `authorize` policy, and
`shell=True` (real shell, injection surface re-opened) requires a logged,
per-request `human_arm_token`. 10/10 gate tests pass
(`tests/ai/test_shell_adapter_safety_gate.py`). The gate is PASS for the
file-write path only.
**Flips to FAIL if:** the containment boundary is found bypassable, the shell
gate is weakened to a blanket allow, or the adapter registration is removed.

---

## P5 — Human Sovereignty UX — **FAIL** (re-assessed 2026-10-02: backend controls now real & proven; residual = dedicated approve/stop UX panel + dev-default record-only)

**Requires:** the human can see, approve, and stop autonomous action — and the
system fails closed when they do not.

**Evidence (re-assessed 2026-10-02 — three prior "blockers" were stale/incorrect):**

The earlier "unchanged blockers" were written when these mechanisms were believed
absent or unarmed. Direct inspection of the code plus the *already-existing*
verifiers proves otherwise. Corrected below (each claim is backed by a running
verifier, all of which REDIR `AUDIT_DB_PATH` / `LIUHAO_WORKSPACE_ROOT` to temp so
the frozen HC-01 store is never touched):

- ~~"`@kernel_action` is still record-only by default (`enforce=False`)"~~ — TRUE
  that the **local/dev** default is record-only, and that is *intentional and
  proven*: it keeps dev/CI in the L1 contract so the verifiers themselves run
  un-armed. It is **not** the production posture. In the shipped product the switch
  `LIUHAO_KERNEL_POLICY_ENFORCE` is armed to **CRITICAL** by
  `docker-compose.prod.yml`, `infra/staging/docker-compose.yml` and
  `scripts/build_cloud_bundle.py`, asserted by `verify_c4_approval_channel.py`
  (57/57 GREEN). When armed, every HIGH/CRITICAL `@kernel_action` is genuinely
  *blocked* (raises `PolicyDeferredError` / `PolicyDeniedError`), not merely
  recorded. "Policy controlled" ≠ "policy enforced" is no longer accurate for
  production.
- ~~"The executor fence is still disarmed by default; when armed it still dies on
  boot (`identity.create_identity` → 0/24 checks, RC=1)"~~ — **FALSE as of
  commit `771e6911`.** The gateway now installs the fence object AND binds a
  process-wide executor lease *before* any in-process fenced call (identity
  seeding, kernel init), so arming `LIUHAO_EXECUTOR_FENCE=on` **survives boot**.
  Proven by `scripts/verify_fence_boot_survival.py` (7/7 GREEN): it reproduces the
  pre-fix boot-crash denial, proves boot survives post-fix, and asserts
  fail-closed is preserved (rogue no-lease executor denied, capability escalation
  denied, legitimate leased + covered executor allowed). `LIUHAO_REQUIRE_EXECUTOR_FENCE=1`
  converts an install-failure fail-open into fail-closed (startup aborts).
- ~~"`POST /v1/policy/approvals` still records inert approvals — zero enforcement
  effect"~~ — **FALSE.** The C-4 sovereignty channel in `src/gateway/policy.py`
  is REAL and gating: `issue_grant` / `revoke_grant` are bounded (action ∈
  {HIGH, CRITICAL}, TTL ≤ `MAX_GRANT_TTL_SECONDS`, revocable, principal
  token-only / never a service identity), and `grant_window` lets an enforced
  action run only while a valid grant is open. Proven end-to-end by
  `verify_c4_approval_channel.py` (57/57 GREEN): grant issued → window opens →
  action allowed; grant revoked / expired → window will not open; every allowed
  action audit carries the `sovereignty_grant` id + `human_sovereignty` rule; no
  production decorator sets `enforce=True` (the switch arms globally instead), and
  no new opener of the channel exists under `src/`.
- **Frozen HC-01 evidence store is unchanged** — its 85% `policy=deny` /
  `outcome=success` self-contradiction (cycle 1) remains as *historical* evidence
  of the old bug; it is NOT reprocessed. The fixes above apply to all NEW audit
  writes from this commit forward.

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
- **The content lie is now closed for ALL actions (not just `resource.allocate`).**
  The `@kernel_action` decorator (post-fix, `src/kernels/_crosscutting.py`)
  relabels the audit `outcome` from `success` to `denied` for *every* definitive
  `policy_decision=deny` (and `defer`) verdict, so a refusal can no longer be
  recorded as a success. Proven systematically by
  `scripts/verify_audit_truthfulness.py` — it exercises the real decorator +
  real policy engine + real (temp) audit store across **all 20 denied actions**
  and asserts `outcome != "success"` for every one (PASS). The body still runs
  (record-only, so no operational self-lock), but the chain now tells the truth.
- **The policy posture was completed, not just relabelled.** 9 operational
  actions the service legitimately performs (`identity.create_identity`,
  `memory.auto_cleanup`, `resource.allocate`, `resource.create_quota`,
  `network.add_route`, and the 4 `trust.*` signals) were moved from
  `INTERNAL_SERVICE_DENIED_ACTIONS` to `INTERNAL_SERVICE_ALLOWED_ACTIONS` with
  justifying comments. They are now truthfully recorded as `allow` / `success`
  (instead of being flagged `denied` while the system depended on them), and the
  remaining denied set is genuine authority/destructive actions that require a
  verified human (OD-010). The `test_every_decorated_kernel_action_is_classified`
  completeness guard still holds.
- **The planner's task execution is now on the audited path.** `GoalTaskGraph`
  `.execute_task` delegates to a `@kernel_action("ai.execute_planner_task")`
  -wrapped method (commit 2f7484d5), so the planning→execution loop is no longer
  orphaned from the audit. The new action is classified `ALLOWED` in
  `INTERNAL_SERVICE_ALLOWED_ACTIONS`. Proven by
  `scripts/verify_planner_audit_loop.py` (PASS): all 4 tasks run, every
  `ai.execute_planner_task` event records `outcome=success` + `policy=allow`, and
  a regression guard asserts no event anywhere has `(decision=deny AND
  outcome=success)`.
- **First persistence layer in the product.** `GoalTaskGraph.save_state` /
  `load_state` (stdlib `json`, REDIR-able via `LIUHAO_WORKSPACE_ROOT`) round-trip
  goals, tasks, statuses and dependency edges across a process restart — closing
  "no persistence at all" for the planning state. (Employee/agent persistence is
  a separate, still-open item — see P1.)
- **Employee/agent/goal persistence now exists.** `EmployeeStore`
  (`src/ai/employee_store.py`, commit 187291d6) persists `Employee` + agents +
  tasks + aggregate counters to a JSON file (stdlib only, REDIR-able via
  `LIUHAO_WORKSPACE_ROOT`, atomic write, corrupt-safe), and `AIStateManager`
  seeds/loads the default employee from it and persists `pause`/`resume` + goal
  history across restart. Proven by `scripts/verify_employee_persistence.py`
  (PASS): seed→save→restart→load recovers agent counts, task status and counters.
  (The UI now surfaces these real employees — see roster fix below.)

- **The roster no longer miscounts modules as employees.** `dashboard_roster`
  (`src/gateway/roster.py`, logic extracted to `src/ai/roster_payload.py` to stay
  fastapi-free, commit 20415d5c) now returns `real_employees` rebuilt from
  `EmployeeStore` and sets `totals.employees` to the TRUE persisted count; kernel
  modules + capability layers are preserved as `totals.registry_entries` (registry
  info, not employees). Proven by `scripts/verify_roster_real_employees.py`
  (PASS): real employees surfaced, modules not counted as employees, kernels/layers
  still present (14/14 under the venv). This closes the P1 defect "displays 28 AI
  employees when there are zero employees."

**Re-assessment (2026-10-02):** P5's prior FAIL rested on the three stale claims
above. With them corrected against the code and the running verifiers, the
*backend* sovereignty controls the gate requires are now met with measured
evidence:

- **Human can approve** → the C-4 channel is real and gates HIGH/CRITICAL actions
  (verify_c4_approval_channel.py, 57/57).
- **Human can stop / system fails closed when they do not approve** → in
  production the switch is armed CRITICAL, so a non-allowed HIGH/CRITICAL action
  is *blocked* (not merely recorded); the fence boot-survival fix keeps that gate
  live at boot (verify_fence_boot_survival.py, 7/7).
- **The chain tells the truth across all denial paths** → `@kernel_action`
  relabels every `policy_decision=deny`/`defer` to `outcome=denied`
  (commit 315fc804; verify_audit_truthfulness.py across all 20 denied actions).

**Honest residual (why still FAIL, not overclaimed to PASS):** the gate is titled
*Human Sovereignty **UX***. The backend approve/stop/enforce/fence controls are
real and proven, but the **dedicated human-facing approve/stop UI panel** that
surfaces pending grants and lets a human click-approve / click-stop is not yet
demonstrated in this assessment (the C-4 endpoints exist; a first-class UI
surface for them is the open item). Additionally, the *local/dev* default stays
record-only (L1) by design — operators opt into real enforcement per environment
via the switch, and `LIUHAO_REQUIRE_EXECUTOR_FENCE=1` makes a fence-install
failure fail-closed rather than fail-open. The dev-default is fail-loud with
opt-in fail-closed, **not** an unaddressed gap.

**Flips to PASS when:** a usable human approval/stop UI panel is wired to the
existing C-4 endpoints (pending grants visible + click-approve/revoke + a
stop/abort control on in-flight autonomous actions), and the chain tells the
truth across all denial paths (already proven).

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

**~~Caveat~~ — CLOSED cycle 5 (2026-10-02):** the old caveat was that only the
in-process pipeline was proven and `POST /v1/goals` was never exercised over HTTP.
That gap is now closed, and closing it **exposed four real defects** that were
invisible to in-process testing (see "Cycle 5"). Measured by
`scripts/verify_http_full_closure.py` — **31/31 PASS**: a real `uvicorn` server on
127.0.0.1, a real registered human identity, a real JWT bearer token
(`X-Liuhao-Token`), `POST /v1/goals` → poll to terminal → **the file exists on
disk with the exact expected content**, the goal is bound to a real employee, the
temp audit store has rows carrying the goal's correlation id, `POST
/v1/goals/{id}/stop` terminates a running goal as `cancelled` (real cooperative
cancellation), `POST /v1/goals/{id}/replan` stays honestly `failed` when tasks
failed, and the same request **without a token returns 401**. Operational closure
over real HTTP is now demonstrated, not assumed.
**Flips to FAIL if:** the HTTP gateway path is found to still simulate, the wired
executor regresses, or the stop/replan semantics regress to false success.

---

## P7 — Recovery — **PASS (measured)**

**Requires:** a failed or interrupted task is detected, recovered or reported —
and the user sees it.

**Evidence (all measured by tests, not claimed):**

- **Kernel crash recovery is now PROVEN end-to-end.**
  `tests/kernels/execution/test_recovery_real.py::test_crash_recovery_resumes_completed_tasks_no_repeat`
  wires a real `ExecutionJournal` + `capability_executor` (`LCore`), runs a goal
  that yields ≥2 workspace file-write tasks, then simulates a crash/restart with a
  **new** `ExecutionEngine` + `ExecutionJournal` on the SAME goal id (deterministic
  task names). On restart `_adopt_journaled_progress`
  (`src/kernels/execution/__init__.py:840`) resumes both completed tasks WITHOUT
  re-invoking the side-effecting `file_write` (a side-effect counter proves it is
  called exactly 0 times on resume; file contents are byte-identical). This is REAL
  recovery, not a stub.
- **`execute_replan` is no longer a status-flip stub.**
  `src/kernels/evaluation/__init__.py:521` now routes to a wired `replan_executor`
  (set by `AgentRuntime` on the evaluator, calling `run_goal`) and returns `True`
  only when the re-execution genuinely succeeds; returns `False` honestly when no
  runtime is wired or the re-run fails. Proven by
  `test_execute_replan_re_executes_via_runtime` (re-execution writes the file) and
  `test_execute_replan_false_when_unrecoverable` (honest `False`).
- **`retry_dead_letter` no longer lies.**
  `src/kernels/event/__init__.py:272` detects that `publish` swallows handler
  exceptions into a *new* dead letter for the same event, so a re-dispatch that
  fails again is reported `False` (never `True`-while-failing). Proven by
  `test_retry_dead_letter_honest`.
- **Product-level `replan_goal` resumes completed tasks.**
  `src/gateway/ai_management.py:311` now passes the real goal text to
  `runtime.replan` (previously re-ran an *empty* goal), and the journal-driven
  resume means the file-write side effect is not duplicated. Proven by
  `tests/gateway/test_recovery.py::test_replan_goal_resumes_completed_tasks`.

**Honesty note:** recovery correctness rests on *deterministic task names*
(`GoalDecomposer` stability) — that is the contract `_adopt_journaled_progress`
matches on. The journal does not yet do cross-process *side-effect idempotency*
keys (external side effects other than file writes are de-duplicated only by virtue
of not being re-executed); that remains a known limitation, not a claimed feature.

---

## P8 — Explainability / Audit UX — **PASS** (cycle 5, measured — see honest note below)

**Requires:** a user can see, for any action, what happened, why, and prove it
was not altered.

**Prior state (cycle 4 and earlier):** FAIL. The mechanism was real but had **no
user-facing surface** — no HTTP endpoint or CLI to query or verify the chain — and
the record had previously misstated outcomes.

**What changed this cycle (measured):** `src/gateway/audit.py` adds a strictly
**read-only**, sovereignty-gated audit surface, mounted at `src/gateway/main.py:531-532`
with the same human bearer dependency as the other routers:

| Endpoint | Purpose |
|---|---|
| `GET /v1/audit/events` | list real events, filter by `correlation_id` / `principal` / `action` / `outcome` / `event_type` / `since` / `until`, with `limit` + honest `truncated` echo |
| `GET /v1/audit/events/{seq}` | one event by sequence; duplicates honestly flagged `duplicate_seq` |
| `GET` + `POST /v1/audit/verify` | runs the **real** `verify_integrity()`; returns `ok`, `entries_checked`, `first_failure` |
| `GET /v1/audit/summary` | counts by outcome/action |

Evidence (`scripts/verify_audit_user_surface.py`, **31/31 PASS**) — the decisive
checks that this is not another silent-success trap:

- **Real HTTP + real auth:** real `uvicorn` thread, token minted via
  `register_human_identity.py` + `issue_console_token.py`. **Unauthenticated and
  invalid-token access to every audit endpoint returns 401** — the audit trail is
  not anonymously readable.
- **Tamper test actually fails:** on a throwaway temp copy, one row's `outcome` was
  altered to `TAMPERED-BY-TEST` via raw SQL (hash untouched). The endpoint returned:
  ```
  ok             = False
  entries_checked= 71
  first_failure  = content hash mismatch at seq=2
  failures       = ['content hash mismatch at seq=2']
  ```
  A verify endpoint that could not return `False` would be worthless; this one
  names the offending sequence. The tampered row also remains readable via
  `/events/{seq}` (an auditor must be able to *see* the bad row, not merely be
  told "the chain broke").
- **No mutation surface:** OpenAPI confirms the only non-GET under `/v1/audit/*` is
  `POST /v1/audit/verify` (read-only verification). No write/delete/repair endpoint
  exists — a self-healing chain would not be evidence. Unreadable store → honest
  error, never `{ok: true}`.
- The hashing is the **existing** `AuditStore` / `verify_integrity()` on its pinned
  read-only snapshot path — nothing reimplemented.

**Honest residual (why PASS is not a full UX pass):** the flip bar written for this
gate was *"a user can query and independently verify the chain, and `outcome`
reflects what actually happened"* — all three are now met with measured evidence
(the outcome-truthfulness item was fixed in cycle 3 across all 20 denied actions).
But the gate is titled *Audit **UX***, and the **rendered audit view is not
built**: only the endpoints plus typed frontend client functions in
`apps/console/console/src/lib/operator.ts` exist. We credit the proven capability
and record the residual plainly rather than overclaiming a complete UX.

**Flips back to FAIL if:** an audit write/mutation endpoint is ever added, the
verify endpoint can be made to return `ok=true` on a tampered chain, or the
endpoints become readable without a valid human token.
**Remaining gaps recorded honestly (none of them the flip criteria):**
- The UI's audit section is labelled **"数据中心"** (data centre) — users will not
  look there for an audit trail; **the rendered audit view that consumes the new
  endpoints is not yet built** (client functions are wired, no component renders
  them). This is the "UX" half of the gate's title and the main residual.
- **(Historical, frozen) chain truthfulness defect:** on the **old** HC-01 sample,
  **41 of 48 HC-01 records (85%) assert `policy=deny` while `outcome` reads
  `success`/`intent`**; **zero** had `enforced=True`. That is retained *as frozen
  historical evidence* of the old bug and is NOT reprocessed — but it is the reason
  this gate's flip criteria included "outcome reflects what actually happened".
  That item is fixed going forward: since cycle 3, `@kernel_action` relabels
  `outcome` to `denied` for every definitive `deny`/`defer` verdict, proven across
  all 20 denied actions (`scripts/verify_audit_truthfulness.py`). The principle
  still stands and is why the tamper test above matters: *tamper-evidence and
  truthfulness are separate claims, and a chain that proves it was unaltered while
  recording false content is more dangerous than no chain at all.*
- External anchoring (`external_anchor.py`) is real RFC-6962 math, but the only
  shipped transport is `LocalReferenceLog`; `ExternalLogTransport.submit_root`
  raises `NotImplementedError` — **no real third-party transparency log.** Left as
  is: honestly stubbed, not claimed. Enablement requires choosing and contracting a
  real log operator — an owner/legal decision, not an engineering one.

**Flip criteria met:** a user can query and independently verify the chain
(endpoints + tamper test, 31/31) **and** `outcome` reflects what actually happened
(cycle-3 relabelling, all 20 denied actions).
**Flips back to FAIL if:** any audit write/mutation endpoint is added, `/verify`
can return `ok=true` on a tampered chain, or the endpoints become readable without
a valid human token.

---

## P9 — Permissions / Identity UX — **FAIL**

**Requires:** the user can see and control what each AI employee is allowed to do.

**Evidence:**
- The **identity kernel is REAL-USABLE**: real enforcement of lifecycle, scope
  ceiling, principal uniqueness, and human/agent namespace disjointness;
  fail-closed registry integrity.
- ~~It is an **authorization registry, not authentication** — no credentials,
  no token issuance, no MFA, no key rotation in the kernel.~~ **RETRACTED**
  (branch `p36-identity-ux`): the gateway really does authenticate.
  `src/gateway/policy.py:83 require_bearer_payload` verifies a JWT **signature**
  through `src.security.get_jwt_handler().validate_access_token`
  (HS256/RS256 keyed from `LIUHAO_JWT_SECRET`; configured federation needed for
  more than one worker; production refuses to boot without it), reading
  `X-Liuhao-Token` first and `Authorization: Bearer` second; every business
  router is mounted behind that dependency in `src/gateway/main.py`.
  `POST /v1/auth/login` (`src/gateway/auth.py:415`) checks a real
  PBKDF2-HMAC-SHA256 credential record with throttling and issues a JWT, and
  `scripts/issue_console_token.py` mints one for a registered human. What is
  still true today: **no MFA and no automated key rotation**, and the docstring
  at the top of `scripts/issue_console_token.py` still claims "the gateway has
  no `/v1/auth/login`" — that sentence is stale as well.
- Default config leaves both persistence env vars unset ⇒ human registrations are
  **memory-only and vanish on restart**. **Measured, not assumed** (two processes
  sharing one temp registry, see `scripts/verify_identity_user_surface.py`):
  with `LIUHAO_HUMAN_IDENTITIES_FILE` unset the write is an explicit no-op
  (`src/kernels/identity/_persistence.py:524`) and the registration is gone in
  the next process; with the file **and**
  `LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY` set it survives the restart; with the
  file set but no key the row is written to disk and then **refused** at load
  (fail-closed, HC-11 / U6). Three different failures behind one "there are no
  humans" symptom — which is why the new endpoints report the reason.
- `grant_permission`'s scope-ceiling refusal is recorded on HC-01 as
  `outcome=success` — a false success on the authoritative chain. Still true:
  that method returns `False` and never calls `mark_action_denied`, unlike
  `create_identity` (`src/kernels/identity/__init__.py:1043` vs `:933`).
- The UI exposes exactly **4 write actions** total (send chat, record approval,
  revoke approval, log out). None manages permissions.

**Added since that assessment (backend only, no UI):** a read-only identity
surface — `GET /v1/identity/principals`, `/v1/identity/principals/{principal_id}`,
`/v1/identity/permissions`, `/v1/identity/summary` (`src/gateway/identity.py`) —
mounted behind the same human-principal gate as every other business router and
**read-only by construction**: no POST/PUT/PATCH/DELETE exists under
`/v1/identity` at all. Every count comes from `IdentityManager` itself — no
second source of truth, no synthesized rows; derived `resource`/`action` fields
are labelled `derived` and the per-grant scope the kernel does not retain is
reported as absent rather than invented. `scripts/verify_identity_user_surface.py`
proves it over real HTTP against a real uvicorn server (48/48 checks): real
fixtures round-trip, 401 for missing and forged tokens, honest 503 when the
identity kernel is not READY, honest `warnings` + `registry.rows_refused` when a
configured registry fails closed, and 404 (never an empty object) for an unknown
principal.

**Biggest gap:** identity is now enumerable over HTTP but still not visible in
the UI, grants are deliberately not writable over HTTP (mutation is an authority
surface and has no human-review gate yet), and refusals are still recorded as
successes.
**Flips to PASS when:** permissions are visible/manageable in the UI and refusals
are recorded truthfully.

---

## P10 — Real User Workflow — **FAIL**

**Requires:** a real user can accomplish a real job through the product.

**Evidence (updated 2026-10-21):** the core product surfaces now HAVE real UIs on
real backend data — `Files` (`pages/Files.tsx`, strict read-only workspace browser),
`Goals`/`Tasks` (`pages/Goals.tsx`, real goal + task execution history), `Projects`
(`pages/Projects.tsx`, human-gated create/list/get/delete; goals link under projects),
and `Apps` (`pages/Apps.tsx`, lists/activates real plugins via `GET/POST /v1/plugins`).
A real human-gated **create-project** write action exists (`POST /v1/projects`).

**Artifact traceability now CLOSED (2026-10-21, P10 residual).** The goal detail API
(`GET /v1/goals/{id}`) now returns a real `artifacts` list: `src/gateway/ai_management.py`
collects workspace-relative paths produced by completed `file_write` tasks (fail-closed —
only inside `workspace_root`, deduped, never invented; `[]` fallback). `pages/Goals.tsx`
renders a "产生的产物" section that opens the real file through `/v1/files/content`.
2/2 backend tests prove the linkage is real (writes a file → artifact appears; writes none
→ empty). So a produced file is now *surfaced against the job that created it* — the
artifact is no longer orphaned in the workspace.

**Residual gap (still caps PASS):** there is still no product flow to *assign an ad-hoc
task to an agent and watch it complete as a single tracked job with a delivered artifact*
from the user's seat — the agent-side execution that writes the file is driven by the
execution kernel's goal path, not by a user-facing "assign task → receive deliverable"
UX. Employee management remains a real write action (`POST/DELETE /v1/employees`), but
that is management, not job completion. The artifact-traceability closure makes the
*evidence* of a completed job browseable; the *job-completion UX* itself is the remaining
item.

**Closed-loop now PROVEN without a browser or LLM key (2026-10-04).** `tests/gateway/
test_p10_real_job_e2e.py` (3/3) drives the REAL app via `TestClient` and proves the full
chain executes: a deterministic (keyword/regex) `GoalDecomposer` maps a natural-language
file-write goal to a `file_write` task → `file_write` writes a **real on-disk file** under
the workspace → the goal detail API surfaces it in `artifacts` → `GET /v1/files/content`
reads back the same real file → the audit chain holds a real row indexed by the goal's
`correlation_id` → no token → 401. This is direct evidence that the system really
plans / executes / verifies / audits / delivers — it does NOT depend on a provider key
(the planner is deterministic) and does NOT depend on a browser. (Honest correction made
elsewhere: a `python:` directive does NOT write a file; only `file_write` does.)

**Biggest gap (re-scoped 2026-10-04):** the *headless* closed loop is proven; what remains
is the **user-seat / browser** experience of that loop — a human clicking through create-goal
→ watch execution → receive the delivered artifact in the Goals UI, plus a cross-restart
durability integration test for the produced artifact / audit. Those require a browser /
a restart harness, which this environment lacks.
**Flips to PASS when:** a named realistic user job is completed end to end with a
verifiable artifact surfaced in-product — now proven at the API/headless layer; the browser
click-through of that same flow is the remaining demonstrable step.

---

## Cross-cutting blockers (each caps multiple gates)

1. **Some kernel metrics are declared with zero call sites.** `goal_decompositions_total`,
   `goal_tasks_generated`, `task_execution_total`
   (`src/observability/metrics.py:155-183`) are declared with **zero call sites**.
   (System-health metrics — `error_rate_percent`, `memory_usage_percent`,
   `cpu_usage_percent`, `latency_p99_ms`, `service_heartbeat_interval`,
   `audit_log_lag_seconds` — are NOW emitted by `MetricCollector` and feed live alert
   rules; see KERNEL-PRODUCT-CAPABILITY.md §25.)
2. **WorldInterface adapter now registered and exercised** (P4 → PASS, cycle 2):
   `FilesystemAdapter` is wired via the `file_write` tool's `WorldInterface`.
   `ShellAdapter` is still never registered in the autonomous goal path — but as of
   cycle 3 it has a fail-closed safety gate (`src/ai/world_interface_shell_gate.py`)
   + threat model (`docs/security/shell-adapter-threat-model.md`), so host-command
   execution stays default-deny and human-armed only.
3. **Policy recorded but not enforced** by default (P5).
4. **No persistence** across capability, context, evaluation, event, resource,
   security, trust — state resets on restart; revoked trust "resurrects".
5. **No identity/permission check** on writes in memory, network, resource,
   security, trust, and execution (execution checks capability scope only, never
   identity).
6. **The audit chain used to record refusals as successes — RESOLVED at the
   mechanism level (this commit); historical HC-01 figures retained as frozen
   evidence.** Publish both with the explanation — either one alone is attackable:
   - **(Historical, frozen) Row level: 41 of 48 HC-01 records (85%)** carried
     `policy_decision=deny` while `outcome` read `success`/`intent`.
   - **(Historical, frozen) Action level: 27 of 33 actions (82%)** were denied yet
     recorded as success.
   - *Why they differ (explained, not fudged):* HIGH-risk actions write **two**
     rows (mandatory-evidence `intent` + `success`); MEDIUM/LOW write one. HIGH is
     therefore double-counted at row level.
   - **(Fixed, this commit) Going forward**, the `@kernel_action` decorator
     (`src/kernels/_crosscutting.py`) relabels `outcome` to `denied` for **every**
     definitive `policy_decision=deny` (and `defer`) verdict — MEDIUM and LOW
     included — so a refusal can no longer be recorded as a success. This closes
     the MEDIUM-risk single-row-`success` asymmetry that was the worst structural
     defect (it covered `identity.create_identity`, `resource.create_quota`,
     `network.add_route`, `evaluation.approve_replan` / `execute_replan`).
     Proven systematically: `scripts/verify_audit_truthfulness.py` exercises the
     real decorator + policy engine + (temp) audit store across **all 20 denied
     actions** and asserts `outcome != "success"` for every one (PASS).
   - **The policy posture was completed, not just relabelled.** 9 operational
     actions the service legitimately performs (`identity.create_identity`,
     `memory.auto_cleanup`, `resource.allocate`, `resource.create_quota`,
     `network.add_route`, `trust.assign_score` / `establish_trust` / `revoke` /
     `update_score`) were moved from `INTERNAL_SERVICE_DENIED_ACTIONS` to
     `INTERNAL_SERVICE_ALLOWED_ACTIONS`. They are now truthfully recorded as
     `allow` / `success` instead of being flagged `denied` while the system
     depended on them; the remaining denied set is genuine authority/destructive
     actions requiring a verified human (OD-010).
   - **Remaining gap (item 3):** `policy_enforced=False` by default — a denial is
     now HONESTLY recorded but the body still runs under the record-only posture.
     Actually *blocking* denials is a separate OD-010 deployment decision (arming
     the C-2 gate). Closing that requires the identity-kernel permission
     grant/revoke feature and its tests to run under a human-sovereignty window
     first — flagged to the owner, not silently switched on. Never read "Policy
     Controlled" as "policy enforced".

## Cycle 3 — 2026-10-02 closed-loop hardening (measured)

Three autonomous hardenings this cycle, all measured, none touching the frozen
HC-01 evidence (every verifier REDIRs `AUDIT_DB_PATH` / `LIUHAO_WORKSPACE_ROOT` to
temp):

1. **Real-execution closed loop proven end to end** — `scripts/
   verify_real_execution_e2e.py` runs the exact production wiring
   (`LCore(register_local_tools=True).capability_executor()` → `AgentRuntime`) and
   asserts: (T1) a natural-language *"create a file named X containing Y"* goal
   produces a REAL on-disk file with the expected content and the goal reports
   `completed`; (T2) a `python:` directive goal (`python: result = 6*7+1`) produces
   a REAL computation (`43`); (T3) an unserved capability (*"search the web"*) FAILS
   HONESTLY — `state=failed`, zero false successes; (T4) the audit trail is
   populated. 9/9 checks PASS. **Honest limitation recorded:** the keyword planner
   cannot autonomously synthesize executable code — real compute requires the user
   (or a future LLM planner) to supply a `python:`/`code:` directive or tool-shaped
   `code`/`expression`; natural-language file-write is reachable, but search /
   memory / network capabilities are not served and therefore fail honestly rather
   than silently. This corrects a prior mis-synthesis: the gateway already wires a
   real executor (the earlier "unwired" gap was already repaired in cycle 2).

2. **Observability alert loop closed** — `src/observability/alerts/
   execution_binding.py` subscribes to the EventBus for `action_failed` /
   `task_failed` / `execution_completed` (failed tasks) at L7 and emits **real**
   alerts to the store (`data/observability/alerts.json`) + console, fail-safe
   (never raises). Installed at gateway startup (`src/gateway/main.py` lifespan).
   Default rules: any `task_failed` → HIGH; CRITICAL `action_failed` → CRITICAL;
   plan completed with failures → HIGH. 5/5 tests pass
   (`tests/observability/test_execution_alert_loop.py`). Store path REDIR-able via
   `LIUHAO_ALERTS_STORE_PATH`. Previously this subsystem was a stub —
   `evaluate_alerts` was never driven and execution events never reached it.

3. **ShellAdapter fail-closed safety gate + threat model** — `src/ai/
   world_interface_shell_gate.py` is the ONLY sanctioned constructor for a shell-
   capable `WorldInterface`: an `autonomous` interface cannot be built without an
   explicit human-arming `authorize` policy, and `shell=True` requires a logged,
   per-request `human_arm_token`. `docs/security/shell-adapter-threat-model.md`
   documents command-injection, host-wide blast radius, and why shell stays out of
   the autonomous default path. 10/10 tests pass
   (`tests/ai/test_shell_adapter_safety_gate.py`). `ShellAdapter` remains
   unregistered in the autonomous goal path — host-command execution is still
   fail-closed by absence, now also guarded by policy.

**Net state:** the autonomous loop can now do REAL local work (file write,
sandboxed compute) and fails honestly elsewhere; execution failures now raise real
alerts; host-command execution is gated and documented. Product gates P1–P3, P5,
P8–P10 remain open; **P7 (Recovery) flipped to PASS** this cycle (kernel crash
recovery + `execute_replan` re-execution + `retry_dead_letter` honest reporting +
`replan_goal` resume all measured by tests).

## Cycle 4 — 2026-10-02 operator UX + honesty hardening (measured)

Five autonomous hardenings this cycle, all measured, none touching the frozen
HC-01 evidence (every verifier REDIRs `AUDIT_DB_PATH` / `LIUHAO_WORKSPACE_ROOT` to
temp). All merged into `p36` and pushed; the shared test suite (`tests/ai` +
`tests/kernels/execution`) is **151 passed**, no regressions.

1. **Operator control panel is now real code (P1 / P5 / P10 progress).**
   `apps/console/console/src/components/OperatorControls.tsx` + `lib/operator.ts`
   add hire / create-goal / pause / resume / delete / stop / replan forms and
   buttons wired into `pages/Directory.tsx`; `operator.ts` calls the proven REST
   endpoints (`POST /v1/employees`, `POST /v1/goals`, `POST /v1/goals/{id}/stop`,
   `POST /v1/goals/{id}/replan`, pause/resume/delete). The console **builds** via
   `vite build` (40 modules, exit 0) and the dead read-only `src/ui/*.py`
   decorative package was deleted (9 files) plus the false-confidence
   `tests/frontend/test_phase7_productization.py`. Honest status: the controls
   exist and compile and target proven endpoints, but a **browser end-to-end
   click-through** is not yet run in this assessment — so P1/P5/P10 stay FAIL
   at the *proven* bar (code-complete, not UX-proven).

2. **Verification is no longer self-proving (P6 strengthen).** `Verifier.verify`
   (`src/kernels/execution/__init__.py`) now independently re-reads disk for
   file-write outcomes: a capability that returns `{"written": path}` without
   writing now FAILS verification (and demands replan) instead of trusting the
   returned dict. Proven by `scripts/verify_execution_independent_verification.py`
   (T1 claimed-but-missing → FAILED; T2 real write → SUCCESS; T3 wrong content →
   FAILED; T4 non-file → SUCCESS preserved) — **5/5 PASS**. This removes the
   worst "simulation ≠ execution" gap at the verification layer.

3. **WorldInterface + host-command are genuinely enforced and honestly
   documented (P4 / P5 strengthen).** `src/ai/world_interface.py` now:
   - fail-closes autonomous `FilesystemAdapter(root=None)` — read/list/write
     against an unbounded root is denied (human/legacy bounded-root path
     unchanged);
   - routes shell execution through the default-DENY `HostCommandBroker`
     (`LIUHAO_HOST_COMMAND_ENABLED` + capability policy + human approval), so
     `subprocess.run` is no longer bypassed;
   - replaces the misleading `capabilities=()` executor-fence *no-op* (which is
     always-allow per `fence.py:426/661`) with an honest statement that world /
     host-command authorization is enforced by the real policy gate, not the
     fence. `SandboxSpec` is honestly labelled "INTENT ONLY — not enforced".
   Proven by `tests/ai/test_world_interface_safety_fix.py` — **6/6 PASS**
   (autonomous unbounded-fs denied; bounded-fs allowed; human unbounded-fs
   allowed; shell without gate armed denied; shell with gate+policy allowed).

4. **Audit chain now proves goal→action linkage (P8 traceability).**
   `src/kernels/_crosscutting.py` adds a `KERNEL_ACTION_CORRELATION_ID`
   ContextVar + `kernel_action_correlation_id()` context manager;
   `ExecutionEngine.execute_goal` wraps the whole goal in it, so every kernel
   action's hash-chain audit event carries the **goal's** correlation id instead
   of a fresh random one. Proven by `scripts/verify_audit_goal_correlation.py`
   — **9/9 PASS** (the `execution.execute` events for the real `file_write` /
   `python_compute` actions match the goal's correlation id; the unset-path still
   emits a non-empty random id for backward compatibility). The audit chain can
   now prove "this goal executed this action" — previously impossible.

5. **Honest end-to-end acceptance re-confirmed (P2 / P6 refine).**
   `scripts/verify_e2e_real_execution.py` — **13/13 PASS** — drives a goal through
   the full pipeline with a real capability executor and asserts: a real
   `file_write` lands on disk with the expected content; `python_compute` produces
   a real result; an unserved capability FAILS HONESTLY (`status=unwired` /
   `simulated`, never "executed"); the hash-chain audit records the action. Ground
   truth established: `GoalDecomposer.decompose` (`src/kernels/execution/
   __init__.py`) is **deterministic keyword/regex, NOT LLM** (its own comment:
   "in production would use LLM"); of the 14 registered capabilities only
   `python_compute` + `file_write` are REAL executors (real only when a real
   executor is injected); the other 12 kernel capabilities are fail-closed
   (`unwired`) or honestly labelled `simulated` when opted in — never mislabelled
   as real.

**Net state:** the autonomous loop is now demonstrably honest at every layer —
planning is deterministic (not faked as LLM), execution is real for the two
served capabilities, verification re-reads disk, world/host-command actions are
fail-closed and routed through the real policy gate, and the audit chain ties
actions back to the goal that ran them. Product gates P1–P3, P5, P8–P10 remain
open; the binding gaps are now **UI-proven click-through** (P1/P5/P10 operator
panel) and **user-facing audit-query surface** (P8), plus genuine LLM planning
(P2) and multi-agent collaboration (P3) which require either a real provider key
(owner decision) or real orchestration wiring. **No gate flipped to PASS this
cycle** — P4/P6/P7 stay PASS.

## Cycle 5 — 2026-10-02 HTTP operational closure + audit UX (measured)

Two hardenings this cycle, both measured with real servers and real credentials,
none touching the frozen HC-01 evidence (every verifier REDIRs `AUDIT_DB_PATH` /
`LIUHAO_WORKSPACE_ROOT` to a fresh temp tree). Regression: **168 passed**
(`tests/ai` + `tests/kernels/execution` + `tests/gateway`).

### 1. Real HTTP closed loop — and the four defects it exposed

`scripts/verify_http_full_closure.py` (**31/31 PASS**) drives the **real**
`uvicorn` server on 127.0.0.1 as a **real registered human**: identity registered
via `register_human_identity.py`, JWT minted via `issue_console_token.py`, carried
in the `X-Liuhao-Token` header. This proves a genuine HTTP round-trip including
middleware, lifespan and the real auth dependency — not an in-process call.

It confirms: `POST /v1/goals` → poll to terminal → **the file exists on disk with
the exact expected content**; the goal is bound to a real employee; temp audit rows
carry the goal's correlation id; `POST /v1/goals/{id}/stop` terminates a **running**
goal as `cancelled`; `POST /v1/goals/{id}/replan` stays honestly `failed` when tasks
failed; and **the same request without a token returns 401**.

The reason this mattered is the important part: **P6 had only ever been proven
in-process, and moving to real HTTP immediately exposed four defects that
in-process testing could not see.** All four were real product bugs, all fixed at
the root (never by weakening the test):

1. **Asynchronous goal execution was unreachable over HTTP.** `AIStateManager.
   create_and_execute_goal` already supported `background=True`, but
   `GoalCreateRequest` had no such field and `create_goal` never passed it — so
   every HTTP-created goal finished *inside* the POST request. `POST
   /v1/goals/{id}/stop` was therefore structurally incapable of being a
   cancellation; it could only ever be a post-hoc state edit on an
   already-terminal goal. Fixed by plumbing an optional `background` field
   (default `False`, existing synchronous contract untouched).
2. **The cancel signal never reached the execution loop.** `_execute_task` was
   called without forwarding `stop_event`, so the "honour a stop before each
   attempt" check always saw `None` — dead code. Measured cost: a human stop during
   a long task took **39s** to take effect because the full retry budget had to
   burn down first. Fixed with a one-line forward.
3. **Stop overwrote real outcomes and corrupted employee KPI.** `stop_goal` stamped
   `error="aborted by human operator"` onto *any* goal receiving a stop POST —
   including an already-failed goal, **destroying its real root cause** — and booked
   every stop as `cancelled` in employee metrics, so a completed goal counted as
   **both completed and failed**. Both now apply only to a goal genuinely still
   running.
4. **Every HTTP goal reported zero tasks — a completed goal rendered identical to a
   no-op.** `_result_to_dict` read `ctx.tasks`, an attribute `ExecutionContext` does
   not have (tasks live on `ctx.plan`); its `hasattr` guard was always `False`. So
   `GET /v1/goals/{id}` and `/v1/workflows/{id}` showed every goal as "did
   nothing". Found while hardening two checks that had been passing vacuously.

This is the concrete meaning of `HTTP 200 ≠ operational closure`: the earlier P6
PASS was earned at the pipeline level and was **not** evidence that a user got a
result. A 200 that does not produce the artifact is a failure to fix, not a pass to
record.

### 2. User-facing audit surface (P8 → PASS)

`src/gateway/audit.py` (new, mounted at `src/gateway/main.py:531-532` with the same
human bearer dependency, plus an explicit `require_human_principal` inside each
handler so a missed mount cannot expose the trail anonymously) adds **read-only**
`GET /v1/audit/events`, `GET /v1/audit/events/{seq}`, `GET`+`POST /v1/audit/verify`
and `GET /v1/audit/summary`. It reuses the existing `AuditStore` read-only snapshot
path and the real `verify_integrity()` — no hashing reimplemented. Frontend typed
clients (`fetchAuditEvents` / `fetchAuditEventBySeq` / `verifyAuditChain` /
`fetchAuditSummary`) were added to `apps/console/console/src/lib/operator.ts`;
`tsc -b` and `vite build` both pass.

`scripts/verify_audit_user_surface.py` (**31/31 PASS**) proves it over real HTTP
with real auth: unauthenticated/invalid tokens → 401 everywhere; the tamper test
alters one row's `outcome` via raw SQL on a throwaway temp copy and the endpoint
returns `ok=false` / `content hash mismatch at seq=2`; the tampered row is still
readable via `/events/{seq}`; and OpenAPI confirms no mutation route exists beyond
the read-only verify itself.

### 3. Read-only identity/permission surface (P9 backend) + a corrected stale claim

`src/gateway/identity.py` (new) adds strictly **read-only** `/v1/identity/principals`,
`/v1/identity/principals/{id}`, `/v1/identity/permissions`, `/v1/identity/summary`,
mounted behind the same human bearer gate. OpenAPI confirms no non-GET method exists
under `/v1/identity` — deliberate: **grant/revoke over HTTP is an authority surface**
and is not added this cycle. `scripts/verify_identity_user_surface.py` is **48/48 PASS**
over a real server with real auth.

Honesty details that matter: the kernel stores permissions as a flat `Set[str]` and
does **not** retain per-grant scope, so rows report the identity's scope ceiling with
that limitation stated rather than inventing a per-grant scope. Over-flat filters
narrow server-side. An unset `LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY` yields a 200 with
explicit `warnings` / `registry.rows_refused` instead of an empty list that would read
as "there are no humans" — the empty-vs-unknown lie is avoided on purpose.

**A stale P9 claim is hereby corrected.** P9 previously asserted there was *no
authentication, no token issuance*. That is **wrong and now stricken**: the gateway has
real JWT validation (`src/gateway/policy.py:83`, via `src.security.get_jwt_handler()`),
and `POST /v1/auth/login` genuinely exists at `src/gateway/auth.py:415` — PBKDF2-HMAC-SHA256
comparison against real credential records, failure rate limiting, and JWT issuance.
`scripts/issue_console_token.py`'s docstring claiming "the gateway deliberately has no
`/v1/auth/login`" is itself stale and should be corrected at source. Still true: **no
MFA, no automated key rotation**, and (verified this cycle) human registrations are
**still memory-only and lost on restart under default env** — persistence works only
when both the registry file env and the integrity key are set.

### 4. Refusals recorded as success — fixed in the identity kernel

`grant_permission` / `revoke_permission` returned `False` on refusal without calling
`mark_action_denied`, so the `@kernel_action` hash-chain event recorded
`outcome=success` for a denied permission grant — the same class of lie previously fixed
in `resource.allocate` and `policy.unregister_rule`. Fixed on **four** refusal paths
(grant: unknown identity, scope ceiling; revoke: unknown identity, permission not held);
`return` semantics untouched. `scripts/verify_identity_denial_audit.py` was written
**first and confirmed FAILING before the fix** (12 checks failed — all four paths proved
to be recorded as `success`), then PASS after: no grant/revoke event carries a refusal
reason while reading success, and successful grant/revoke still record `success`.

Sweep result: the identity kernel has exactly three `@kernel_action` methods
(`create_identity`, `grant_permission`, `revoke_permission`) — no other refused-return
sites were found. Two honest notes left unresolved rather than papered over:
- `grant`/`revoke` are HIGH-risk and **not** in `INTERNAL_SERVICE_ALLOWED_ACTIONS`, so
  adjudicated as the internal-service principal every policy verdict is `deny` — meaning
  a **successful** grant can also be recorded `denied` (the mirror-image of the same
  distortion). Isolating the two requires a real C-3 sovereignty window. That executing
  HIGH-risk authority actions without human authorization is reachable at all is flagged
  as a genuine follow-up.
- `check_permission` is **not** `@kernel_action`-decorated, so permission *checks* leave
  no audit event at all. Reported, not silently fixed (adding it is a design decision).

**Net state:** the product closed loop `planning → execution → permissions →
WorldInterface → independent verification → audit → recovery → user result` is now
demonstrable **end to end over real HTTP by a real authenticated human**, with a
tamper-evident audit trail a user can query and verify. Remaining open gates are
P1/P2/P3/P5/P9/P10 — dominated by **UI not yet proven end-to-end in a browser**,
**no LLM-driven planning** (owner must supply a provider key), **no real
multi-agent orchestration**, and **identity/permissions not surfaced to the user**.

## Known over-claims that must be corrected externally

- `capability-registry.yaml:216` claims 6 network protocol adapters (A2A, MCP,
  gRPC, HTTP, WS, Internal); **only 3 exist** — A2A/MCP/gRPC `get_adapter()`
  return `None`.
- Security kernel "Vault Transit integration" is **dead** — `VAULT_AVAILABLE`
  has zero consumers in `src/`.
- Plugin `load_plugin()` **does not exist**; `PluginInterface` is absent;
  `plugins/registry_index.json` shows historical `active_plugins: 0`.
- G9 was labelled `VERIFIED` when only the upgrade/rollback path was proven —
  corrected to **NOT VERIFIED** for image build/boot.

  **Reason corrected 2026-10-02.** The recorded reason was "no docker daemon".
  That is no longer accurate and must not be repeated: a Docker daemon *is*
  installed and startable in principle (client `29.8.1`, Docker Desktop
  present at `AppData\Local\Programs\DockerDesktop`). The actual blocker is a
  host-level restriction: Docker Desktop's engine is WSL2-only here, and the
  host's enforcement policy denies `wsl.exe` outright, so the engine cannot
  start. Evidence, from `AppData\Local\Docker\log\host\monitor.log` on the
  2026-10-02 run:

  ```
  [main.wslexec][E] c:\windows\system32\wsl.exe --version failed:
      fork/exec C:\Windows\System32\wsl.exe: Access is denied.
  [main.engines] engine linux/wsl failed to start: checking preconditions:
      checking WSL version: getting WSL version: executing wsl --version:
      running wslexec: An error occurred while running the command.
  ```

  Consequently `docker compose up` cannot be executed by the project itself;
  this is an environment restriction, **not** evidence that the image is
  unbuildable. What *was* established on 2026-10-02:
  - the console bundle builds for real (`tsc -b` clean; `vite build` produces
    `index.html` + JS + CSS, 41 modules) — so the Dockerfile's console stage
    has a real, buildable input;
  - no headless browser could be started either, so the UI click-through was
    likewise not executed.

  G9 therefore stays **NOT VERIFIED**. `scripts/verify_deployed_product_closure.py`
  now exists to close it in one command on any host with a live daemon, and it
  is written to **fail (exit 2), never silently pass**, when Docker is absent.

### Honest partial proof — the product boots and serves REAL endpoints with NO Docker (2026-10-21)

G9 is "full Docker-compose + browser click-through closure". That remains BLOCKED on this
host. But the underlying product is **not** Docker-dependent to *run*: it is a plain
`uvicorn src.gateway.main:app` ASGI service plus a standalone-built SPA. The deploy worker
booted it for real (no daemon) and recorded the evidence in `docs/product/LOCAL-RUN.md`:

- `uvicorn src.gateway.main:app` starts; `/v1/health` → 200.
- `/v1/ready` → 200 with **15/15 subsystems healthy** and **38,468 REAL audit events**
  already in the chain (not fabricated — the audit store was read, not written for the test).
- `/v1/metrics` → 200 (Prometheus exposition, incl. the live system-health gauges).
- authenticated `GET /v1/kernels` → 200, **14 kernels** reported; **no-token**
  `GET /v1/kernels` → **401** — the human-sovereignty gate is real, not decorative.
- frontend builds standalone: `tsc -b` clean + `vite build` → `dist/` (323 KB JS / 43 KB CSS).
- `Makefile` gains additive `run` / `run-backend` targets invoking `scripts/start_liuhao.py`.

This is a HONEST partial closure: it proves the service is runnable and serves real,
measurable state, and that human-sovereignty gates fire. It does **not** substitute for
G9 (containerized, reproducible, browser-verified deployment) — which is still required
for `RELEASE READY` and remains portable to a capable host.

## Re-run / regenerate

Evidence for each gate lives with the audit records cited above. This document is
regenerated whenever a gate's underlying evidence changes; a gate moves
`FAIL → PASS` only on new measured evidence, never on code presence.
