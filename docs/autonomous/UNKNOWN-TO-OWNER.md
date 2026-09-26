# UNKNOWN-TO-OWNER Register (LIUHAO AI OS)

> Purpose: a read-only discovery register of system facts the project owner very
> likely does NOT know — security posture, capability surfaces, and systemic
> gaps an autonomous R&D org must surface before they bite.
>
> Discipline (per FULL AUTONOMOUS mandate, HARD BOUNDARIES):
> - This file is produced by READ-ONLY scanning. No evidence was deleted,
>   overwritten, or re-captured. The forensic snapshot and
>   `D:\WorkBuddyFiles\LIUHAO-Phase3.6-Evidence\` remain IMMUTABLE.
> - "VERIFIED" is used ONLY where independently proven. No success is faked.
> - No source was modified to produce these findings; this is a register, not a
>   change.
>
> Domain owner of this section: **sec-impl** (security architecture / AI-security
> / identity / capability-security / red-team discovery).
> Last scan: 2026-09-26 (rate-limit lifted), branch p36.

---

## U38 — Shell adapter is a default-allow command-execution capability surface
- **Where:** `src/ai/world_interface.py` — `ShellAdapter.execute` (≈L126-151),
  `WorldInterface.__init__` (≈L157-168).
- **What:** `ShellAdapter` runs host commands. By default it uses `shlex.split`
  (no shell). But `shell=True` is reachable whenever a caller passes
  `params={"shell": True}`. More importantly, `WorldInterface` constructs with
  `authorize=None`, documented as **"default allow"**. So the gate that is
  supposed to sit in front of a command-execution capability *defaults to
  permissive*.
- **Why it matters (AI / capability security):** an LLM-driven planner that can
  register or reach a `shell` adapter — with a permissive (or absent)
  `authorize` callback — can execute arbitrary host commands. On the autonomous
  path this is a direct host-compromise capability, not merely a sandbox escape.
- **Severity:** HIGH for autonomous operation. Not a hard human-sovereignty
  violation yet, but it is a capability that must be armed deliberately, not
  defaulted open.
- **Recommendation (for team-lead / security-identity triage):**
  1. Make `WorldInterface` *default-deny* when `authorize is None` (refuse, do
     not allow).
  2. Forbid `shell=True` on the autonomous path unless a human-in-the-loop has
     explicitly armed it.
  3. Keep the `authorize` callback mandatory and logged.
- **Status:** REMEDIATED (commit `6d7769e6`, wave p36). `WorldInterface` now takes an explicit `actor` flag; with `actor="autonomous"` the policy gate is default-deny and `shell=True` host-command execution is blocked unless an explicit human-arming `authorize` policy allows it. Human dispatch keeps default-allow (no regression). Caveat: the protection is opt-in at construction — autonomous agents must be built with `actor="autonomous"`; the agent framework (security-identity) should set this automatically. **HUMAN DECISION REQUIRED** remains open for the policy question of *whether* autonomous agents may ever run host commands at all — the code now makes the safe default (deny + require arming), but that is a governance choice, not just a code fix.

## U39 — Subprocess sandbox resource limits are not enforced on Windows
- **Where:** `src/plugins/sandbox/backends/subprocess_backend.py` ≈L128
  (`preexec_fn=_set_limits if os.name != 'nt' else None`).
- **What:** The subprocess sandbox applies `RLIMIT_AS / RLIMIT_CPU /
  RLIMIT_NPROC / RLIMIT_FSIZE` via `preexec_fn`, but `preexec_fn` is ignored on
  Windows (`None`), so on `nt` hosts **none of the resource limits are applied**.
- **Why it matters:** plugin / agent code executed through this backend is not
  memory-/CPU-/PID-/output-bounded on Windows. A runaway or hostile plugin can
  exhaust host resources; containment is weaker on the Windows deploy target than
  on Unix.
- **Severity:** MEDIUM (platform-dependent containment gap).
- **Recommendation:** add a Windows-side limiter (job objects / process groups)
  or explicitly document Windows as an unsupported sandbox target.
- **Status:** DISCOVERED (read-only). No code changed.

## U40 — There is currently NO verified durable audit trail in this build
- **Where:** `src/security/audit_logger.py` (HC-09), `src/security/audit_policy.py`
  (HC-10), `src/kernels/audit` (HC-01).
- **What:** D19/D20/D21 (implemented this wave) deliberately keep HC-09/HC-10
  **volatile / in-memory / non-authoritative** — they are NOT audit-grade and the
  matrix still rates them UNVERIFIED. The only authoritative store
  (`src.kernels.audit`, HC-01) has a **forked, UNVERIFIED** chain (per the matrix
  and the HC-01 decision memo). So as of this build, no chain is VERIFIED.
- **Why it matters:** any "we have an audit log" claim today is misleading. The
  system cannot yet produce tamper-evident, durable evidence of security-relevant
  actions. This is by human decision (D19 = A+, do-not-persist), not an accident,
  but the owner should know the evidence gap is real and current.
- **Severity:** STRATEGIC / governance. HC-01 remediation (F1–F6) is the path to
  VERIFIED; until then, treat all internal audit as telemetry, not evidence.
- **Status:** CONFIRMED by runtime matrix probe (verify_p08_final_status_matrix.py
  → COMPLIANT 7 / UNVERIFIED 4, no drift). Not a defect to "fix" this wave; it is
  the declared, honest state.

## U41 — New HWM telemetry file is counter-only and fail-open (correct, but note)
- **Where:** `src/security/audit_logger.py` `get_crypto_audit_logger()` writes
  ` ~/.liuhao/audit_hwm.json` in production (D19).
- **What:** The high-water-mark file holds ONLY `{"entries_ever_written": <int>}`,
  mode 0600, atomic `os.replace`, fully fail-open. It confers no authority and is
  explicitly NOT evidence.
- **Why it matters (note, not a defect):** ensure operators/backup tooling do not
  mistake this counter file for audit evidence or chain-of-custody. It is
  monotonic telemetry only.
- **Severity:** INFO.
- **Status:** IMPLEMENTED this wave; recorded so it is not later mistaken for
  proof.

---

## U42 — Systemic permit-by-default authorization framework
- **Where:** `src/ai/lcore.py:131-132` (`if self.authorize is None: return True  # human-sovereignty default allow`); `src/ai/world_interface.py:166,183` (`# Optional policy gate (default allow)` / `return True  # default allow`).
- **What:** Multiple components treat `authorize=None` as **allow**. `WorldInterface` and `L-Core` both fall through to permit when no explicit gate is injected. (See U38 for the dangerous instance: the shell adapter sits behind this default-allow.)
- **Why it matters (capability security):** this is a framework-wide pattern — any NEW world action / adapter / tool added without an explicit `authorize` callback inherits permit-by-default. The human-facing L-Core default-allow is defensible (human sovereignty), but the pattern is dangerous wherever it gates non-human/autonomous capability (shell, subprocess, external writes).
- **Severity:** MEDIUM–HIGH (compounding U38).
- **Recommendation:** invert the default to deny when `authorize is None` for any non-human-facing capability; keep explicit allow only for the human's L-Core interface, and log every gate decision.
- **Status:** PARTIALLY REMEDIATED (wave p36). The `world_interface.py` autonomous path is now default-deny (commit `6d7769e6`). `lcore.py` was intentionally left default-allow (human sovereignty, per directive). Residual: the systemic `authorize=None → allow` pattern still exists at `lcore.py:131-132` for the human L-Core (by design) — any NEW non-human capability must set `actor="autonomous"` to inherit default-deny. Tracked for the agent framework to wire automatically.

