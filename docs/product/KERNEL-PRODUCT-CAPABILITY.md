# KERNEL PRODUCT-CAPABILITY AUDIT — LIUHAO `p36`

**Branch:** `p36-kernel-audit` (pinned at `eeef0d4b`)
**Date:** 2026-10-02
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
| 12 | trust | LHX-C-010 | **PRIMITIVE ONLY** | none |
| 13 | event | LHX-C-008 | **PRIMITIVE ONLY** | WS exists but is **not mounted** |
| 14 | plugin | LHX-C-014 | **STUB / PARTIAL** ⚠ overclaim | none |
| — | retention | *(unregistered)* | **PRIMITIVE ONLY** | none |
| — | observability | *(not a kernel)* | **STUB / PARTIAL** | 3 routes, **no alerts surface** |

**Counts (the 14 registered kernels):** `REAL PRODUCT CAPABILITY` **10**
(8 direct + 2 indirect-only-surface: memory, context/capability/resource counted as
real-but-surface-less), `PRIMITIVE ONLY` **3** (network, trust, event),
`STUB / PARTIAL` **1** (plugin).

Split precisely:
- **REAL, user-observable outcome: 6** — identity, execution, evaluation, policy, security, audit
- **REAL, meaningful work but no surface of its own: 4** — memory, context, capability, resource
- **PRIMITIVE ONLY: 3** — network, trust, event
- **STUB / PARTIAL: 1** — plugin

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

## 14. trust (LHX-C-010) — PRIMITIVE ONLY

**Claims** (`src/kernels/trust/__init__.py:1-13`): trust scores, chains with
transitive propagation, revocation, "Evaluate trust for access decisions".

**What is real:** the mechanism exists (765 lines) and is correct-by-test.

**Integration: none.** Importers outside the package: `src/ai/governance.py`,
`src/ai/network_gateway.py`, `src/kernels/retention/__init__.py`, and
`src/observability/production.py:114` (liveness probe only). Both
`src/ai/governance.py` and `src/ai/network_gateway.py` are themselves orphaned
(see §18). **No gateway route, no execution path.** Trust never influences a real
user decision.

**Single most important gap:** wire trust into one real decision — otherwise
"evaluate trust for access decisions" describes a library call nobody makes.

---

## 15. event (LHX-C-008) — PRIMITIVE ONLY (a WS surface exists but is NOT mounted)

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

**Single most important gap:** mount an event surface (WS or a polling
`/v1/events`) in `src/gateway/main.py`, or delete `src/api/events_ws.py`. Today the
event bus is real plumbing with no visible outlet.

---

## 16. plugin (LHX-C-014) — STUB / PARTIAL ⚠ OVERCLAIMED

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

## 18. observability (cross-cutting, not a registered kernel) — STUB / PARTIAL

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

## 19. Cross-cutting finding: Tasks / Projects / Files / Apps DO NOT EXIST

**This is the single largest gap between the OS promise and the shipped product.**

### Backend: no routes at all

A grep across `src/gateway/` and `src/api/` for `v1/tasks`, `/tasks`, `/projects`,
`/files`, `/apps`, `workspace.py`, `PluginRegistry` returns **zero results**.

The complete mounted route inventory (`src/gateway/main.py:492-550`, plus the
decorators in each module) is:
`/v1/health`, `/v1/ready`, `/v1/metrics`, `/v1/metrics/prometheus`,
`/v1/ready/subsystems`, `/v1/production/preflight`, `/v1/knowledge/*`,
`/v1/chat*`, `/v1/dashboard/{summary,activity,analytics,roster}`, `/v1/profile*`,
`/v1/kernels*`, `/v1/goals*`, `/v1/employees*`, `/v1/workflows*`, `/v1/audit/*`,
`/v1/identity/*`, `/v1/policy/*`, `/v1/auth/*`.

**There is no `/v1/tasks`, `/v1/projects`, `/v1/files`, or `/v1/apps`.**
**There is no `workspace.py`.**

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
| `data` | 数据中心 | `DataCenter` (Operations:250) | live: `/v1/ready`, `/v1/metrics`, `/v1/policy/enforcement`, `/v1/auth/config` |
| `approval` | 审批中心 | `ApprovalCenter` | live: `/v1/policy/approvals` |
| `status` | 系统状态 | `SystemStatus` (Operations:31) | live: `/v1/dashboard/analytics?days=30`, `/v1/dashboard/summary` |
| `settings` | 系统设置 | `Settings` | **no network calls at all** |

