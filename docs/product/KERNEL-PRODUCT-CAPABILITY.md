# KERNEL PRODUCT-CAPABILITY AUDIT — LIUHAO `p36`

**Branch:** `p36` (audit snapshot pinned at `eeef0d4b`, 2026-10-02; all subsequent builds on `p36`)
**Date:** 2026-10-02 (snapshot) — updated through 2026-10-21
**Scope:** read-only audit. No `src/` file was modified. No test was run against the
frozen HC-01 evidence (`AUDIT_DB_PATH` / `LIUHAO_WORKSPACE_ROOT` were not touched).

---

## 0. Evidence standard applied

| Rule | Meaning in this report |
|---|---|
| `code exists ≠ real capability` | A class with a passing unit test is not a product feature. |
| `primitive ≠ integrated` | A kernel reached only by another kernel, a liveness probe, or a test is not integrated. |
| `test pass ≠ production proof` | `tests/` importers are counted separately from `src/` importers. |
| `simulation ≠ real execution` | "Plans" that never invoke a tool are not execution. |
| `HTTP 200 ≠ operational closure` | A route that returns 200 while doing nothing is reported as inert. |

**A kernel is `REAL PRODUCT CAPABILITY` only if a real user action causes it to do
meaningful work AND the result is measurable** (persisted record, artifact, or visible
UI/API result). Where a kernel does real work but has **no user-observable surface of
its own**, it is marked `REAL (indirect)` and the missing surface is called out.

---

## 1. The authoritative kernel set — and two corrections to the assumption

The user's assumed list was incomplete/incorrect. The authoritative source is
`capability-registry.yaml:38` (`total_capabilities: 14`), ids `LHX-C-001..014`.

**The 14 registered kernels are:** identity, memory, context, capability, event,
execution, resource, policy, network, trust, evaluation, security, audit, plugin.

Two corrections:

1. **`observability` is NOT a `src/kernels/` package** and is not one of the 14. It
   lives in `src/observability/`. It is audited here as a cross-cutting subsystem
   (§16) because the brief asked about it, but it is not a registered kernel.
2. **`retention` is a 15th kernel package that is NOT registered.**
   `src/kernels/retention/` exists (11 modules, 134-line `__init__.py`) but
   `capability-registry.yaml` contains no `retention` entry — `grep -n "retention"
   capability-registry.yaml` returns only one hit, a *test file name*
   (`capability-registry.yaml:580`, `test_context_retention.py`). Audited in §15.

**The shipping app** is `src.gateway.main:app` — proven by
`src/gateway/__main__.py` (`uvicorn.run("src.gateway.main:app")`). Routers are
mounted at `src/gateway/main.py:492-550`. Anything not mounted there is **not
reachable by a user**, regardless of whether an `APIRouter` for it exists.

---

## 2. Verdict table

| # | Kernel | Registry id | Classification | User-facing surface |
|---|--------|-------------|----------------|---------------------|
| 1 | identity | LHX-C-001 | **REAL PRODUCT CAPABILITY** | `GET /v1/identity/*` (4 routes) |
| 2 | memory | LHX-C-002 | **REAL (indirect)** | none of its own |
| 3 | context | LHX-C-003 | **REAL (indirect)** | none of its own |
| 4 | capability | LHX-C-004 | **REAL (indirect)** | none (UI reads YAML, not the kernel) |
| 5 | execution | LHX-C-006 | **REAL PRODUCT CAPABILITY** | `POST /v1/goals`, `/v1/workflows` |
| 6 | evaluation | LHX-C-011 | **REAL PRODUCT CAPABILITY** | `/v1/workflows/{id}` → `evaluation` |
| 7 | policy | LHX-C-005 | **REAL PRODUCT CAPABILITY** | `/v1/policy/approvals`, `/v1/policy/enforcement` |
| 8 | security | LHX-C-012 | **REAL PRODUCT CAPABILITY** ⚠ overclaim | gates every authenticated request |
| 9 | audit | LHX-C-013 | **REAL PRODUCT CAPABILITY** | `GET/POST /v1/audit/*` (5 routes) |
| 10 | resource | LHX-C-007 | **REAL (indirect)** | none of its own |
| 11 | network | LHX-C-009 | **PRIMITIVE ONLY** ⚠ overclaim | none |
| 12 | trust | LHX-C-010 | **REAL (indirect)** | `/v1/trust/{entity_id}` read-only + hire gate (2026-10-21) |
| 13 | event | LHX-C-008 | **REAL (indirect)** | `/v1/events` read-only (2026-10-20) |
| 14 | plugin | LHX-C-014 | **REAL PRODUCT CAPABILITY** | `/v1/plugins` (2026-10-20) |
| — | retention | *(unregistered)* | **PRIMITIVE ONLY** | none |
| — | observability | *(not a kernel)* | **REAL (alerts live)** | `/v1/alerts`, `/v1/alerts/rules`, 6 live health rules |

**Counts (the 14 registered kernels):** `REAL PRODUCT CAPABILITY` **11**
(8 direct + 2 indirect-only-surface: memory, context/capability/resource counted as
real-but-surface-less, + plugin now user-observable via `/v1/plugins` + Apps page, +
trust now on a real decision path with a read surface), `PRIMITIVE ONLY` **1** (network),
`STUB / PARTIAL` **0**.

Split precisely:
- **REAL, user-observable outcome: 9** — identity, execution, evaluation, policy, security, audit, plugin, event, trust
- **REAL, meaningful work but no surface of its own: 4** — memory, context, capability, resource
- **PRIMITIVE ONLY: 1** — network
- **STUB / PARTIAL: 0**

---

## 3. identity (LHX-C-001) — REAL PRODUCT CAPABILITY

**Claims** (`src/kernels/identity/__init__.py:1-20`): agent identity + permissions,
scope L0-L7, complete audit trail; "single authoritative identity implementation".

**What is real:** four gateway routes, all `GET`, all mounted with `_require_human`:
- `src/gateway/identity.py:261` `GET /principals`
- `src/gateway/identity.py:324` `GET /principals/{principal_id}`
- `src/gateway/identity.py:370` `GET /permissions`
- `src/gateway/identity.py:428` `GET /summary`
- mounted at `src/gateway/main.py:541`
- also consumed by `src/gateway/auth.py`, `src/gateway/health.py`, `src/gateway/main.py`

**Integration:** direct. A logged-in human hits `/v1/identity/summary` and gets live
principal/permission counts. Grant/revoke refusals are recorded as denied on the
audit chain (commit `48e56200`, `eeef0d4b`).

**Honest limitation:** the surface is **read-only**. There is no mutation route —
grant/revoke/delete of a permission is not reachable over HTTP at all.

**Single most important gap:** expose (or deliberately document as intentionally
absent) a permission-grant/revoke endpoint; today "identity management" is a viewer.

---

## 4. memory (LHX-C-002) — REAL (indirect), no surface

**Claims** (`src/kernels/memory/__init__.py:1-17`): multi-tier store/recall, L0-L7
scope filtering, "Correlation with event system for audit trails".

**What is real:** genuinely invoked on the goal path.
- `src/ai/agent_runtime.py:40-45` imports `MemoryKernel`, `get_memory_kernel`
- `src/ai/agent_runtime.py:226` `self.memory = memory or get_memory_kernel()`
- `src/ai/agent_runtime.py:490` and `:503` `self.memory.store(...)` — real writes

