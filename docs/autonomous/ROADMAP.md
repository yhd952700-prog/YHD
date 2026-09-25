# LIUHAO — Autonomous Roadmaps (LIVING DOCUMENT)

> These roadmaps evolve continuously as research progresses. They are the team's current best plan toward a production-grade, complete, evolvable large system — not a fixed contract.

---

## Vision

LIUHAO becomes a production-grade **Human-Sovereign Operating System for autonomous agents**: integrity-first by construction, independently verifiable, recoverable by design, and evolvable to 10× / 100× / 1000× scale without architectural rewrite.

---

## Architecture Roadmap

- **Now (`p36`):** single-process SQLite audit chain with in-process `RLock`; HC-09/10 volatile; 11 integrity chains — 8 compliant, 3 unverified.
- **Next:** single-writer + fencing/idempotency for the audit chain (close RCA-1); durable HC-09/10 design (D19=A+ keeps them volatile by decision, but the durable path must be designed); unified `sovereignty_grant` (D23); enforcement hardening (D24).
- **10×:** partition the audit log by time/domain; replace linear `seq` with a scalable ordering (HLC / segmented seq); write-ahead replication + quorum for multi-node.
- **100×:** distributed audit service (control plane + data plane); consensus-backed chain; tiered storage (hot/warm/cold); independent verification service.
- **1000×:** multi-region, geo-partitioned, stream-processed integrity with continuous verification + anomaly detection.

---

## Capability Roadmap (gaps the team will build)

- Durable, tamper-evident HC-09/10 (currently volatile — owner didn't ask; team found the gap).
- Recovery framework for chain forks (detect → quarantine → re-derive → re-verify).
- Observability layer: metrics, structured logs, distributed tracing across kernels.
- Developer platform: SDK/API, CLI, local dev harness, reproducible builds.
- Benchmarking / sizing harness for scale planning.
- Agent platform: safe onboarding of external AI agents with capability-scoped authority.
- CI/CD hardening: per-chain verification gates, migration dry-run, evidence-custody automation.
- Independent verification service (third-party-auditable proofs of chain integrity).

---

## Research Roadmap

- Scale failure modes of linear `seq` and single-writer; HLC / segmented-ordering tradeoffs.
- Cryptographic agility for the hash-chain (registry already supports `hash_alg`).
- Unknown-unknown discovery process (UNKNOWN-TO-OWNER).
- Cost/latency of continuous verification at scale.

---

## Security Roadmap

- Close RCA-1 (single-writer / fencing).
- Close HC-11 `tag_matches()` empty-key bypass (env-key enforcement + fail-closed).
- Harden custody of forensic baselines (immutable storage + manifest).
- Continuous secret/key-management review.

---

## Reliability Roadmap

- Idempotent migration; online verification; automated recovery; chaos / failure-injection testing; SLOs for integrity verification.

---

## Performance Roadmap

- Benchmark the ~331k-row chain verification; index/query optimization; batched chain rebuild; streaming verification.

---

## Dev-Platform Roadmap

- Reproducible build; local test harness (managed venv); per-chain test suites; coverage gates; doc generation.

---

## Agent Roadmap

- Capability-scoped agent authority; human-override audit (HC-09/10); agent onboarding protocol; per-agent observability.
