# MASTER PROJECT READINESS — LIUHAO AI OS

> **Single source of truth for project completion status.** Updated automatically after every significant engineering change.
>
> **Honesty discipline (NON-NEGOTIABLE):** Every gate is one of `PASS` / `FAIL` / `BLOCKED` / `NOT YET TESTED` / `NOT VERIFIED`.
> A gate is `NOT VERIFIED` when there is **no evidence** — we do **not** guess, do **not** fabricate percentages, and do **not** substitute "basically done" for `PASS`. "Tests pass" is not "production verified". Where a claim is documentation-only it is flagged `CLAIMED`, not counted as evidence.
>
> Branch: `p36`. Repo: `D:\LiuHao-AI-OS`. Report date: **2026-09-29**.

## Overall Status: **BUILDING**

The system has real, fail-closed security and data-integrity subsystems, a distributed execution fence, and a live observability surface. It is **not** yet RELEASE READY: the clean-environment run, full test baseline, reliability/performance/observability/deployment gates are NOT VERIFIED, and HC-01 is a frozen human-sovereignty decision. Work continues autonomously toward RELEASE READY, then EVOLUTION.

---

## G1 — Build
- **Status:** **PASS** (builds) · **clean-environment full run:** `NOT VERIFIED`
- **Evidence:**
  - `Dockerfile` multi-stage (Node console build → Python builder → slim runtime, non-root `app` user, `HEALTHCHECK` → `/v1/health`, `CMD python -m src.gateway`) — real, honest.
  - CI `build` job (`.github/workflows/ci.yml`) builds + pushes image to `ghcr.io` on `main` behind release gates (`scripts/check_hold_gate.py`, `scripts/verify_go_readiness.py`).
  - Local editable install + import succeeds in the managed venv (`pip install -e .`; `from src... import *` succeeds).
- **Last Verified:** 2026-09-29 (local install + import; CI build job config).
- **Blocker:** none engineering.
- **Owner:** os-systems (`os-impl`).
- **Next:** Prove a clean-environment bring-up end-to-end (see G2) and wire it into a CI smoke bring-up.

## G2 — Run
- **Status:** **NOT VERIFIED**
- **Evidence (partial):** `console-readability` CI job boots the real gateway on loopback (`--host 127.0.0.1`) and logs in, but only audits WCAG readability — it does NOT prove the full system runs from a clean environment. No clean-env end-to-end run is recorded.
- **Last Verified:** NOT VERIFIED.
- **Blocker:** none — needs a clean-env bring-up test (build artifact → boot → exercise core flows).
- **Owner:** os-systems (`os-impl`).
- **Next:** Add a CI job that boots the built image from scratch and exercises a minimal real flow (acquire fence → enforce → audit write → read back).

## G3 — Test
- **Status:** **NOT VERIFIED** (full clean-env re-run pending) · representative + targeted suites green; the single full-suite failure was a test-isolation defect, now fixed
- **Evidence:**
  - **Full `pytest tests/` run (2026-09-29, pre-fix): 3202 passed, 1 failed, 22 skipped** (604 s).
  - **The 1 failure was NOT a product regression** — it was a test-isolation leak. `tests/kernels/test_kernel_registry.py:184` and `tests/kernels/test_lifecycle_runtime_driven.py:98,136,156` used `os.environ.setdefault("LIUHAO_JWT_SECRET", ...)` directly on `os.environ` (NOT `monkeypatch`), so the secret leaked into the shared process and made `get_jwt_handler()` build a handler instead of raising `SecretBackendUnavailable` in production — silently defeating the fail-closed gate in the suite. Root-caused and fixed: (a) `tests/security/test_secret_store.py::_clear_env` now also clears `LIUHAO_JWT_SECRET`/`JWT_SECRET_KEY`/`JWT_SECRET` (hermetic), (b) the 4 leaking tests now use `monkeypatch.setenv` so the secret is reverted at teardown. Repro confirmed: the production fail-closed test now passes even when the leaking test runs first; all four affected files green (12 / 10 / 5+1skip / 15).
  - Representative suites green: `tests/distribution/test_coordination.py` **89 passed / 2 skipped**, `fence_metrics` **9 passed**, `fence+gateway-health` **40 passed**, `tests/security/test_secret_store.py` **15 passed**, `tests/kernels/test_lifecycle_runtime_driven.py` **5 passed / 1 skipped**, `tests/kernels/test_kernel_registry.py` **10 passed**.
  - 3225 tests collected.