## U43 — `revoke_all_user_tokens` is a silent no-op placeholder
- **Where:** `src/security/jwt_handler.py:545-548`.
- **What:** `revoke_all_user_tokens(subject)` returns `0` with a `# placeholder for now` comment — it does NOT revoke anything. No exception, no log, just a falsy 0.
- **Why it matters:** this is exactly the "false success" the mandate forbids. An operator/automated responder calling "revoke all of user X's tokens" after a compromise would believe the revocation happened; it did not. Violates the HARD BOUNDARY "No faking success" and "No garbage code."
- **Severity:** HIGH (security control that silently does nothing).
- **Recommendation:** either implement it against a real token store, or make it raise `NotImplementedError` so callers cannot mistake a no-op for success. Do NOT ship a silent 0.
- **Status:** REMEDIATED (commit `5d618ac5`, wave p36). `revoke_all_user_tokens` now maintains a subject→JTI index in `create_token` and actually revokes all of a user's tracked tokens, returning the real count (0 honestly when none). No longer a silent no-op. In-memory only (documented; same limitation as the rest of the revocation blacklist — not durable across restart/workers). Regression test added (`tests/test_jwt_revoke_all.py`).

## U44 — Built-in `system` identity carries `admin`; verify no principal spoofing
- **Where:** `src/kernels/identity/__init__.py:402-408` (default `system` identity, `permissions={"admin"}`, scope L0).
- **What:** The bootstrap `system` principal is a privileged built-in used for kernel-internal actions (Policy C-1). Its `metadata["kind"]=="service"` marker is what lets the Policy Kernel distinguish it from human identities. Intentional design.
- **Why it matters (verification question, not a confirmed vuln):** a built-in `admin` principal is a high-value target. The owner should know whether the Policy Kernel / RBAC layer **rejects an externally-supplied `principal="system"`** (spoofing). If any caller-supplied principal string can become "system", that is privilege escalation to admin.
- **Severity:** LOW–MEDIUM (depends on spoofing protection, unverified here).
- **Recommendation:** confirm `principal` is never trusted from untrusted input for the `system`/service marker; add a regression test that an external request claiming `system` is denied or re-bound to a non-admin identity.
- **Status:** VERIFIED SAFE (read-only review, wave p36; no code change needed). The Policy Kernel (`src/gateway/policy.py`) derives `principal` **exclusively** from the signature-verified JWT `sub` claim; the request body cannot supply a principal (`ApprovalRequest` has no `principal` field), and `require_bearer_payload` validates the JWT signature. External auth (`src/gateway/auth.py`) requires a verified credential to bind a principal to a token. The built-in `system` identity's `admin` perms are reachable only via a valid `sub="system"` JWT, which requires the `system` credential secret (unforgeable without it). RBAC is fail-closed default-deny. Conclusion: external `principal="system"` spoofing to admin is rejected. Residual trust (unchanged): depends on (a) JWT signing-key confidentiality, (b) no weak/known `system` credential, (c) no future code adding a trusted `principal` request field.