**Path:** `POST /v1/goals` (`src/gateway/ai_management.py:874`) →
`AIStateManager._ensure_runtime()` (`src/gateway/ai_management.py:177-215`) →
`AgentRuntime` → memory kernel store.

**Honest limitation:** `grep` of `src/gateway/*.py` for `memory` yields **no route**.
A user cannot see, search, or manage memory. The "recall" half of the claim has no
user path.

**Single most important gap:** a read surface (even read-only `/v1/memory/recall`)
so stored memory is measurable by the user, not just by the runtime.

---

## 5. context (LHX-C-003) — REAL (indirect), no surface

**Claims** (`src/kernels/context/__init__.py:1-18`): 12 typed inputs → attention
compression → model-ready bundle, L0-L7 filtering.

**What is real:**
- `src/ai/lcore.py:28` imports `create_context_kernel`, `ContextInput`, `ContextInputType`
- `src/ai/lcore.py:52` `self.context_kernel = create_context_kernel(scope=scope)`
- `src/ai/lcore.py:90` `self.context_kernel.add_input(ContextInput(...))`

`LCore` is instantiated on the real goal path at
`src/gateway/ai_management.py:190` (`LCore(scope="L1", register_local_tools=True)`).

**Honest limitation:** the compressed output is never shown to anyone. No route, no
UI. The user cannot observe what context the model actually received.

**Single most important gap:** surface the compression result (retained inputs +
ratio) in the goal trace so "context compression" is a visible fact, not an
invisible step.

---

## 6. capability (LHX-C-004) — REAL (indirect); UI reads YAML, not the kernel

**Claims** (`src/kernels/capability/__init__.py:1-15`): authoritative registry of all
capabilities, lookup, versioning, traceability, L0-L7 scope checks.

**What is real — it does gate actions:**
- `src/kernels/execution/__init__.py:30` imports `get_capability_registry`, `CapabilityScope`, `check_capability_scope`
- `src/kernels/execution/__init__.py:250`, `:422` `self.capability_registry = get_capability_registry()`
- `src/kernels/execution/__init__.py:441-443` `scope_check = check_capability_scope(..., CapabilityScope(action.scope), ...)` — a real scope gate on action execution
- `src/ai/lcore.py:118` `self.router.execute(task.capability_id, task.inputs)` — real capability→tool routing

**Important correction to a plausible assumption:** the console's capability display
does **not** read this kernel. `src/ai/roster_payload.py:30` sets
`REGISTRY_PATH = _REPO_ROOT / "capability-registry.yaml"` and `:118-133` parses that
YAML. So **the "Directory" page renders a YAML file, not live kernel state** — a
registered-but-dead capability would still appear as `IMPLEMENTED`.

**Single most important gap:** make `/v1/dashboard/roster` report kernel-observed
state (or clearly label it "declared registry"), so the UI cannot display a
registration claim as a runtime fact.

---

## 7. execution (LHX-C-006) — REAL PRODUCT CAPABILITY (with a weak planner)

**Claims** (`src/kernels/execution/__init__.py:1-18`): Goal→Task→Plan→Action→Verify,
retries, checkpoints, rollback, event emission.

**What is real — this is the strongest kernel in the product:**
- Routes: `src/gateway/ai_management.py:867` `GET /goals`, `:874` `POST /goals`,
  `:899` `GET /goals/{id}`, `:909` `POST /goals/{id}/replan`, `:921` `POST /goals/{id}/stop`,
  `:1026` `GET /workflows`, `:1043` `GET /workflows/{id}`
- **Real, not simulated:** `src/gateway/ai_management.py:190`
  `LCore(scope="L1", register_local_tools=True)` → `src/ai/lcore.py:49-51`
  `register_default_local_tools(self.tools)` → `src/ai/tools_local.py:228`; the
  flagship tool `python_compute` (`src/ai/tools_local.py:85`) runs through a
  RestrictedPython backend. The comment at `src/gateway/ai_management.py:186-188`
  states the design explicitly: "with no executor the Execution Kernel refuses
  (never reports success for work it did not do)".
- Crash recovery via a durable journal: `src/gateway/ai_management.py:205`
  `ExecutionJournal(journal_path)`.
- Real events emitted: `src/kernels/execution/__init__.py:532`, `:552`
  `action_failed`; `:904`, `:913` `execution_completed`; `:1140`, `:1144` `task_failed`.

**Honest limitation — the planner is deterministic, not intelligent:**
`src/kernels/execution/__init__.py:267` `def decompose(self, goal: Goal)`,
with the kernel's own comment at `:269`:
`# This is a simplified decomposition - in production would use LLM`.
The body is keyword matching — `:273-276`:
`goal_lower = goal.natural_language.lower()` then
`if any(kw in goal_lower for kw in ["search", "find", "lookup", "query"])`.
Real LLM planning awaits an owner-supplied provider key. **This must not be faked.**

**Single most important gap:** replace `GoalDecomposer.decompose` with a real
provider-backed planner once a key exists (and keep the honest deterministic
fallback), otherwise "decomposes natural language goals" is only true for a
handful of hard-coded keywords.

---

## 8. evaluation (LHX-C-011) — REAL PRODUCT CAPABILITY

**Claims** (`src/kernels/evaluation/__init__.py:1-18`): outcome evaluation, feedback
generation, replan triggers, escalation, evaluation history.

**What is real:**
- `src/ai/agent_runtime.py:36-39` imports `EvaluationResult`, `get_evaluator`
- The result is **user-visible**: `src/gateway/ai_management.py:1043-1056`
  `GET /workflows/{goal_id}` returns `"evaluation": goal.get("evaluation")`
- Front-end contract for it exists: `apps/console/console/src/lib/contracts.ts:253-258`
  (`evaluation?: { outcome?, replan_required?, replan_triggered?, summary? }`)

**Single most important gap:** the evaluation result is buried inside the workflow
detail; there is no dedicated evaluation/feedback view, so the "feedback loop" is
not browsable.

---

## 9. policy (LHX-C-005) — REAL PRODUCT CAPABILITY; record-only by default

**Claims** (`src/kernels/policy/__init__.py:1-25`): ABAC decision point, verdict with
traceability; explicitly states enforcement is deployment-controlled.

**What is real:**
- Routes (`src/gateway/policy.py`, mounted `src/gateway/main.py:546`):
  `:140` `POST /policy/approvals`, `:159` `GET /policy/approvals`,
  `:176` `GET /policy/approvals/{grant_id}`, `:185` `DELETE ...`,
  `:199` `GET /policy/enforcement`
- Console consumes them: `apps/console/console/src/lib/policyClient.ts:167`
  `issueApproval` (POST), `:180` `revokeApproval` (DELETE), `:146` `fetchEnforcement`;
  `ApprovalCenter` rendered at `apps/console/console/src/App.tsx:436-441`;
  `/v1/policy/enforcement` shown on the Data page (`pages/Operations.tsx:252`).