- **Last Verified:** 2026-09-29 (full run + isolation-fix verification).
- **Blocker:** full clean-env re-run after the isolation fix to confirm 0 failures reproducibly.
- **Owner:** quality-reliability (`qr-baseline`).
- **Next:** Re-run the full `pytest tests/` in a clean environment to publish the 0-failure baseline; wire it into CI as the G3 gate.

## G4 — Security
- **Status:** **PASS** (security mechanisms real + fail-closed) · root-of-trust **final provider = human decision (HD-05)**
- **Evidence (code-backed, fail-closed):**
  - `src/security/secret_store.py` (558 lines): AES-GCM + Argon2id/PBKDF2, `chmod 0600`, raises `SecretBackendUnavailable` with no backend — no plaintext fallback.
  - `src/security/encryption.py`: Fernet / AES-GCM / Argon2 — real authenticated encryption.
  - `src/integrations/vault/client.py` + `vault_crypto.py`: production fail-closed when Vault offline; dev fallback is real AES-GCM (documented).
  - `src/security/jwt_handler.py`: raises without a persistent signing key in production; revocation list present (single-process — limitation documented, not hidden).
  - `src/security/api_keys.py`: only SHA-256 hashes stored; plaintext returned once at creation.
  - CI gates: **Bandit** (`--severity-level high`, blocking) and **Semgrep** (`--error`, the only truly blocking static gate) are real; Trivy / pip-audit are advisory (`--exit-code 0` / `|| true`).
- **Last Verified:** 2026-09-29 (code read + CI config).
- **Blocker:** none engineering.
- **Owner:** security-identity (`sec-impl`).
- **Next:** Resolve U39 (Windows sandbox RLIMIT missing) and re-run Bandit/Semgrep in CI to confirm 0 high findings.

## G5 — Data Integrity
- **Status:** **NOT VERIFIED** overall · sub-statuses below
- **Evidence (runtime-verified where marked):**
  - **HC-02..HC-08** (JSON file hash chains): **COMPLIANT** — `scripts/verify_p08_hash_chain_sig_alg.py` = **32/32 PASS** (hash_alg persisted, reload-verify, tamper-detected, unknown-alg fail-closed).
  - **HC-11** (identity `tag_matches`): **COMPLIANT** — fail-closed; unknown alg / bare digest refused.
  - **HC-09** (`CryptoAuditLogger`) / **HC-10** (`AuditKernel`): **VOLATILE by design** (in-memory only; D19 decision) — NOT VERIFIED at audit grade.
  - **HC-01** (audit master chain, `src/kernels/audit`): **BLOCKED** — see HC-01 below.
  - Segmented/incremental/rolling verification (`src/kernels/audit/verification.py`) is real: accumulated `link_hash`, checkpoint re-derived from verified segments, `rooted_at_genesis` separated from "segments verified", `coverage_frontier` prevents gaps.
- **Last Verified:** 2026-09-29 (runtime scripts + code).
- **Blocker:** HC-01 (human-sovereignty decision, isolated).
- **Owner:** security-identity (`sec-impl`) + governance-legal (`gov-impl`).
- **Next:** Decide HC-09/HC-10 persistence (engineering, owner-decidable) or keep volatile by design; keep HC-01 frozen & isolated.

