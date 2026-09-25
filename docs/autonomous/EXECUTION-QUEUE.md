# LIUHAO — Autonomous Execution Queue (LIVING)

> Owned by team lead. Prioritized, assigned, boundary-checked. The team executes autonomously — not for owner approval. Updated continuously as work completes and new gaps are found.

## Standing boundaries (apply to every item)
- No violation of law / security / human-sovereignty decisions.
- No faking success — `VERIFIED` only with independent evidence.
- **Evidence IMMUTABLE**: forensic snapshot + all `D:\WorkBuddyFiles\LIUHAO-Phase3.6-Evidence\` files — never delete / overwrite / re-capture-overwrite.
- No garbage code; every built subsystem ships with Tests + Verification + Docs + Observability hook + Recovery consideration.
- Commits: scoped per-path (never `git add -A`); **do NOT push** to remote without explicit coordination.

## Wave 0 — Locked decisions
- Q0.1 D19/D20/D21 — HC-09/10 semantics + hash_chain dispatch — `sec-impl` (paused; work tree currently clean of its changes)
- Q0.2 D23/D24 — `sovereignty_grant` unification + enforcement harden — `os-impl` (paused)
- Q0.3 D25 R1–R7 — public-claim doc fixes — `gov-impl` (+ governance-legal cross-check `0c266910`; guard `842eeb3e`)
- Q0.4 D22 — read-only `derive_audit_view.py` — `new-d226d27` (paused)

## Wave 1 — P0 security / correctness
- Q1.1 HC-11 `tag_matches()` fail-open → **fail-closed** — implemented by `security-identity` (Windows) then completed/verified by team lead: store refuses every unauthenticated row regardless of key, operator migration path surfaced, CI unblocked. **DONE** (identity fail-closed commit) — see U27/U28.
- Q1.2 Chain-fork recovery framework — `os-systems` — **DONE** commit `fab9c720` (8/8 tests; design+scaffold; not yet wired to live)

## Wave 2 — PROJECT DISCOVERY
- Q2.1 Architecture + OS/runtime + storage + scalability — `os-systems` — **DONE** (U11–U14)
- Q2.2 Security + crypto + identity + forensics — `security-identity` — partially done via Q1.1 code work; formal report pending (agent rate-limited)
- Q2.3 Governance + docs + product-claims + compliance — `governance-legal` — **DONE** (`0c266910`, `842eeb3e`, `5a2de585`, `2b48d71f`; U15–U22)
- Q2.4 Reliability + performance + test-coverage + observability + CI/CD + ops — `quality-reliability` — **DONE** (U24–U26; see below)
- Q2.5 AI/Agent infrastructure gap + product capability gap + unknown-needs discovery — cross-lead — **UNOWNED**, to assign when retries available

## Wave 3 — Foundation build
- Q3.1 Observability scaffolding — `quality-reliability` — **DONE** commit `9a321215` (`src/reliability/`: metrics + Prometheus text, audit_metrics read-only, structured_log, tracing; 27 tests, 99% cov)
- Q3.2 Testing-platform + coverage gates — `quality-reliability` — partially: gap scan done, **dynamic gate blocked** by suite instability (U25); needs `pytest-timeout` + threshold (confirmed absent: `--timeout` is not installed)
- Q3.3 Benchmarking / sizing harness — **DONE** commit `b2617567` (`src/benchmarks/sizing.py`) + commit `54fe5f2c` (`scripts/bench_audit_chain.py`). Measured: ~1.8k eps per-event, ~40k eps batched, ~418 B/row
- Q3.4 Developer SDK / CLI scaffold — `os-systems` — **DONE** commit `6653bb71` (minimal read-only CLI)
- Q3.5 HC-01 F1–F6 remediation — `os-systems` — primitives **DONE** commit `6653bb71` (`fencing.py`, `durability.py`); **wiring + live migration pending**; HC-01 stays UNVERIFIED
- Q3.6 CI doc-lint guard — `governance-legal` — **DONE** (`842eeb3e` + `5a2de585` + `2b48d71f`); HC-11 rule pending Q1.1 → now unblocked by this wave

## Wave 4 — Discovered while finishing Q1.1 (team-lead执行的直接交付)
- Q4.1 **Fail-closed registry migration trap (U27)** — every pre-key deployment silently admits zero humans. Closed: gate script reports `refused_rows` + `[FAIL-CLOSED]` remedy; refusal logs name the fix; CI given an ephemeral key; RUN.md / production-runbook / docker-compose.prod updated
- Q4.2 **Seed-test migration (U28)** — c7 fixtures stamped through the authenticated path (intent preserved, assertions not weakened) + new `test_an_untagged_seed_row_is_refused`
- Q4.3 Workspace sandbox for LLM-driven file access (`src/ai/workspace.py`) — read/write/list converge to one allow-root; committed with its tests

## Team availability note
- `governance-legal`, `os-systems`, `security-identity` are **rate-limited (429)**; reset ~2026-09-26 02:51 UTC+8. Lead continues directly on their queued items; they resume assigned work after the reset. Nothing was claimed as done on their behalf except work the lead itself completed and verified here.

## Standing
- `UNKNOWN-TO-OWNER.md` maintained by all leads (U1–U28).
- Old Blueprint / Roadmap upgraded as discovery dictates (team-decided).
- **HUMAN DECISION REQUIRED** reserved strictly to: data ownership, product/business sovereignty, law/compliance policy, retention final policy, irreversible deletion of original assets, external contracts/liability, non-derivable value tradeoffs. Currently open: **R7/HD-06 retention final policy**, **HD-05 TSA/TPM provider**.