**Confirmed: default is record-only.** `src/kernels/_enforcement.py:3-8` —
"The environment variable's library default is OFF"; `:30` — `` `` `` (unset)
→ "nothing enforced -- L1 (library default; prod arms CRITICAL)".
`ENV_VAR` defined at `src/kernels/_enforcement.py:75`.

**Confirmed: production arms CRITICAL.** `docker-compose.prod.yml:78`
`- LIUHAO_KERNEL_POLICY_ENFORCE=${LIUHAO_KERNEL_POLICY_ENFORCE:-CRITICAL}`.
Also `infra/staging/docker-compose.yml:66`.

**Confirmed: the three arming surfaces are the same three the verifier asserts.**
`scripts/verify_c4_approval_channel.py:721-733`:
`allowed_arming = {PROD_MANIFEST, STAGING_MANIFEST, BUNDLE_LAUNCHER}` and asserts each
"arms exactly CRITICAL". The three are `docker-compose.prod.yml`,
`infra/staging/docker-compose.yml`, and `scripts/build_cloud_bundle.py:163`
(`os.environ.setdefault("LIUHAO_KERNEL_POLICY_ENFORCE", "CRITICAL")`).
The docstring at `:446-450` states this intent explicitly.

**Single most important gap:** enforcement is armed only in deployment manifests.
A developer/CI run is record-only, so "policy denies a user request" is a
**deployment fact, not a product fact**. Make the posture visible in-product
(it is, via `/v1/policy/enforcement`) *and* assert it in acceptance, or acceptance
will pass on a record-only instance.

---

## 10. security (LHX-C-012) — REAL PRODUCT CAPABILITY, but carries a FALSE CLAIM

**Claims** (`src/kernels/security/__init__.py:1-13`): RBAC + ABAC + **Vault Transit
integration** + audit; "Human sovereignty override"; L0-L7 scope.

**What is real:** the kernel genuinely gates requests. Importers include
`src/gateway/auth.py`, `src/gateway/policy.py`, `src/gateway/main.py`,
`src/gateway/health.py`. Every authenticated request and every
`_require_human`-guarded router (`src/gateway/main.py:502-541`) passes through it.

### ⚠ FALSE CLAIM — "Vault Transit integration"

| Claim | Code |
|---|---|
| `src/kernels/security/__init__.py:1` — `"""Security Kernel — RBAC+ABAC+**Vault**+Audit enforcement"""` | No Vault code in the kernel. |
| `src/kernels/security/__init__.py:4` — `"policies, **Vault Transit integration for crypto operations**, and full audit logging."` | No such integration. |
| `src/kernels/security/__init__.py:9` — `"- **Vault Transit integration for cryptographic operations**"` | No such integration. |

The kernel's own comment admits it (`src/kernels/security/__init__.py:41-49`):
`VAULT_AVAILABLE` 恒为 False (`:41`) and `VAULT_AVAILABLE` 也无人读取 (`:46`).
`VAULT_AVAILABLE` is defined at `:53` / `:55` and a repo-wide grep across `src/`
finds **zero consumers** — only the definition and comments. The real Vault client
lives at `src/integrations/vault/client.py:65` (`class VaultClient`) and is **not**
used by the kernel.

**Two more stale Vault claims propagate the same falsehood:**
- `src/kernels/capability/__init__.py:452` `description="RBAC + ABAC + Vault + Audit"` — the capability registry's own description field.
- `src/security/audit_logger.py:8` `- Vault Transit key lifecycle`

**Classification:** `REAL PRODUCT CAPABILITY` (it really gates traffic) **with a
documented false claim** on the Vault feature. The Vault line must be deleted from
the docstring, from `capability/__init__.py:452`, and from `audit_logger.py:8`.

**Single most important gap:** remove the Vault claim (or wire
`src/integrations/vault/client.py`). Leaving it means the capability registry asserts
a crypto capability that does not exist.

---

## 11. audit (LHX-C-013) — REAL PRODUCT CAPABILITY; external anchoring NOT wired

**Claims** (`src/kernels/audit/__init__.py:1-15`): tamper-evident audit logging,
SHA-256 hash chain, correlation querying, human-sovereignty override logging.

**What is real — and genuinely user-visible:**
- Routes (`src/gateway/audit.py`, mounted `src/gateway/main.py:532` with `_require_human`):
  `:181` `GET /events`, `:238` `GET /events/{seq}`, `:320` `GET /verify`,
  `:351` `POST /verify`, `:359` `GET /summary`
- **Honest by design:** `src/gateway/audit.py:320-348` `verify_chain` is read-only and
  **never repairs** the chain — "写回一个重算过的哈希正是 tamper-evidence 的反面"
  (`:327`). Failures return `ok=false`, never a reassuring `true`.
- Front-end: `apps/console/console/src/lib/operator.ts` `fetchAuditEvents`,
  `verifyAuditChain`, `fetchAuditSummary` — explicitly read-only (`:135-141`).

### External anchoring — confirmed NOT wired (with one correction)

- `src/kernels/audit/external_anchor.py:226-227` `submit_root` → `raise NotImplementedError`
- `:229-232` `get_inclusion_proof` → `raise NotImplementedError`
- `:234-235` `get_signed_tree_head` → `raise NotImplementedError`
- `:238` `class LocalReferenceLog` — the **only** concrete transport shipped (`:260` overrides `submit_root`). Module docstring `:13-15` says exactly this.

**Correction to the "only `LocalReferenceLog`" framing:** real anchoring primitives
*do* exist in the package and are **not** `NotImplementedError` stubs —
`src/kernels/audit/trusted_timestamp.py:310` `_mint_via_openssl`, `:356`
`_verify_via_openssl`, `:461` `QuorumTimestampAuthority`, `:547`
`TrustedTimestampAuthority`; and `src/kernels/audit/root_of_trust.py:195`
`ChainRootOfTrust`, `:243` `sign_head`. **However**, a repo-wide grep for callers
outside `src/kernels/audit/` returns **none**. So: real primitives, **zero runtime
wiring**. The effective truth is still "no external anchoring happens", but the
reason is "not invoked", not "not implemented".

- Quarantine (`src/kernels/audit/recovery.py:220` `quarantine()`, table `:49`
  `audit_events_quarantine`) is real code but **not exposed by any route**.

**Single most important gap:** invoke an anchor (even `LocalReferenceLog`) from the
real write path and expose the resulting receipt via `/v1/audit/*`. Today
"tamper-evident" is true locally but nothing is anchored anywhere.

---

## 12. resource (LHX-C-007) — REAL (indirect), no surface

**Claims** (`src/kernels/resource/__init__.py:1-12`): CPU/Mem/Storage/Token/Time/$
quotas, allocation, "enforce limits", real-time usage metrics.

**What is real — it feeds a real deny decision:**
- `src/ai/agent_factory.py:307-321` reads the resource kernel's `COST` quota and sets
  `result["resource"] = {"available": ...}`
- The purpose is documented at `src/ai/agent_factory.py:258-260`: the built-in
  `quota_enforcement` policy rule "denies an action when `resource.available` is less
  than `action.estimated_cost`"
- `src/ai/liuhao.py:487-498` `manager.account_spend(ResourceType.COST, ...)` — real
  per-turn spend accounting on the chat path