> **Snapshot caveat.** The nav table above reflects commit `eeef0d4b` (9 keys).
> While this audit was running, another branch of work added a 10th key —
> `audit` → `Audit.tsx` (untracked at the time of writing), rendering
> `fetchAuditEvents` / `verifyAuditChain` / `fetchAuditSummary` from
> `lib/operator.ts`. That does not change any kernel verdict here (the audit
> kernel was already `REAL` on the strength of its routes alone), but a reader
> inspecting `lib/nav.ts` *today* will see one more entry than the table shows.
> It also does not create a Tasks/Projects/Files/Apps surface.

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

### #1 — The advertised product surfaces do not exist: Tasks, Projects, Files, Apps
There is no `/v1/tasks`, `/v1/projects`, `/v1/files`, or `/v1/apps` route anywhere in
`src/gateway/` or `src/api/`; there is no `workspace.py`; the console's nine nav keys
(`lib/nav.ts:34-51`) map to no such page. `/v1/workflows`
(`ai_management.py:1026`) is a read-only projection over goals, and the contract types
that would back a Workflows UI (`WorkflowsPayload`, `GoalDetail`, `GoalTraceEntry`)
have **zero** consumers outside `contracts.ts`.

*Why this is #1:* every other finding is about a mechanism being weak or unwired.
This one is about a **product that was never built**. An "AI OS" whose users cannot
create a task, open a project, touch a file, or install an app fails the plainest
reading of its own PRD. It is also the cheapest to state and the most expensive to
fake — and it cannot be closed by improving any kernel.

### #2 — The plugin kernel has no loader, so "Apps" has no mechanism
`src/kernels/plugin/__init__.py:11` promises `load_plugin(plugin_id) → PluginInterface`.
Neither exists: `load_plugin` is not defined in the kernel, and `PluginInterface` is
never defined anywhere — it appears only as `hasattr` duck-typing checks
(`:273`, `:302`). `plugins/registry_index.json:4` records `active_plugins: 0`, and
`src/plugins/registry.py:18` `PluginRegistry` has zero importers.

*Why #2:* it is the root cause beneath half of #1. Even if an Apps page were built,
there is no way to install or activate anything into it — a UI over a loader that
does not exist. It is also a **live false claim in a docstring** that a skeptical
reviewer will find in one grep, which is the kind of thing that collapses trust in
the whole audit. Ranked below #1 only because a missing loader is a smaller build
than four missing product surfaces.

### #3 — Observability cannot alert on system health, and nothing pages a human
All six metric-threshold rules are still `enabled=False`
(`alerts/__init__.py:64/82/100/118/136/154`) **and** `install_production_rules()`
(`:31`) has **zero callers** in `src/`, `tests/`, or `scripts/` — the rules are not
registered at all. All six metrics have zero emitters. The only path that can fire is
execution-failure (`execution_binding.py:214` → `main.py:271`), and its only
configured sink by default is `ConsoleLogSink` (`sinks.py:45`) — the real
`WebhookSink` (`sinks.py:60`) activates only when `LIUHAO_ALERT_WEBHOOK` is set
(`:33`, `:109`), which it is not by default. There is no `/v1/alerts` route
(`gateway/observability.py` has only 3, none alert-related) and no alerts UI.

*Why #3:* the first two are about what the product *offers*; this one is about
whether anyone would **know** it broke. Shipping a system where high CPU, memory,
error-rate, latency, heartbeat loss and audit-lag can never raise an alert — and
where the one path that can fire writes a log line — means the first production
incident is discovered by a user, not by the operator. That is an acceptance blocker
for any system that claims production readiness, and it is invisible until measured,
which is exactly why it ranks here rather than being dismissed as "just monitoring".

**Honourable mention (not in the top 3, but it will come up in acceptance):** the
planner is deterministic keyword matching —
`src/kernels/execution/__init__.py:267` `decompose`, with the kernel's own comment at
`:269` "in production would use LLM". Real LLM planning awaits an owner-supplied
provider key. **Do not fake it**; keep the honest deterministic fallback and record
the dependency.

---

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