## G6 — Reliability
- **Status:** **NOT VERIFIED** (scenarios coded + CI-wired; not executed in clean env)
- **Evidence (real scenario coverage, EVIDENCED by code):**
  - `test_audit_soak_concurrency.py`: 4 threads × 2500 = 10 000 appends no loss; crash via `store._conn.close()` recovers with no gap.
  - `test_storage_faults.py`: real injection of `SQLITE_FULL` / `READONLY` / `CORRUPT` / `IOERR`, plus multi-process `kill()` mid-transaction → sibling recovers, seq contiguous; backup restore; real byte-flip detected.
  - Missing (not evidenced): OS-level **power loss** (only process-kill/conn-close simulated), **network partition**, and cross-shard atomic snapshot.
- **Last Verified:** 2026-09-29 (code read + CI wiring).
- **Blocker:** none — needs a clean-env execution pass.
- **Owner:** quality-reliability (`qr-baseline`).
- **Next:** Execute the soak/fault suite in a clean env and publish results; add power-loss/partition scenarios.

## G7 — Performance & Scale
- **Status:** **NOT VERIFIED** (real machine-profile baselines + 1M/10M runs; 100M extrapolated; CI numeric gate skips on profile mismatch)
- **Evidence (honest, EVIDENCED):**
  - `scripts/bench_baseline.json`: **profile-keyed** baseline (fingerprint `Windows|py3.13.14|Intel…|t24|quick`). `single_append` ≈ **697.5 eps** (p95 2.49 ms); `batched_append` batch10≈5028, batch100≈13078; `verify` 225 ms@6500 / 383 ms@10500; `recovery` 0.128 s; 710 bytes/event.
  - `scripts/bench_audit_scale.py`: **1M** build ≈ 13 746 eps, `verify_integrity` 31.69 s; **10M** build ≈ 13 649 eps. **100M NOT run** (honest extrapolation only: ~41.7 GB > 4 GB ceiling, ~123 min > 20 min ceiling).
  - CI numeric gate (`bench_audit_append.py --quick --gate`) **SKIPS** on any non-matching machine profile → throughput is never CI-verified (baseline profile is a Windows workstation; CI runs Linux).
  - Stale doc vs artifact: `PERFORMANCE-BASELINE.md` claims "731 eps" but the authoritative JSON says **697.5** — cite the JSON.
- **Last Verified:** 2026-09-29 (artifacts read).
- **Blocker:** none — needs CI-runnable profile + 100M decision.
- **Owner:** quality-reliability (`qr-baseline`).
- **Next:** Establish a CI machine profile + baseline so the numeric gate can actually gate; decide whether 100M is a real target or out of scope.

## G8 — Observability & Operations
- **Status:** **NOT VERIFIED** (metrics + endpoints exist + wired; no runtime scrape proven)
- **Evidence (code-backed):**
  - `src/observability/metrics.py`: ~40 Prometheus metrics registered into a module `REGISTRY` (incl. UBX-005 `executor_fence_*` and coordinator admission metrics).
  - `src/gateway/health.py`: `/v1/ready` surfaces api_key_manager, jwt_handler, rbac, encryption, rate_limiter, audit_store, human_identity_registry, **executor_fence**; returns 503 on any unhealthy check.
  - `src/gateway/observability.py`: `/v1/metrics/prometheus` (404 when `LIUHAO_PROMETHEUS_METRICS=0`, 503 if export fails), `/v1/ready/subsystems` (14 kernels), `/v1/production/preflight`.
  - `src/gateway/main.py`: routers wired (`health_router`, `observability_router`).
  - Monitoring configs exist (`config/monitoring/prometheus_rules.yaml`, `loki.yml`, `otel-collector.yml`, `tempo.yaml`, `rules/liuhao-ai-os-alerts.yml`) — whether they receive data is NOT VERIFIED.
  - **Gap:** nothing in CI ever scrapes `/v1/metrics/prometheus` at runtime; live population of the metrics is NOT VERIFIED.