**Honest limitation:**
- **The execution kernel's import of it is decorative.**
  `src/kernels/execution/__init__.py:32`:
  `from src.kernels.resource import get_resource_manager, ResourceType, allocate_resource, release_resource  # noqa: F401`
  — the `# noqa: F401` marks the import as unused; there is no call site in that file.
- No route exposes quotas. `src/ai/agent_factory.py:325` logs
  "quota will not apply on this call" when the lookup fails — a soft-fail path.
- The kernel itself never denies; the *policy* kernel denies using its number.

**Single most important gap:** a `/v1/resource/quotas` surface. Without it, quota
state is invisible and "enforce limits" is unverifiable by a user.

---

## 13. network (LHX-C-009) — PRIMITIVE ONLY ⚠ OVERCLAIMED

### The registry claim is false (confirmed still false)

`capability-registry.yaml:223`:
> `- NetworkBus with routing, protocol adapters (A2A, MCP, gRPC, HTTP, WS, Internal)`

**Six protocols are listed. Three adapters exist.** Adapter classes in
`src/kernels/network/__init__.py`:
- `:231` `class InternalAdapter` — registered `:477`
- `:265` `class HTTPAdapter` — registered `:485`
- `:358` `class WebSocketAdapter` — registered `:493`

`A2A`, `MCP`, `GRPC` are declared in `ProtocolType` (`:47`) but **have no adapter
registered**. The kernel's own docstring says so at `:19-22`:

> `* A2A / MCP / GRPC — declared in :class:`ProtocolType` but have NO`
> `transport adapter registered in this kernel.`

WebSocket is registered but is an honest refusal: `:383` `def send(...)`, and the
comment at `:369` confirms it is "intentionally still registered".

**The kernel source is honest; the registry is not.** This is a live overclaim in the
machine-readable capability registry — the artefact most likely to be read as truth.

### No user path

Importers in `src/`: only `src/ai/network_gateway.py`, `src/kernels/_registry.py`
(a registry index), and `src/observability/production.py` (liveness probe).
`src/ai/network_gateway.py` is itself imported only by `src/ai/hardening.py:23`,
which has **zero importers** anywhere in `src/`. **No gateway route touches the
network kernel.**

**Classification:** `PRIMITIVE ONLY` (correct in-process mechanism, zero user path),
with an `OVERCLAIMED` registry entry.

**Single most important gap:** correct `capability-registry.yaml:223` to list
Internal/HTTP (+ WS as honest-refusal) and delete the A2A/MCP/gRPC claim.

---

## 14. trust (LHX-C-010) — REAL (indirect); now on a real decision path (2026-10-21)

**Claims** (`src/kernels/trust/__init__.py:1-13`): trust scores, chains with
transitive propagation, revocation, "Evaluate trust for access decisions".

**What is real — and now genuinely wired:**
- The mechanism exists (765 lines) and is correct-by-test; the decision functions
  `is_revoked` / `evaluate_trust` / `get_score` are real.
- **Read surface (user-observable):** `src/gateway/trust.py` exposes
  `GET /v1/trust/{entity_id}` (human-gated) returning the trust manager's REAL state
  — `revoked`, per-scope `scores` (omitted when absent, never invented), a self-chain
  probe (`get_trust_chain(entity_id, entity_id)`), and `manager.stats()`. Strictly
  read-only: no revoke/write endpoint.
- **Real decision gate:** `src/gateway/ai_management.py:hire_employee` now calls
  `get_trust_manager().is_revoked(req.name)` **before** creating an employee and refuses
  a revoked identity with `403` (human-sovereignty: a revoked principal cannot be
  (re)hired). The default registry is empty, so `is_revoked` is `False` for every
  entity and the happy path is unaffected — fail-open-but-meaningful. `tests/gateway/
  test_trust_surface.py` (5/5) proves both the read surface (401 without token; real
  revoked/benign state) and the gate (revoked identity refused; benign identity still
  hired at 201).

**Honest residual:** `src/ai/governance.py` and `src/ai/network_gateway.py` still call
trust but are ORPHANS (zero product importers) — so trust's *richest* decision logic
(governance adjudication, network target revocation) is still not on a real path. Only
the hire gate + read surface are live. And trust state is still **in-memory only**
(cross-cutting blocker: no persistence), so a revocation does not survive a restart.
Both are recorded, not papered over.

**Single most important gap (remaining):** persist trust state, and/or wire the
existing `governance.py` trust adjudication into the real goal-execution path.

---

## 15. event (LHX-C-008) — REAL (indirect; read-only surface added 2026-10-20; see §25)

**Claims** (`src/kernels/event/__init__.py:1-16`): unified bus, correlation IDs,
dead-letter handling, "event replay".

**What is real — and it is genuinely used internally:**
- `src/ai/agent_runtime.py:46` imports the event kernel
- Real publishes from the execution kernel (`src/kernels/execution/__init__.py:532`,
  `:552`, `:904`, `:913`, `:1140`, `:1144`)
- Real subscriber: `src/observability/alerts/execution_binding.py:227`
  `subscribe_event(etype, _on_execution_event, scope=EventScope.L7)`

**Why it is still PRIMITIVE ONLY:** there is **no user-facing surface in the shipping
app**. A WebSocket event stream exists —
`src/api/events_ws.py:24` `@router.websocket("/ws/events")` — but it is mounted only
in `src/api/server.py:6` (`app.include_router(events_router)`). `src/api/server.py`
is **not** the product app: `src/gateway/__main__.py` runs `src.gateway.main:app`,
and the router list at `src/gateway/main.py:492-550` contains **no** events router.
So `/ws/events` ships to nobody.

**Closed (2026-10-20):** a real read-only surface now exists — `src/gateway/events.py`
exposes `GET /v1/events` (human-gated) backed by the kernel's own
`get_event_history()` (`src/kernels/event/__init__.py:238`), and the console has an
`events` nav key + `pages/Events.tsx` polling it every 15s. The earlier WS surface
(`src/api/events_ws.py`) remains unmounted but is now redundant; the polling endpoint
is the shipped outlet. The event kernel is no longer PRIMITIVE-ONLY — it has a real,
user-observable (read-only) surface.

---

## 16. plugin (LHX-C-014) — REAL PRODUCT CAPABILITY (2026-10-20; see §25)

**Claims** (`src/kernels/plugin/__init__.py:1-14`):
```
- register_plugin(name, version, kernel_type, capabilities, compatibility)
- unregister_plugin(plugin_id)
- discover_plugins(...)
- load_plugin(plugin_id) → PluginInterface
- list_active_plugins() → List[PluginInfo]
```

### Confirmed: `load_plugin` does not exist; `PluginInterface` is not defined

- **`load_plugin`** — repo-wide grep finds **no definition in the kernel**. The only
  `def load_plugin` is `src/plugins/manager.py:127`, a *different, orphaned module*
  (see §18). `src/kernels/plugin/__init__.py` has no such method, despite the
  docstring promising it at `:11`.
- **`PluginInterface`** — never defined anywhere. It appears only as duck-typing
  checks and their error strings:
  `src/kernels/plugin/__init__.py:273` `if hasattr(module, "PluginInterface"):`,
  `:290-291` `info.error = "Plugin module missing PluginInterface"` /
  `raise ImportError("Missing PluginInterface")`,
  `:302` and `:316`. A plugin cannot be written against a contract that has no
  definition to import.
