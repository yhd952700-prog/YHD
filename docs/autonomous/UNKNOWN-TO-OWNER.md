# UNKNOWN-TO-OWNER — things the owner didn't ask for, the team found

> Living register. The owner explicitly asked the team to discover and act on what they had not imagined. Entries: **id · discovered-by · date · what · why-it-matters · team-status · priority**.
> `Built/Doing` = team proceeding autonomously. `Resolved` = decided. `HUMAN` = sovereignty boundary, owner only.

| id | discovered-by | date | what | why it matters | status | priority |
|----|----|----|----|----|----|----|
| U1 | security | 2026-09-25 | HC-09/HC-10 are volatile (in-memory only) | Crypto + permission audit can be silently lost on process exit | Decided D19=A+ keeps them volatile by design; durable path designed separately | P1 |
| U2 | os-systems | 2026-09-25 | Single-writer/RLock audit chain + linear `seq` won't scale | 10×/100× growth breaks ordering + concurrency | Researching HLC / segmented ordering | P1 |
| U3 | reliability | 2026-09-25 | No recovery framework for chain forks | A fork (HC-01) currently blocks GO; need detect → recover | Designing recovery framework | P0 |
| U4 | observability | 2026-09-25 | No metrics / tracing layer across kernels | Can't operate/debug a production system blind | Roadmap; build incrementally | P1 |
| U5 | platform | 2026-09-25 | No developer SDK/API or reproducible build | External devs/agents can't extend safely | Roadmap | P2 |
| U6 | security | 2026-09-25 | HC-11 `tag_matches()` returns True when key absent (fail-open) | Identity-integrity verification silently bypassed | Decided: enforce key + fail-closed (pending impl) | P0 |
| U7 | forensics | 2026-09-25 | Forensic baseline custody not automated; snapshot opened RW post-capture | Chain-of-custody break undermines evidence | Decided: immutable storage + manifest; snapshot retained | P1 |
| U8 | governance | 2026-09-25 | Schema v1 (snapshot) vs v9 (live) divergence | Migration-source ambiguity | Resolved: Path A (live source); snapshot historical | Resolved |
| U9 | scale | 2026-09-25 | No benchmarking / sizing harness | Can't plan 10×/100× capacity | Roadmap | P2 |
| U10 | agent | 2026-09-25 | No safe external-agent onboarding protocol | Future "AI Agent will need" unaddressed | Roadmap | P2 |
| U11 | os-systems | 2026-09-25 | Per-instance `threading.RLock()` + `check_same_thread=False` + no `busy_timeout` across 25+ modules (e.g. src/kernels/audit/\_\_init\_\_.py:191-192; 25+ `RLock` sites) | Cross-process concurrency unprotected; the RCA-1 fork class is systemic, not just the audit store. Also no `UNIQUE(seq)` / DEFERRED-only transactions allow duplicate seq | Decided: move to single-writer-service + `BEGIN IMMEDIATE` + `UNIQUE(seq)` (F1/F2) | P1 |
| U12 | os-systems | 2026-09-25 | Distribution fencing / leader-election are Redis-less stubs (src/distribution/election.py:59, src/distribution/lock.py:118) | HD-02=A single-writer has NO cross-process enforcement today; failover reintroduces split-brain (C3) | Designing fencing token lease as part of F2/HD-02 | P0 |
| U13 | os-systems | 2026-09-25 | Dual audit persistence: JSON `AuditStore` (src/audit/store.py:21, default data/audit/events.json) coexists with the SQLite store | Two audit sources of truth (R12); contradicts F2 single-source; reconciliation hazard | Retire the JSON store (F2/R12) | P1 |
| U14 | os-systems | 2026-09-25 | `journal_mode=WAL` + `synchronous=NORMAL` on 6+ SQLite stores (audit, memory, execution journal, identity persistence, conversation_store, personal_context) | Windows power-loss can lose an uncheckpointed `-wal`; a copy-based snapshot without `VACUUM INTO` loses data; a single SQLite file is a write-throughput ceiling (mandatory single-writer) | Research checkpoint policy + Windows durability; recovery mandates `VACUUM INTO` snapshot (R8/R11) | P1 |