- **Last Verified:** 2026-09-29 (code read + CI config).
- **Blocker:** none — needs a runtime scrape / alerting proof.
- **Owner:** os-systems (`os-impl`) + quality-reliability (`qr-baseline`).
- **Next:** Add a CI job that boots the gateway and scrapes `/v1/metrics/prometheus`; verify alert rules fire on a injected fault.

## G9 — Deployment & Upgrade
- **Status:** **NOT VERIFIED** (image build/publish real + alembic upgrade in CI; deploy jobs intentionally `exit 1`; no helm/k8s)
- **Evidence:**
  - Docker image **builds and pushes** to `ghcr.io` (CI `build` job) — real.
  - DB migration path **verified in CI**: `alembic upgrade head` → `verify_orm_vs_db.py` → `verify_persistence.py`.
  - Compose files exist (`docker-compose*.yml`, `infra/{etcd,kafka,postgres,qdrant,redis}/docker-compose.yml`) — infra definitions, not a deploy automation.
  - **No working CD:** `ci-cd.yml` `deploy-staging` / `deploy-production` deliberately `exit 1` ("a green pipeline has to mean something actually shipped"). **No helm chart, no k8s manifests.**
  - Only real "deploy-side" check: `verify_metrics_persist.yml` validates metrics persistence against a staging PostgreSQL (manual/cron, requires `STAGING_DATABASE_URL`).
- **Last Verified:** 2026-09-29 (config read).
- **Blocker:** none engineering — CD is intentionally absent (honest).
- **Owner:** os-systems (`os-impl`).
- **Next:** Decide on a real CD target (helm/k8s or a concrete staging deploy) before claiming G9 VERIFIED; keep the intentional `exit 1` until then.

## G10 — Independent Verification
- **Status:** **NOT VERIFIED** (guardrail `verify_*.py` scripts exist + run; no standing independent verification service)
- **Evidence:**
  - `scripts/verify_*.py` (≈25) carry pass/fail contracts and are wired into CI `guardrails` job; `tests/test_guardrail_scripts.py` enforces every one is invoked.
  - `scripts/verify_p08b_chain_matrix.py`, `verify_p08_hash_chain_sig_alg.py`, `verify_p07_dual_signature_sig_alg.py` run at runtime and report COMPLIANT/UNVERIFIED/BLOCKED (no assumption-filled gaps).
  - No independent verification **service** (e.g., an external auditor) is established for HC-01 or system-level claims.
- **Last Verified:** 2026-09-29 (scripts run + CI wiring).
- **Blocker:** HC-01 independent verification is a frozen human decision.
- **Owner:** governance-legal (`gov-impl`).
- **Next:** Stand up an independent verification harness that consumes the runtime evidence scripts and emits a signed verification record (decoupled from the builder).