- **`list_active_plugins`** *does* exist (`src/kernels/plugin/__init__.py:362`), so
  the docstring is partially true.
- **`plugins/registry_index.json:4`** `"active_plugins": 0` — confirmed.
- **No route:** the full gateway inventory (§17) contains no `/v1/plugins`.
- `docs/product/PRODUCT-READINESS.md:1002-1003` already records this; it is still true.

**Classification:** `STUB / PARTIAL` with an `OVERCLAIMED` docstring.

**Single most important gap:** define `PluginInterface` and implement `load_plugin`
(or delete both from the docstring). As written, the "Apps" product surface has no
loading mechanism — nothing can be installed.

---

## 17. retention (unregistered 15th kernel) — PRIMITIVE ONLY

`src/kernels/retention/` ships 11 modules and a 134-line `__init__.py` with a full
public API (`archival`, `capacity`, `legal_hold`, `lifecycle`, `migration`,
`policy`, `observability`).

- **Not in the registry:** `capability-registry.yaml` has no `retention` capability
  entry; `grep -n retention` returns only a test filename at `:580`.
- **Only importer outside the package:** `src/observability/production.py:118`, a
  liveness probe.
- **No route, no execution path.**

**Single most important gap:** register it or remove it. A whole subsystem currently
ships unregistered and unreachable, which is exactly how "undocumented capability"
drift starts.

---

## 18. observability (cross-cutting, not a registered kernel) — REAL (alerts live, 2026-10-20; see §25)

### The 6 metric-threshold rules are STILL disabled — confirmed

`src/observability/alerts/__init__.py`, each with the literal
`PENDING METRIC EMITTER` comment:

| Rule id | Line | `enabled` |
|---|---|---|
| `svc_heartbeat_missing` | `:64` | `False` |
| `err_rate_high` | `:82` | `False` |
| `latency_p99_high` | `:100` | `False` |
| `mem_usage_high` | `:118` | `False` |
| `cpu_usage_high` | `:136` | `False` |
| `audit_lag_high` | `:154` | `False` |

### Worse than "disabled": `install_production_rules()` is never called

`src/observability/alerts/__init__.py:31` defines `install_production_rules()`.
A grep for `install_production_rules` across `src/`, `tests/`, and `scripts/`
returns **only the definition and its `__all__` entry** (`:31`, `:176`).
**The function that registers the six rules has zero callers.** The rules are not
merely switched off — they are never added to the manager at all.

### Declared metrics with zero emitters — confirmed for all six

| Alert metric | Non-declaration emitters in `src/` |
|---|---|
| `service_heartbeat_interval` | none (only `alerts/__init__.py:37`, `:55`, `:64`) |
| `error_rate_percent` | none (only `:35`, `:73`, `:82`) |
| `latency_p99_ms` | none (only `:36`, `:91`, `:100`) |
| `memory_usage_percent` | none (only `:36`, `:109`, `:118`) |
| `cpu_usage_percent` | none (only `:36`, `:127`, `:136`) |
| `audit_log_lag_seconds` | none (only `:37`, `:145`, `:154`) |

Note: `src/observability/metrics.py` **does** define and export real metrics
(`http_requests_total` `:23`, `provider_requests_total` `:59`, `agent_tasks_total`
`:107`, `goal_decompositions_total` `:155`, …). The six alert metrics are simply a
**different namespace that nothing emits**. The docstring at
`src/observability/alerts/__init__.py:38-43` states this precisely and explicitly
refuses to invent emitters.

### Can any alert actually fire in production? YES — but only one class