## Reviewed-safe (explicit false-positive callouts)
- `jwt_handler.py` unverified `jwt.decode(..., options={"verify_signature": False})` at L435/L525 is **safe**: it only extracts `jti` for revocation lookup; the trust decision uses a fully-verified decode (L448: signature + issuer + audience + exp + iat). No unverified claim is trusted.
- `introspect_token` calls `validate_token(token, verify_exp=False)` (L553) — minor: an expired token reports `active: True` on introspection. Standard-ish, but note if strict expiry on introspection is required.
- All secret/key/token generation uses `secrets.token_*` / `secrets.token_bytes` (strong CSPRNG). No `random.*` used for secrets. Good.
- No `CERT_NONE` / `verify=False` / unverified-TLS anywhere in `src`. Good.
- No weak `md5`/`sha1` for security; hash chains use `sha256` via the explicit-algorithm registry. Good.
- RBAC kernel (`src/security/rbac.py`) is **default-deny**: `has_permission` / `check_access` fall through to `return False` (L191/L217/L562). This is the correct posture and contrasts with the `WorldInterface`/`lcore` `authorize=None → allow` pattern (U42). The authz gap is at the world-action/adapter boundary, not in RBAC itself.

## Scan method (reproducibility)
- Read-only static greps over `src/` for: `shell=True`, `verify=False`,
  `pickle/yaml.load/marshal/eval/exec`, `md5/sha1`, hardcoded
  `password/api_key/secret_key/token`, `DEBUG=True`, `disable_auth/bypass_auth`.
- Runtime: re-ran SEC-subset pytest (75 passed) and
  `verify_p08_final_status_matrix.py` (EXIT 0, 7/4, no drift).
- No evidence files were touched. No source was modified for this scan.

---