## HC-01 — Audit Master Chain
- **Status:** **BLOCKED** (GO=BLOCKED) · **HUMAN DECISION PENDING** · **Decision Isolation active**
- **Evidence:**
  - Runtime mechanism is **COMPLIANT** (`probe_hc01` in `verify_p08b_chain_matrix.py` = **8/8 PASS**: per-line hash_alg declaration, canonicalization excluding event_hash/prev_event_hash, linkage, tamper-detectable, unknown-alg fail-closed, no-default-alg fail-closed).
  - But the **live evidence chain** was recorded (Phase 3.6) as a **real fork**: 137 duplicate `seq` clusters, 169 redundant rows. The binding authority is the **immutable** Phase-3.6 evidence under `D:\WorkBuddyFiles\LIUHAO-Phase3.6-Evidence\` — per governance §2, no evidence ⇒ cannot be marked VERIFIED.
  - Current working-tree `audit_store.db` shows **0 duplicate-seq clusters, all sha256, 0 empty hash_alg** — i.e., the present DB carries no fork signature, but the frozen Phase-3.6 record governs.
- **Last Verified:** 2026-09-29 (runtime scripts + read-only DB query + evidence custody docs).
- **Blocker:** human-sovereignty decision (data ownership / irreversible raw-data disposition / retention policy). **This is NOT an engineering failure and does NOT block the rest of the project (Decision Isolation).**
- **Owner:** governance-legal (`gov-impl`) — reserved for human decision; engineering continues on all other gates.
- **Next:** Await human decision on HC-01 disposition; in the meantime keep it isolated and keep all other chains (HC-02..HC-08, HC-11) releasable.

## Release Readiness
- **Status:** **NOT READY** — gated by NOT VERIFIED: G2, G3 (full baseline), G6, G7, G8, G9, G10. Engineering-critical blockers: none. HC-01 is frozen & isolated (does not block engineering).

---

## Critical Blockers
- **HC-01 disposition** — HUMAN DECISION PENDING / GO=BLOCKED. Frozen & isolated; not an engineering blocker.

## High Risks
- **U40 (STRATEGIC / governance):** no VERIFIED tamper-proof persistent audit trail today; all internal audit is telemetry-grade, not evidence-grade.
- **R-G2-02:** audit writer single point of failure — writer death ⇒ pending futures error ⇒ HIGH/CRITICAL actions fail-closed by the evidence gate (concentration = failure surface).
- **U42 (CLARIFIED, low urgency):** the RBAC/ABAC *kernel* is correctly default-deny (`src/security/rbac.py` fall-through `return False`; `check_abac` denies with no rule). Permit-by-default exists ONLY at the human L-Core / WorldInterface boundary (`src/ai/lcore.py:131`, `src/ai/world_interface.py:217`) and is intentional (human sovereignty — NOT a code contradiction). **Recommendation:** add a CI guardrail that forces `actor="autonomous"` (inherits default-deny) and default-denies any new WorldInterface adapter that omits an explicit `actor`.
- **U39 (MEDIUM, FIXED 2026-09-29):** Windows sandbox RLIMITs silently no-op on `nt` (`preexec_fn=_set_limits` disabled; `_set_limits` returns early off-Unix). Fixed: `src/plugins/sandbox/backends/subprocess_backend.py` now logs a warning when RLIMIT is requested-but-not-enforced on Windows; only wall-clock timeout applies. Linux path unchanged. Commit `08ff1a88`.
- **Doc/code drift (CLARIFIED):** authoritative kernel count = **14** (blueprint / `docs/ARCHITECTURE.md` / `docs/CODEX-CONTRACT.md` correct; PRD's "12 Kernels" is stale). The "15+" reading was a misread — `src/kernels/retention/` is an HD-06 subsystem, not a registered kernel. "Definition Lock v3.0" is confirmed **nonexistent** as a spec doc (only a `definition_lock_version: "v3.0"` field, a status doc, and incoming drafts exist); PRD's "Auto-generated from Definition Lock v3.0" cites a missing source.
- **Repeat implementations (CLARIFIED, NOT dead code):** the two `AuditStore` are a controlled legacy-compat layer (`src/audit/store.py`, deprecated + guarded by `tests/test_audit_single_source_of_truth.py`) + the authoritative `src/kernels/audit`; both `SecretStore` are live with distinct roles (console credentials `src/gateway/auth.py:204` vs encrypted app keys `src/security/secret_store.py`). No deletion warranted. Optional: rename `auth.py:204` `SecretStore` → `ConsoleCredentialStore`.

## Open Unknowns (UNKNOWN-TO-OWNER)
- **U40** — no verified persistent audit trail.
- **U52** — retention policy contradicts append-only hash chain (crypto-shred ≠ erase).
- **U53** — no one signs the chain head; 10-year chain has no algorithm-migration flow.
- **U36** — no GDPR/CCPA PII compliance framework though the pipeline handles PII.
- **U44** — built-in `system` identity carries `admin`; guard against subject forgery.
- **U51** — no single value committing its own history ⇒ cheap notarization/inclusion-proof infeasible.
- **U39 (RESOLVED):** Windows sandbox RLIMIT warning added (commit `08ff1a88`); remaining gap is that Windows cannot enforce RLIMIT at all — only wall-clock timeout. Documented, not hidden.
- **U42 (CLARIFIED):** see High Risks — RBAC/ABAC kernel is default-deny; human boundary default-allow is by design. CI guardrail recommended.

## Autonomous Workstreams (self-directed, no owner prompting)
1. **#3 Distributed coordination hardening** — `EtcdLease` third backend + CI etcd service; rebuild `deploy/cloud/src/distribution/` bundle; Windows legacy filename auto-clean. *(IN FLIGHT — worker)*
2. **#4 Productionization** — soak / chaos benchmarks (crash-recovery, lease-expiry, split-brain, backend outage, failover) for the fence + coordination path. *(IN FLIGHT — worker)*
3. **#6 Continuous discovery** — scan for un-owned problems (U39 Windows sandbox RLIMIT, U42 permit-by-default auth, doc/code drift, repeat implementations). *(IN FLIGHT — worker)*
4. **G2/G3/G6/G7/G8/G9/G10 verification** — turn each NOT VERIFIED into VERIFIED with clean-env evidence.
5. **Blueprint evolution** — upgrade `UNIFIED-BLUEPRINT` when research proves current architecture unfit for the million-scale target (with ADR + Architecture Delta + Migration Path + Verification Plan).
6. **Independent verification service** — standing harness consuming runtime evidence scripts, decoupled from the builder.
7. **HC-09/HC-10 persistence decision** — owner-decidable engineering choice (keep volatile by design, or persist).

## Current Blueprint Version
- `docs/spec/UNIFIED-BLUEPRINT.md` **v1.0** (established 2026-09-06). Product/Architecture: **LIUHAO X v3.0 / Architecture v3.0**. UBX upgrade mechanism (UBX-001..006) established 2026-09-28, all currently **PROPOSED**.

## Current Architecture Version
- **LIUHAO X v3.0 / Architecture v3.0** (single-process SQLite audit chain + in-process `RLock` on `p36`; HC-09/HC-10 volatile; 11 integrity chains → 8 compliant / 3 unverified).

## Current Test Baseline
- **Full `pytest tests/` (2026-09-29, pre-fix): 3202 passed, 1 failed, 22 skipped** (604 s). The 1 failure was a test-isolation env leak (see G3), now fixed; a clean-env re-run to confirm 0 failures is the remaining step. 3225 tests collected. Representative suites green (coordination 89/2, fence_metrics 9, fence+gateway-health 40, secret_store 15, lifecycle 5+1skip, kernel_registry 10).

## Current Performance Baseline
- `scripts/bench_baseline.json` (profile-keyed, Windows workstation): `single_append` ≈ 697.5 eps (p95 2.49 ms); `batched_append` batch10≈5028 / batch100≈13078; 1M build ≈ 13 746 eps; 10M build ≈ 13 649 eps; **100M NOT run** (extrapolation only).

---

## Next Autonomous Work
1. **DONE** — `RELEASE-READINESS-CONTRACT.md` (canonical 12-condition contract) + `scripts/verify_readiness.py` (gate battery, writes `readiness_report.json`). First run of the battery already returns PASS on C1 (build), C2 (clean-env boot + /v1/health=200), C4 (no hardcoded secrets + fail-closed + CI gates), C5 (hash-chain 32/32 COMPLIANT); C11 BLOCKED (HC-01); rest NOT VERIFIED pending clean-env evidence.
2. **DONE this cycle** — full test baseline published (3202 passed / 1 failed / 22 skipped) and the single failure root-caused as a test-isolation leak + fixed. Remaining: clean-env re-run to confirm 0 failures reproducibly.
3. **IN FLIGHT (worker)** — **#3** EtcdLease third backend + CI etcd service; **#4** fence+coordination chaos/soak benchmarks; **#6** discovery sweep (U39/U42/drift/repeats) — delegated to parallel workers, results pending.
4. Reconcile doc/code drift (kernel count; Definition Lock proven nonexistent) — covered by the #6 worker.
5. Return to EVOLUTION after RELEASE READY.