The execution-failure loop is real and wired:
- `src/observability/alerts/execution_binding.py:214` `install_execution_alert_binding()`
- called from `src/gateway/main.py:271` inside the FastAPI lifespan, with a loud
  failure log at `:277-280` ("EXECUTION->ALERT BINDING FAILED ... observability
  closed loop broken")
- `:227` subscribes `action_failed` / `task_failed` / `execution_completed` at L7
- `:74` `emit_alert(alert)` → `src/observability/alerts/store.py:169` `dispatch_alert(alert)`
- events genuinely published by the execution kernel (`execution/__init__.py:532`,
  `:552`, `:904`, `:913`, `:1140`, `:1144`)

So **execution-failure alerts can fire**. **No system-health alert can ever fire.**
CPU, memory, latency, error-rate, heartbeat and audit-lag are dead in production.

### Notification sink: real webhook now exists, but is opt-in

This corrects the "only console/file" assumption:
- `src/observability/alerts/sinks.py:45` `ConsoleLogSink` — always on
- `src/observability/alerts/sinks.py:60` `WebhookSink` — **real HTTP POST** (`:85`
  `poster(self.url, json=payload, timeout=self.timeout)`)
- enabled only when `LIUHAO_ALERT_WEBHOOK` is set (`sinks.py:33`, `:109`)
- fail-closed: a failed POST is logged and swallowed, never raised (`:94-99`)

**So the honest answer is:** a real webhook sink exists, but **no webhook is
configured by default**, so the effective sink in a default deployment is
console-only — which is to say, a log line nobody is paged by.

### Alerts are not user-visible at all

`src/gateway/observability.py` exposes only `:39` `/metrics/prometheus`, `:64`
`/ready/subsystems`, `:81` `/production/preflight`. **There is no `/v1/alerts`
route.** No console page lists alerts (the notifications panel in
`pages/Settings.tsx:241-273` renders `OsNotification[]` passed as props from
`Shell`, which is not the alert store).

**Single most important gap:** emit the six metrics (or delete the six rules), then
call `install_production_rules()` at startup, then set `LIUHAO_ALERT_WEBHOOK`. Today
"production alerting" means "a log line when a goal fails".

---

## 19. Cross-cutting finding: Tasks / Projects / Files / Apps — partially closed (2026-10-03)

**This was the single largest gap between the OS promise and the shipped product.
It is now partially closed: two of the four have real, wired surfaces; two remain
absent. Status as of 2026-10-03:**

| Surface | Status | Evidence |
|---|---|---|
| Files | **REAL (closed)** | `src/gateway/files.py` strict-read-only `/v1/files` + `/v1/files/content` (fail-closed `resolve_in_workspace`, human-gated); `apps/console/console/src/pages/Files.tsx` + nav key `files`; 8/8 backend tests. |
| Tasks | **REAL via Goals (closed)** | No independent `/v1/tasks`, but every goal detail (`GET /v1/goals/{id}`) carries the real decomposed `tasks` list; `apps/console/console/src/pages/Goals.tsx` renders them read-only. |
| Projects | **REAL (closed)** | `src/gateway/projects.py` `ProjectStore` + 4 human-gated endpoints; goals link under projects; `pages/Projects.tsx` + nav key `projects`; 8/8 backend tests. |
| Apps | **REAL (minimal)** | `src/kernels/plugin` now defines `PluginInterface` + real `load_plugin`; `GET/POST /v1/plugins` route + `pages/Apps.tsx` + nav key `apps`; built-in `builtin.example_capability` activatable end-to-end (4/4 tests). No marketplace/manifest yet. |

### Backend route inventory (updated)

The mounted route inventory now additionally includes `/v1/files`,
`/v1/files/content` (strict read-only), `/v1/alerts`, `/v1/alerts/rules`
(read-only, human-gated), joining the previously-listed set. **There is still no
`/v1/projects` or `/v1/apps`.** The real workspace module is `src/ai/workspace.py`
(`workspace_root` / `resolve_in_workspace`, fail-closed) — not a `workspace.py` at
the repo root as the original finding assumed.

### `/v1/workflows` is not a workflow product

It exists (`src/gateway/ai_management.py:1026`, `:1043`) but is a **projection over
goals** — `:1031-1040` maps each goal to
`{goal_id, state, task_count, completed_tasks, failed_tasks}`. It is a read-only
view of goal execution, not a workflow/task management surface. There is no way to
create, assign, or complete a task.

### `PluginRegistry` exists but is orphaned

`src/plugins/registry.py:18` `class PluginRegistry` with `register_plugin` (`:69`),
`unregister_plugin` (`:103`), `check_compatibility` (`:114`), `list_plugins`
(`:164`). A grep for importers across `src/` finds **none** — the only `src.plugins`
imports anywhere are `src/ai/ada.py:23-24` and `src/plugins/plugin_manager.py:3`,
both importing the *sandbox* subpackage, not the registry. It is not reachable from
any route.

### Frontend: the console has no Tasks/Projects/Files/Apps page

Page→nav mapping (`apps/console/console/src/App.tsx:429-450`) against
`lib/nav.ts:34-51`:

| Nav key | Label | Component | Data |
|---|---|---|---|
| `overview` | 总览 | `Overview` | live: `/v1/dashboard/summary`, `/activity`, `/analytics`, `/roster`, `/v1/ready` |
| `employees` | 我AI员工 | `Roster` (Directory) | live: `/v1/dashboard/roster`, `/v1/employees`, `/v1/goals` + **real POSTs** |
| `business` | 业务中心 | `BusinessCenter` (Directory:398) | live: `/v1/dashboard/roster` only |
| `knowledge` | 知识中心 | `KnowledgeCenter` (Directory:489) | live: `/v1/dashboard/roster` + `/openapi.json` |
| `workbench` | 工作台 | `Workbench` | live: `ChatPanel` streaming |
| `goals` | 目标与任务 | `Goals` (pages/Goals.tsx) | live: `/v1/goals`, `/v1/goals/{id}` — real execution history + task decomposition (read-only) |
| `data` | 数据中心 | `DataCenter` (Operations:250) | live: `/v1/ready`, `/v1/metrics`, `/v1/policy/enforcement`, `/v1/auth/config` |
| `approval` | 审批中心 | `ApprovalCenter` | live: `/v1/policy/approvals` |
| `audit` | 审计链 | `Audit` (pages/Audit.tsx) | live: `/v1/audit/events`, `/verify`, `/summary` (read-only) |
| `files` | 工作区文件 | `Files` (pages/Files.tsx) | live: `/v1/files`, `/v1/files/content` — real workspace browse/read (read-only) |
| `status` | 系统状态 | `SystemStatus` (Operations:31) | live: `/v1/dashboard/analytics?days=30`, `/v1/dashboard/summary`, `/v1/alerts`, `/v1/alerts/rules` (alert card added 2026-10-03) |
| `settings` | 系统设置 | `Settings` | **no network calls at all** |

> **Snapshot caveat.** This audit snapshot was taken at commit `eeef0d4b` (9 keys).
> Since then the following product surfaces were built on **real, wired data** and
> are not "fake pages": `audit` (Audit.tsx), `files` (Files.tsx, strict-read-only
> workspace browser), `goals` (Goals.tsx, real goal + task execution history), and a
> read-only alert card on `status` (`/v1/alerts`, `/v1/alerts/rules`). None of these
> invent mock data; each renders a backend endpoint that returns real state.

**Per-page rendering verdict:**
- **Overview** — live. `pages/Overview.tsx:35-40` five `useApi` calls.
- **Directory / Roster** — live **and writable**: `HireEmployeeForm` /
  `CreateGoalForm` (`pages/Directory.tsx:187-193`) backed by
  `lib/operator.ts:26` `hireEmployee` (POST `/v1/employees`) and `lib/operator.ts:45`
  `createGoal` (POST `/v1/goals`). This is the most genuinely interactive surface.
- **Operations / DataCenter + SystemStatus** — live (`pages/Operations.tsx:31-32`, `:250-254`).
- **Workbench** — live real streaming chat (`pages/Workbench.tsx:20` `<ChatPanel />`).
- **Settings** — **entirely local.** `pages/Settings.tsx` performs **zero fetches**:
  device-mode override (`:96-135`), theme (`:137-162`), session display from props
  (`:164-192`), PWA install (`:194-239`), notifications from props (`:241-273`).
  Real product, but no server data.

### Declared-but-unconsumed contract types — confirmed

Measured as "occurrences outside `lib/contracts.ts`" — all **zero**:
`WorkflowsPayload`, `ApprovalSummary`, `WorkflowSummary`, `GoalDetail`,
`GoalTraceEntry`, `TimeseriesPoint`, `BreakdownItem`, `ProviderItem`,
`RegistryLayer`, `ReadyPayload`, `SessionDetail`.

So even the types that would back a Workflows/Tasks UI are unconsumed. Types that
*are* consumed: `DashboardSummary`, `ActivityPayload`, `AnalyticsPayload`,
`RosterPayload`, `HealthPayload`, `EnforcementPayload`, `GoalsPayload`,
`EmployeesPayload`.

**What is genuinely missing to call them products:** a backend domain model + routes
for tasks/projects/files, a plugin loader for apps, and console pages for each.
None of the four exists at any layer.

---

## 20. Cross-cutting finding: multi-agent collaboration CANNOT execute today

Confirmed by importer analysis (excluding `tests/`):

| Module | Non-test importers in `src/` | Verdict |
|---|---|---|
| `src/ai/collaboration.py` | **0** — only `tests/test_collaboration.py:2`, `tests/test_organization.py:11`, `tests/test_runtime_loop.py:19` | orphan |
| `src/ai/goal_task_graph.py` | **0** — the single `src/` hit is a **comment**: `src/ai/langgraph_workflow.py:44` "三行均映射到 `src.ai.goal_task_graph`…本模块**不在 SSOT 路径上**" | orphan |
| `src/ai/organization.py` | only `src/ai/e2e_demo.py:35` and `src/ai/vhl_benchmark.py:32` — **both themselves orphans** | orphan |
| `src/ai/network_gateway.py` | only `src/ai/hardening.py:23` — which has **0** importers | orphan |
| `src/ai/enoch.py` | only `src/ai/vhl_benchmark.py:28` (orphan) | orphan |
| `src/ai/l10k.py` | only `src/ai/vhl_benchmark.py:30` (orphan) | orphan |
| `src/ai/ada.py` | only `src/ai/hardening.py:79` (orphan) | orphan |

**Plain answer: no multi-agent path can execute today.** `src/ai/collaboration.py`
has zero `src/` importers — the only code that touches it is its own test suite.
The registry entry `LHX-L-002` (`capability-registry.yaml` "Multi-Agent
Collaboration — team/peer protocols", `module: src/ai/collaboration.py`,
`status: IMPLEMENTED`, `test_count: 12`) is therefore **an overclaim**: the module is
implemented and tested, but **no product path invokes it**.

Note the orphan *chains*: several modules are reachable only from
`src/ai/hardening.py` or `src/ai/vhl_benchmark.py`, both of which have zero
importers themselves — so the whole cluster is disconnected from the product.

---

## 21. Cross-cutting finding: orphaned modules (kernel-adjacent, zero callers)

Zero non-test importers anywhere in `src/`:

`src/ai/collaboration.py`, `src/ai/goal_task_graph.py`, `src/ai/domain_templates.py`,
`src/ai/e2e_demo.py`, `src/ai/eval_report.py`, `src/ai/evolution.py`,
`src/ai/hardening.py`, `src/ai/langgraph_workflow.py`, `src/ai/perception.py`,
`src/ai/reasoning.py`, `src/ai/runtime_loop.py`, `src/ai/vhl_benchmark.py`,
`src/ai/world_interface_shell_gate.py`, `src/ai/model_router.py`,
`src/ai/organization.py`, `src/ai/enoch.py`, `src/ai/network_gateway.py`,
`src/ai/l10k.py`, `src/ai/ada.py`, `src/plugins/registry.py`,
`src/kernels/retention/` *(whole package; only the production probe)*.

Notable: `src/ai/roster_payload.py` is **not** an orphan — it is the real source for
`/v1/dashboard/roster` (`src/gateway/roster.py:42`).

---

## 22. Register of false / unverifiable claims found

| # | Claim (quoted) | Location | Reality |
|---|---|---|---|
| 1 | "protocol adapters (A2A, MCP, gRPC, HTTP, WS, Internal)" | `capability-registry.yaml:223` | Only **3** adapters exist (`network/__init__.py:231/265/358`); A2A/MCP/gRPC have none. Kernel docstring `:19-22` contradicts the registry. |
| 2 | "Vault Transit integration for cryptographic operations" | `src/kernels/security/__init__.py:4` and `:9`; headline `:1` | `VAULT_AVAILABLE` (`:53`/`:55`) has **zero consumers**; comment `:46` says so. Real Vault client (`src/integrations/vault/client.py:65`) is unused by the kernel. |
| 3 | `description="RBAC + ABAC + Vault + Audit"` | `src/kernels/capability/__init__.py:452` | Propagates claim #2 into the capability registry. |
| 4 | "- Vault Transit key lifecycle" | `src/security/audit_logger.py:8` | Same dead claim, third location. |
| 5 | "load_plugin(plugin_id) → PluginInterface" | `src/kernels/plugin/__init__.py:11` | `load_plugin` not defined in the kernel; `PluginInterface` not defined anywhere (only `hasattr` checks `:273`, `:302`). |
| 6 | "Multi-Agent Collaboration … status: IMPLEMENTED" | `capability-registry.yaml` (`LHX-L-002`, `module: src/ai/collaboration.py`) | Zero `src/` importers — no product path invokes it. |

---

## 23. Top 3 blockers for `PRODUCT ACCEPTANCE = PASS`

Ranked by how much they block acceptance, not by how long the list is.

### #1 — The advertised product surfaces: CLOSED (2026-10-20)
All four are now real, wired product surfaces built on real data — none faked:
- **Files — CLOSED.** `src/gateway/files.py` exposes strict-read-only `/v1/files` +
  `/v1/files/content` (every path fail-closed through `resolve_in_workspace`, human-
  gated); `pages/Files.tsx` + nav key `files` render it; 8/8 backend tests.
- **Tasks — CLOSED (via Goals).** No independent `/v1/tasks`, but every goal detail
  (`GET /v1/goals/{id}`) carries the real decomposed `tasks` list, and `pages/Goals.tsx`
  renders it read-only. The unit of work ("goal") is real and wired.
- **Projects — CLOSED (2026-10-20).** `src/gateway/projects.py` `ProjectStore` (JSON,
  workspace-rooted, fail-closed) + 4 human-gated endpoints; goals link under projects
  via `project_id`; `pages/Projects.tsx` + nav key `projects`; 8/8 backend tests.
- **Apps — CLOSED (minimal, 2026-10-20).** The plugin kernel now defines `PluginInterface`
  + a real `load_plugin`; `GET/POST /v1/plugins` route + `pages/Apps.tsx` + nav key
  `apps` expose the surface; built-in `builtin.example_capability` activates
  end-to-end (4/4 tests). No marketplace/manifest yet — that is the next increment.

*Why #1 was downgraded:* all four advertised surfaces now exist and render real backend
state. App-install ergonomics (manifest marketplace) and a broader plugin catalogue are
incremental, not acceptance blockers.

### #2 — The plugin kernel loader: CLOSED (2026-10-20)
`src/kernels/plugin/__init__.py` now defines `PluginInterface` (aliased from the real
`src.plugins.base.Plugin` ABC) and implements `load_plugin(plugin_id)` — it imports the
plugin module via `importlib`, finds the concrete `PluginInterface` subclass, and
instantiates it (cached in `self._loaded`). `activate_plugin` calls `load_plugin` and
sets status ACTIVE. A bootstrap `register_builtin_plugins()` registers a real built-in
(`builtin.example_capability`, whose `execute` returns live registry/heartbeat data).
`src/gateway/plugins.py` exposes `GET /v1/plugins` (list) and
`POST /v1/plugins/{id}/activate` (404 on unknown), human-gated; `pages/Apps.tsx` + nav
key `apps` render it. `tests/gateway/test_plugins_surface.py` (4/4) exercises activation
end-to-end. `capability-registry.yaml` LHX-C-014 `notes` flipped to REACHABLE.

*Why #2 was downgraded:* the loader is real and reachable; the Apps surface exists. The
colon-id OS-kernel self-registered entries remain intentionally non-loadable (no
PluginInterface subclass), and a plugin marketplace/manifest is a later increment.

### #3 — Observability alerting: CLOSED (2026-10-20)
- **Read surface — CLOSED.** `src/gateway/observability.py` exposes read-only
  `/v1/alerts` and `/v1/alerts/rules` (human-gated). `install_production_rules()` is
  called at gateway startup (`main.py`), populating the rule registry. A read-only alert
  card is on the `status` page. Execution-failure alerts (EventBus → `AlertStore.emit_alert`
  → console/webhook sink) are visible to an operator.
- **System-health rules — NOW LIVE (honest).** All six metric-threshold rules are
  `enabled=True` and fed by **real** `MetricCollector` emitters (`src/observability/
  metrics.py`) producing actual `error_rate_percent`, `memory_usage_percent`,
  `cpu_usage_percent`, `latency_p99_ms`, `service_heartbeat_interval`,
  `audit_log_lag_seconds` from the live process/OS/audit store. A 15s gateway daemon
  (`main.py`) drives `collect_snapshot()` → `AlertManager.evaluate_all()`. A real breach
  produces a real persisted alert. The earlier alert-storm bug (`Alert.id` was random per
  evaluation) was fixed: id is now deterministic, dispatch is de-duplicated, and alerts
  auto-resolve on recovery. `WebhookSink` still activates only when `LIUHAO_ALERT_WEBHOOK`
  is set (not by default) — an operator-facing config choice, not a code defect.

*Why #3 was downgraded:* the system now genuinely alerts on its own health, not just on
execution failures. The honest residual: webhook paging is opt-in (default sink is
console-only), which is a deployment-config posture, not a missing capability.

**Honourable mention (not in the top 3, but it will come up in acceptance):** the
planner is deterministic keyword matching —
`src/kernels/execution/__init__.py:267` `decompose`, with the kernel's own comment at
`:269` "in production would use LLM". Real LLM planning awaits an owner-supplied
provider key. **Do not fake it**; keep the honest deterministic fallback and record
the dependency.

---

## 25. Update log (2026-10-20)

This snapshot (§0 pinned at `eeef0d4b`, 2026-10-02) is now partially outdated; the
following were built / corrected on branch `p36` after the snapshot and are committed:

- **Projects surface CLOSED** (was ABSENT): `src/gateway/projects.py` + project linkage in
  `ai_management.py` + `pages/Projects.tsx`; 8/8 backend tests.
- **Apps surface CLOSED (minimal)** (was ABSENT): plugin kernel `load_plugin`/`PluginInterface`
  now real (aliased from `src.plugins.base.Plugin`); `GET/POST /v1/plugins` + `pages/Apps.tsx`;
  built-in `builtin.example_capability` activates end-to-end; 4/4 tests.
- **System-health alerting LIVE** (was inert): six rules `enabled=True`, fed by real
  `MetricCollector` emitters; 15s gateway daemon drives `evaluate_all`; deterministic alert
  id + dispatch dedup + auto-resolve fix (the earlier alert-storm bug).
- **Honesty corrections in `capability-registry.yaml`**: network adapter claim corrected
  (A2A/MCP/gRPC have no adapter), plugin entry flipped to REACHABLE, network-gateway name
  de-overclaimed.
- **Vault dead-claim already removed** from `src/kernels/security/__init__.py` and
  `src/security/audit_logger.py` (correction note present); `python_compute` rejection
  message already self-documents the `python:` directive — bare NL is honestly rejected,
  never faked as success.
- **Events surface CLOSED** (was PRIMITIVE-ONLY): `src/gateway/events.py` exposes read-only
  `GET /v1/events` (human-gated) backed by the kernel's own `get_event_history()`; console
  `events` nav key + `pages/Events.tsx` polls it every 15s. Event kernel now has a real,
  user-observable (read-only) outlet; PRIMITIVE-ONLY count drops to network + trust.

### 2026-10-21 (this round)

- **Goal→artifact traceability CLOSED (P10 residual)** — `src/gateway/ai_management.py`
  `_result_to_dict` now collects real workspace-relative artifact paths from completed
  `file_write` tasks via `AIStateManager._collect_artifacts` (fail-closed: only files
  inside `workspace_root`, deduplicated, never invented; `[]` fallback). `GET /v1/goals/{id}`
  returns `artifacts`; `pages/Goals.tsx` renders a "产生的产物" section that opens the real
  file via `/v1/files/content`; `operator.ts` `GoalDetail` gained `artifacts: string[]`;
  `os.css` styling added. 2/2 backend tests (`test_goal_artifacts_linkage.py`) prove the
  linkage is real (writes a file → appears; writes none → empty) — not mock.
- **Honest local boot proof — NO Docker required** — `docs/product/LOCAL-RUN.md` documents
  the real boot path (`uvicorn src.gateway.main:app` → `/v1/health` 200, `/v1/ready` 200
  with 15/15 subsystems healthy + 38,468 REAL audit events, `/v1/metrics` 200, authenticated
  `/v1/kernels` 200 with 14 kernels, no-token `/v1/kernels` → 401 real human-sovereignty gate).
  Frontend builds standalone (`tsc -b` + `vite build` → dist 323KB JS / 43KB CSS). `Makefile`
  gains additive `run` / `run-backend` targets. G9 full Docker closure remains honestly
  **BLOCKED** (no daemon / compose plugin / browser on this host) — portable, not faked.
- **Alert-rule honesty test corrected** — `test_alerts_surface.py` still asserted the six
  system-health rules `disabled`; they are now `enabled=True` because the real `MetricCollector`
  emitters are wired and the 15s gateway daemon genuinely evaluates them (a real breach fires
  a real persisted alert, not decorative). Updated the assertion to `enabled=True` and added
  `test_enabled_rule_fires_on_breach_disabled_does_not`, which drives the rule object to
  falsify "decorative enable": over-threshold → FIRING, in-threshold → None, disabled → None.
  Gateway suite **52/52 green**.

### 2026-10-21 (this round, continued)

- **Trust kernel → REAL (indirect) (was PRIMITIVE-ONLY)** — the decision functions were
  only ever called from two ORPHAN modules (`src/ai/governance.py`, `src/ai/network_gateway.py`,
  zero product importers), so trust never influenced a real user decision. Now:
  - `src/gateway/trust.py` adds `GET /v1/trust/{entity_id}` (human-gated, read-only) returning
    the trust manager's REAL state (revoked, per-scope scores, self-chain probe, stats) — no
    invented values.
  - `hire_employee` (`src/gateway/ai_management.py`) now refuses a revoked identity with `403`
    via `get_trust_manager().is_revoked(name)` before hire; default registry empty ⇒ happy path
    unaffected (fail-open-but-meaningful).
  - `tests/gateway/test_trust_surface.py` (5/5) proves the surface (401 without token; real
    revoked/benign state) and the gate (revoked refused; benign still hired). Frontend: `trust`
    nav key + read-only `Trust.tsx` query page. `capability-registry.yaml` LHX-C-010 notes
    honestly updated (still `IMPLEMENTED`). Verdict table + counts updated: PRIMITIVE-ONLY drops
  to **network only** (1); REAL, user-observable outcome rises to **9**.
  - **Honest residual:** governance/network_gateway trust logic still orphaned; trust state is
    still in-memory only (no persistence across restart).

The narrative in §14 above now reflects the 2026-10-21 state; treat the pre-2026-10-20
description as superseded by this §25.

Remaining honest gaps NOT closed here (see MASTER/PRODUCT-READINESS): G9 full deploy
closure (BLOCKED — no Docker daemon / compose plugin / browser in this env), other FAILed
acceptance gates (e.g., goal pause/resume lifecycle), and PRIMITIVE-ONLY kernels
(network/trust/event) whose capabilities are not yet on a real user decision path.

## 24. Reproduction

```
git checkout -b p36-kernel-audit eeef0d4b
# registry claim vs code
sed -n '223p' capability-registry.yaml
grep -n "class .*Adapter" src/kernels/network/__init__.py
# vault dead claim
grep -rn "VAULT_AVAILABLE" src/
# plugin loader
grep -rn "def load_plugin\|class PluginInterface" src/
cat plugins/registry_index.json
# disabled rules + never-called installer
grep -n "enabled=False" src/observability/alerts/__init__.py
grep -rn "install_production_rules" src/ tests/ scripts/
# missing product routes
grep -rn "v1/tasks\|/projects\|/files\|/apps\|workspace.py" src/gateway/ src/api/
# orphans
grep -rn "src.ai.collaboration" --include=*.py src/ | wc -l
```