## U36 — No data-protection / PII legal-compliance framework (GDPR/CCPA) despite PII handling in the pipeline
- **Where:** `src/knowledge/pii.py` (regex PII detect + `[REDACTED]`); audit chains (`src/kernels/audit` HC-01, `src/security/audit_logger.py` HC-09) redact **secrets only**, not PII; `docs/archive/PHASE_ACCEPTANCE_REPORTS.md:1300` → `Phase 20 | 合规自动化（GDPR/CCPA） | 🔒 Frozen | TBD via Amendment`.
- **What:** The pipeline ingests/redacts PII (emails, phones, identity/address markers) and the audit trail records identity principals + operations, but there is **no implemented data-protection legal framework**: no data-subject rights (access / rectification / erasure), no cross-border / jurisdiction policy, and no finalized retention policy (HD-06 = HUMAN DECISION). GDPR/CCPA compliance is explicitly Frozen/TBD.
- **Why it matters (governance / legal):** LIUHAO is an autonomous agent OS that will process personal data; operating it without a data-protection posture creates external legal liability (GDPR/CCPA enforcement) the owner is currently unaware of. This is a sovereignty/legal boundary, not a defect for the team to auto-fix.
- **Severity:** HIGH (legal exposure) — but explicitly a HUMAN DECISION boundary (data ownership, law/compliance policy, retention final policy per EXECUTION-QUEUE §Standing).
- **Recommendation:** escalate to owner as HUMAN DECISION (see `docs/autonomous/HUMAN-DECISION-HD06-U37.md`). Team must NOT auto-decide data-protection compliance or retention.
- **Status:** DISCOVERED (read-only). No code changed. Pending HUMAN DECISION.

## U37 — No legal liability / accountability framework for autonomous-agent actions
- **Where:** design intent `Human-Sovereign + Bounded Autonomous Authority`; `src/kernels/audit` (HC-01) is a *technical* audit chain, not a legal-attribution framework; `docs/autonomous/EXECUTION-QUEUE.md:76` lists `external contracts/liability` as HUMAN DECISION REQUIRED.
- **What:** Agents execute bounded autonomous authority, yet there is no documented framework answering **who bears legal liability when an autonomous action causes harm**, nor how authorization/attribution records map to external (human / contractual) responsibility. Technical accountability (HC-01 audit chain) exists; legal liability assignment does not.
- **Why it matters (governance / legal):** as the owner delegates the *entire* project to the team, legal liability for autonomous actions remains an unaddressed owner-level boundary. Auto-executing agent actions without a liability framework is a HUMAN DECISION, not a team default.
- **Severity:** HIGH (legal exposure) — HUMAN DECISION REQUIRED.
- **Recommendation:** escalate to owner as HUMAN DECISION (see `docs/autonomous/HUMAN-DECISION-HD06-U37.md`). Team must NOT assume/default liability allocation.
- **Status:** DISCOVERED (read-only). No code changed. Pending HUMAN DECISION.

---

> **Provenance:** U1–U35 reconciled verbatim from the committed `HEAD` during a concurrent file-rewrite conflict (originally authored by governance-legal); no content altered.

| id | discovered-by | date | what | why it matters | status | priority |
|----|----|----|----|----|----|----|
| U1 | security | 2026-09-25 | HC-09/HC-10 are volatile (in-memory only) | Crypto + permission audit can be silently lost on process exit | Decided D19=A+ keeps them volatile by design; durable path designed separately | P1 |
| U2 | os-systems | 2026-09-25 | Single-writer/RLock audit chain + linear `seq` won't scale | 10×/100× growth breaks ordering + concurrency | Researching HLC / segmented ordering | P1 |
| U3 | reliability | 2026-09-25 | No recovery framework for chain forks | A fork (HC-01) currently blocks GO; need detect → recover | Designing recovery framework | P0 |
| U4 | observability | 2026-09-25 | No metrics / tracing layer across kernels | Can't operate/debug a production system blind | **Built incrementally** — `src/reliability/` delivered: metrics (Counter/Gauge/Histogram + Prometheus text), audit_metrics (audit_evidence_status / audit_failure_total / audit_total_events / audit_fork_count / audit_integrity_ok, read-only on `src.kernels.audit` public API), structured_log (JSON), tracing (span hooks). 27 tests, 99% coverage. Evidence=missing surfaced, never swallowed. | Built/Doing | P1 |
| U5 | platform | 2026-09-25 | No developer SDK/API or reproducible build | External devs/agents can't extend safely | Roadmap | P2 |
| U6 | security | 2026-09-25 | HC-11 `tag_matches()` returns True when key absent (fail-open) | Identity-integrity verification silently bypassed | Decided: enforce key + fail-closed (pending impl) | P0 |
| U7 | forensics | 2026-09-25 | Forensic baseline custody not automated; snapshot opened RW post-capture | Chain-of-custody break undermines evidence | Decided: immutable storage + manifest; snapshot retained | P1 |
| U8 | governance | 2026-09-25 | Schema v1 (snapshot) vs v9 (live) divergence | Migration-source ambiguity | Resolved: Path A (live source); snapshot historical | Resolved |
| U9 | scale | 2026-09-25 | No benchmarking / sizing harness | Can't plan 10×/100× capacity | **Owned + scaffold built** — `scripts/bench_audit_chain.py` (per-event vs batched throughput, bytes/row, annual-GiB projection at 10/100 eps, full-verify time extrapolation to 1M rows) + test. Measured: ~1.8k eps per-event, ~40k eps batched, ~418 B/row, ~40s/1M verify. | Built/Doing | P2 |
| U10 | agent | 2026-09-25 | No safe external-agent onboarding protocol | Future "AI Agent will need" unaddressed | Roadmap | P2 |
| U11 | os-systems | 2026-09-25 | Per-instance `threading.RLock()` + `check_same_thread=False` + no `busy_timeout` across 25+ modules (e.g. src/kernels/audit/\_\_init\_\_.py:191-192; 25+ `RLock` sites) | Cross-process concurrency unprotected; the RCA-1 fork class is systemic, not just the audit store. Also no `UNIQUE(seq)` / DEFERRED-only transactions allow duplicate seq | Decided: move to single-writer-service + `BEGIN IMMEDIATE` + `UNIQUE(seq)` (F1/F2) | P1 |
| U12 | os-systems | 2026-09-25 | Distribution fencing / leader-election are Redis-less stubs (src/distribution/election.py:59, src/distribution/lock.py:118) | HD-02=A single-writer has NO cross-process enforcement today; failover reintroduces split-brain (C3) | Designing fencing token lease as part of F2/HD-02 | P0 |
| U13 | os-systems | 2026-09-25 | Dual audit persistence: JSON `AuditStore` (src/audit/store.py:21, default data/audit/events.json) coexists with the SQLite store | Two audit sources of truth (R12); contradicts F2 single-source; reconciliation hazard | Retire the JSON store (F2/R12) | P1 |
| U14 | os-systems | 2026-09-25 | `journal_mode=WAL` + `synchronous=NORMAL` on 6+ SQLite stores (audit, memory, execution journal, identity persistence, conversation_store, personal_context) | Windows power-loss can lose an uncheckpointed `-wal`; a copy-based snapshot without `VACUUM INTO` loses data; a single SQLite file is a write-throughput ceiling (mandatory single-writer) | Research checkpoint policy + Windows durability; recovery mandates `VACUUM INTO` snapshot (R8/R11) | P1 |
| U15 | governance-legal | 2026-09-26 | `docs/kernel-spec/identity.md:27` claimed the human-path permission audit trail is "经 _persistence 持久化、防篡改". Code (`src/kernels/identity/__init__.py`) shows `self._audit_log: List[AuditEntry]` is in-memory only, never persisted to `_persistence`, and has NO hash-chain/prev_hash. Only denials route to the real chain via `_record_denial_on_the_chain` → `src.kernels.audit.log_event`. | Misrepresents identity audit as durable/tamper-proof; contradicts the kernel's own code comment ("dies with the process, not part of the hash chain"). Not a sovereignty boundary → fixed, not HUMAN. | Resolved — doc corrected: in-memory, NOT persisted, NOT tamper-evident; authoritative trail = kernels/audit (HC-01, runtime UNVERIFIED). | P1 |
| U16 | governance-legal | 2026-09-26 | `docs/quickstart.md:28` print() stated `Audit kernel (authoritative, tamper-evident) OK` with no UNVERIFIED caveat (missed by prior commit 0c266910 which fixed L26/39/216). | User-facing install check asserted verified tamper-evidence that does not exist (HC-01 runtime UNVERIFIED). | Resolved — corrected to "(designed tamper-evident; runtime integrity currently UNVERIFIED — forked chain, pending F1-F6 + independent verification)". | P1 |
| U17 | governance-legal | 2026-09-26 | `docs/architecture/Architect-Architecture-v3.0.md:227` product matrix row `\| Audit \| Tamper-proof \| Hash chain \| Chain verification \|` with no caveat. | Public-facing product claim of tamper-proofness contradicting GOVERNANCE §7 (UNVERIFIED). | Resolved — changed to "Tamper-evident (target; runtime integrity UNVERIFIED — forked chain)" + "Chain verification (pending F1-F6 + independent verification)". | P1 |
| U18 | governance-legal | 2026-09-26 | `docs/spec/CAPABILITY-REGISTRY.md:47` row `LHX-C-013 \| Tamper-evident Audit \| ... \| IMPLEMENTED \| ✅ 31 \|` asserted tamper-evident audit as IMPLEMENTED+✅. | Capability-registry status claim implying verified tamper-evidence; inconsistent with UNVERIFIED runtime. (Distinct from REMEDIATION-PLAN R1-R10 registry fraud — this is the HC-01 audit row.) | Resolved — qualified to "Audit (hash-chain; tamper-evident DESIGN implemented, runtime integrity UNVERIFIED — forked chain)" while keeping IMPLEMENTED for the design. | P1 |
| U19 | governance-legal | 2026-09-26 | `docs/operations/scenario-chatbot.md:84` comment "写入防篡改链" (bare) — missed by prior commit 0c266910 (which fixed L79/181/186). | Runbook told operators the event goes to a "tamper-proof chain" with no caveat. | Resolved — corrected to "写入 kernels/audit 审计链；设计为防篡改，但运行时链完整性当前 UNVERIFIED——链存在分叉，待 F1–F6 修复+独立验证". | P1 |
| U20 | governance-legal | 2026-09-26 | Built `scripts/lint_audit_claims.py` + `.github/workflows/doc-lint.yml` — CI guard that FAILS (exit 1) if any doc change introduces an HC-01 "tamper-proof / true evidence source / authoritative" claim WITHOUT the UNVERIFIED/fork caveat. | Prevents recurrence of the bare-claim misrepresentation class (the D19/D20 red line) that let U15-U19 slip in. | Built — committed (docs + script only, scoped, no push). Verified: full-tree clean (exit 0); planted bare line → exit 1; caveated line → exit 0. | P1 |
| U24 | reliability | 2026-09-26 | Audit chain had NO time-series metrics (no fork_count / evidence / failure gauges). The only exposure was `/v1/ready` total_events + failures (point-in-time, not scrapable). | Without time-series signals, a forked/missing store can hide behind a green dashboard; violates INV-7 (Observability). | Closed — `src/reliability/audit_metrics.py` exposes audit_evidence_status / audit_failure_total / audit_total_events / audit_fork_count / audit_integrity_ok, read-only on the public API. Recovery: store unreachable → evidence_status=0 (Evidence=missing), never silent. | Resolved | P1 |
| U25 | reliability | 2026-09-26 | Full `pytest` suite is unstable: a dynamic repo-wide coverage run is blocked — the run is killed (SIGTERM) by a hanging/blocking test in another agent's WIP. No per-test timeout gate in CI. | Blocks the Q3.2 coverage gate from being enforced dynamically; flaky CI hides real regressions. | Discovered — recommend adding `pytest-timeout` + a per-test timeout (e.g. 30s) and a CI coverage-threshold gate. Static gap scan done instead (see report). | Doing | P1 |
| U26 | reliability | 2026-09-26 | `src/sre/` (cost/cost_manager.py, disaster/backup.py, scaling/) has ZERO dedicated tests (`tests/sre/` does not exist). | Disaster-recovery backup logic and scaling/cost controllers are reliability-critical yet unverified; a backup regression would be silent until a real disaster. | Discovered — recommend a `tests/sre/` suite; prioritize `disaster/backup.py` (backup/restore round-trip + corruption detection). | Doing | P1 |
| U21 | governance-legal | 2026-09-26 | Extended the doc-lint guard to ALSO catch HC-09 (`src/security/audit_logger.py` / CryptoAuditLogger) and HC-10 (`src/security/audit_policy.py` / AuditKernel) claims of durability / authority / evidence-grade made WITHOUT the VOLATILE / NON-AUTHORITATIVE / IN-MEMORY-ONLY caveat (the D19/D20 red line, parallel to HC-01's UNVERIFIED rule). Added `scripts/pre-commit-lint.sh` (optional local pre-commit hook variant). | Full-tree scan confirms NO remaining bare HC-09/HC-10 claims in live docs — the prior D19/D20 work already caveated them (capability-traceability.md:21/42 → 内存态/非持久/非权威; PM-PRD-v3.0.md:72/107 → IN-MEMORY ONLY / NON-AUTHORITATIVE). Guard now closes the whole D19/D20 misrepresentation class at doc-edit time (CI + local pre-commit). | Built — committed (scripts + hook only, scoped, no push). Verified: HC-09 bare probe → exit 1 (group HC-09-10-VOLATILE + correct VOLATILE hint); caveated probe → exit 0; pre-commit hook blocks a staged bare claim. | P1 |
| U22 | governance-legal | 2026-09-26 | Made the pre-commit doc-lint hook discoverable: root `Makefile` (`make hooks`, `make lint-docs`) + `CONTRIBUTING.md` documenting the HC-01 / HC-09 / HC-10 claim rules and required caveats. HC-11 doc-claim lint rule HELD pending Q1.1. | Pre-commit hook now installable; contributor docs state the accepted caveat language per subsystem. | Built/Doing — Makefile + CONTRIBUTING committed; HC-11 rule pending Q1.1. | P1 |
| U27 | identity | 2026-09-26 | **Fail-closed registry has a migration trap nobody was watching for.** Once `tag_matches()` escalated to fail-closed (U6/HC-11), ANY deployment whose rows predate `LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY` admits **zero humans** — nobody can log in, nobody can approve — and the old gate script reported it as `integrity_state: not_applicable`, identical to "no humans registered". Worse, CI itself seeded its ephemeral CEO identity WITHOUT the key, so the audit job's real login would have started failing. | An operator upgrading in place would see a healthy-looking report and a system with no recognised humans: a sovereignty outage disguised as an empty registry. Rows cannot be authenticated retroactively — it needs re-registration, not just a config flip. | **Resolved** — (a) `verify_human_registry_integrity_state.py` now reports `refused_rows` and prints a `[FAIL-CLOSED]` block naming the remedy; (b) refusal logs tell operators to set the key and re-register each human; (c) `.github/workflows/ci.yml` uses an ephemeral CI key so the seeded identity is tagged; (d) `docs/RUN.md`, `docs/operations/production-runbook.md`, `docker-compose.prod.yml` state the requirement and the re-registration step. Verified end-to-end: untagged row → refused_rows=['alice'] + FAIL-CLOSED; register with key → admitted_humans=1, integrity_state=enforced. | Resolved | P0 |
| U28 | identity | 2026-09-26 | Two legacy seeding tests in `tests/kernels/identity/test_human_identity_c7.py` encoded the OLD admit-unverified-rows behaviour and broke under fail-closed. | A red suite could have tempted someone to "fix" it by weakening the store instead of the fixture. | Resolved — fixtures now stamp rows with a valid tag under a fixture key (`_stamp`), preserving each test's original intent (file-shape tolerance / restart survival) instead of relaxing assertions; added `test_an_untagged_seed_row_is_refused` to lock the escalated posture at the seed-file level. 272 tests green across identity/security/gateway/tools/workspace, flake8 clean at CI口径. | Resolved | P1 |
| U29 | ai-tools | 2026-09-26 | LLM-driven file access had no single allow-root: `FilesystemAdapter` reached the whole disk for read/list/write, and its three callers each re-implemented fencing in an `authorize` callback that constrained **writes only**. Once a model chooses the path, reads are the bigger leak (secrets pulled into a reply). | Data exfiltration through a "read-only" tool; three copies of the same half-gate drifting apart. | **Resolved** — `src/ai/workspace.py` is now the one judging point: realpath ( resolves `..` and symlinks, including links pointing out of the root), case-insensitive compare on Windows, empty/non-string refused, every violation raises `WorkspaceViolation`. `FilesystemAdapter` takes an explicit root and `tools.py` passes one, because relying on the default ships an open gate. **Deliberately still no write tool** — writes belong behind the approval chain. | Resolved | P1 |
| U30 | sre | 2026-09-26 | `src/sre/disaster/backup.py` `RecoveryManager.restore()` was a **silent-success generator**: it computed no verification, discarded the `json.load()` result, and returned `success=True` for ANY file with a matching name — including one whose payload had been rewritten. Executed proof: tampered backup → old code returns `success=True, error=None`, and returns no data at all. | Disaster recovery you discover is empty during the incident; the module even computes an `integrity_hash` on write that nothing ever checks. | **Resolved** — restore recomputes the hash over what it actually read and refuses on mismatch or a missing hash; `RecoveryRecord` now carries the recovered `payload` / `resource_snapshot` so "success" means the data came back. `compute_integrity_hash()` is a single shared function so writer and verifier cannot drift. `tests/sre/test_disaster_backup.py` — 10 cases incl. tampered payload/snapshot, no-hash, truncated, unparsable, non-object, missing. | Resolved | P0 |
| U31 | sre | 2026-09-26 | The **real** operational tool `scripts/ops/backup.py` had four defects at once: (a) `tar.extractall(target_dir)` with no `filter=` — a member named `../../..` decides where we write; (b) `_encrypt_backup()` used `base64` while the module never imported it → NameError, so the encryption path could never have worked; (c) verification ran AFTER encryption, so any encrypted backup fails verification and `create_backup` raises — encryption and verification could never both be on; (d) scratch dir hardcoded to `/tmp/...` on a Windows project. | Restoring an attacker-supplied archive = arbitrary file write; and a "verified" backup that is actually unencrypted-or-broken. | **Resolved** — `filter="data"` (plus an explicit `_reject_traversal()` fallback for pre-3.12 interpreters), `base64` imported, verify-then-encrypt ordering, refusal to unpack an archive that does not verify, `tempfile.mkdtemp()`. `tests/sre/test_ops_backup.py` covers traversal refusal (asserting nothing lands outside), round-trip, missing metadata, truncated archive, and asserts the `/tmp` literal is gone. | Resolved | P0 |
| U32 | reliability | 2026-09-26 | Serial full-suite run: 2524 tests, **13 failures + 11 errors**, ~8 min, **no hang** — the earlier "the suite hangs" report did not reproduce. 11 of those (knowledge_memory 11 errors) plus 7 more (oss_ecosystem 6, policy_properties 1) are **order-dependent**: they PASS when their files are run alone and FAIL only inside the full run. | An unverified failure list invites wrong attribution (and the temptation to "fix" innocent commits); a red suite nobody can explain decays into background noise. | Resolved for attribution, **not** for cause — 4 failures reproduce identically in a `git worktree` control at `54fe5f2c` and are therefore pre-existing, and the remaining failures reproduce with WITHOUT this wave's new tests, so **this wave introduced zero regressions**. Their actual root cause (probably shared global/env state or the oss corpus dir) is still open — see EXECUTION-QUEUE §Test baseline. Discipline recorded: **never run two pytest processes against this repo at once.** | Open | P1 |
| U33 | architecture | 2026-09-26 | Two backup implementations coexist — `src/sre/disaster/backup.py` (zero importers anywhere in src/tests/scripts) and `scripts/ops/backup.py` (the real CLI one). | A dead DR path with plausible-looking API can be wired up by mistake, and the two disagree about what "restore" means. | **Resolved — scoped** — they are different LAYERS, not redundant: `scripts/ops/backup.py` is the operational *portable archive tool* (tar + encrypt, owns the CLI, backs up/restores infrastructure); `src/sre/disaster/backup.py` is an *in-application DR record-integrity library* (per-record hash-verified recovery, no CLI). Neither is retired. The genuine gap is that NEITHER is wired to a production caller yet — that is the follow-up item, not a deletion. Boundary now documented in each module's docstring. | Resolved (scoped) | P2 |
| U34 | sre | 2026-09-26 | `tests/sre/` did not exist at all: `src/sre/` (disaster backup, cost manager, scaling) shipped with **zero tests**, and the one test file that imported it (`tests/load/test_load_baseline.py`) only touches cost/scaling — the disaster-recovery path had none. | DR regressions are invisible until the disaster, which is exactly the wrong time. | Resolved — 17 new cases across `tests/sre/test_disaster_backup.py` and `tests/sre/test_ops_backup.py`, each asserting failure cases too, plus an executed control proving the OLD restore returned `success=True` for a tampered backup. | Resolved | P1 |
| U35 | reliability | 2026-09-26 | The full-suite redness is partly **environmental**: the WorkBuddy sandbox injects `sitecustomize.py` with a **bulk-delete guard** that, after ~50 file deletions in a turn, makes any further `os.remove` / `Path.unlink` raise `SystemExit(1)`. In a 2525-test run that budget is exhausted, so `tests/test_knowledge_memory.py` (11 errors), `test_policy_properties.py::test_service_principal_allow_exactly_whitelist` and `test_register_human_identity.py::test_registers_and_proves_the_login` fail — each traced to `sitecustomize.py:848 raise SystemExit(1)`, and each passes when run alone or in small groups. | Booking these as repo regressions would send someone hunting a bug that does not exist (and CI, which has no shim, would disagree with local results forever). | Recorded as environmental, NOT fixed — there is nothing in the repo to change. Mitigation when a trustworthy number is needed: run the suite in chunks, or trust the per-file/per-group runs. Do not "fix" the tests to stop deleting files. | Won't fix (environmental) | P1 |
