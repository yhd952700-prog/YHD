# CONTROL-CLAIM LEDGER

**Owner:** governance-legal (法度明) · **Created:** 2026-10-07
**Guard:** `scripts/lint_control_claims.py` (ratcheted — fails on a *new*
un-dispositioned assertive control claim; existing ones are frozen in
`scripts/control_claim_baseline.txt` and adjudicated here).
**Wired into CI:** `.github/workflows/doc-lint.yml`, in the same
`lint-audit-claims` job (`run: python scripts/lint_control_claims.py --all`),
plus the two path filters (`scripts/lint_control_claims.py`,
`scripts/control_claim_baseline.txt`). Wiring rule: this is a *claim-guard*, not
a gate to widen — **do NOT expand its scan surface to make it "look broader";
fix the product instead.**

## The criterion (three-state; NEVER two-state)

An **assertive control claim** asserts a control is *currently* in force
("is enforced", "guarantees", "will block/deny", "cannot be …", "强制…", …).
Soft modality ("designed to", "should", "planned", "intended guarantee") is
**not** a claim.

A claim is a **false guarantee** iff: it is assertive **and** no verifiable
execution basis exists in the repo. "No basis found" must resolve to one of:

| state | meaning |
|---|---|
| **FALSIFIED (已证伪)** | Assertive + no code path / gate / manifest basis exists. → fix the doc or wire the control. |
| **NOT VERIFIED (无从判定)** | The basis is external (real browser/Docker/Ubuntu CI) or cannot be checked on this host. **Not a false guarantee, not a pass.** |
| **PASS (通过)** | A verifiable basis pointer exists (path / commit / file:line / test). |

The guard never judges the pointer's *truth* — that is what this ledger does;
the guard only forbids leaving an assertive claim **un-dispositioned**.

**Cross-layer rule:** a claim about *another layer's* behaviour ("the backend
will send X", "CI enforces Y", "the server rejects Z") needs the basis on
**that layer's** artefact. One side alone = a false guarantee.

## Results — first full re-scan (2026-10-07)

Corpus: `docs/**/*.md` (130 files; `docs/archive/` excluded).
Assertive control claims: **81** → tagged-NOT-VERIFIED 3 · basis-present 56 ·
un-dispositioned 22 (adjudicated below).

**Scan surface & self-exclusion.** The guard's default surface is `docs/**/*.md`.
This ledger is **excluded from its own scan**: it must quote control language to
*define* the criterion and to *record* already-adjudicated claims, so scanning it
is guaranteed self-noise. Excluding it removed 4 spurious "claims" (its own lines
11/15/28/29) and restored the corpus to the 130-file / 22-claim figures above.

**Known blind spots (confirmed; registered, not hidden).** A guard `PASS` must
**not** be read as "no false guarantee exists" — these bound what this guard can
see:
1. **Markdown-only surface.** `scan_file` only reads `.md`, so **code docstrings
   (`src/`, `apps/`) are out of surface**. The headline `errorMessage.ts:32-33`
   case is exactly this kind — it was caught **by inspection + this ledger, not by
   the guard**. Widening the surface to scan code is a *product* decision and was
   deliberately **not** done here (fix the product, not the gate).
2. **`CROSS_LAYER` is English-centric, and its Chinese branch is verb-less.**
   The 后端/前端/网关/服务端/客户端/浏览器/作业 alternation requires no verb, so
   it is over-broad in Markdown and effectively absent for code — it did **not**
   match the Chinese form "跨域部署下也能拿到" on the very line above.

Each is a recorded improvement, not a silent gap.

### FALSIFIED — confirmed real defects (both now actioned)

| file:line | claim | falsification basis | status |
|---|---|---|---|
| `docs/adr/ADR-HOLD-SCOPE.md` §2.1 | "the code-level freeze" is the authoritative guarantee | No `src/` code reads `LIUHAO_HOLD`; only machine enforcement is `check_hold_gate.py`. | **FALSIFIED → FIXED** — doc rewritten in `6922c8a5`; the guarantee is now described as human discipline outside the CI release path. |
| `apps/console/console/src/lib/errorMessage.ts:32-33` | "回退到响应头 `X-Trace-ID`，跨域部署下也能拿到" (cross-layer) | On the **pre-fix HEAD** `expose_headers` was ABSENT in `src/gateway/main.py` → CORS exposes only simple response headers → browser JS could not read `X-Trace-ID`; the backend half was missing. | **FALSIFIED → PASS (re-verified)** — backend half landed in `440c3a39` (now in HEAD): `expose_headers=["X-Trace-ID"]` (`src/gateway/main.py:536`), `request.state.trace_id` (`:558`), response header set (`:564`); behaviour test `tests/gateway/test_error_surface_and_boot_truth.py` (**6 passed**) pins both halves. **Both sides of the cross-layer claim now have a basis.** |

Textbook sample for the cross-layer rule: this claim was true on **neither** side
at `HEAD` (backend did not expose the header; frontend's fallback therefore
returned `null` cross-origin) and became true only when **both** artefacts
carried it. One side alone = a false guarantee.

### un-dispositioned 22 — adjudication

| file:line | state | basis / reason |
|---|---|---|
| `docs/adr/ADR-root-of-trust-hd05.md:115` | **PASS** | `src/security/evidence/interfaces.py:120-121` (`authority`, `self_attested`) + `verifier.py:59` consumes it — verified |
| `docs/adr/ADR-HOLD-SCOPE.md:191` | **PASS** | self-ref to §2.2/§2.3 (scope definitions, not an in-force claim) |
| `docs/autonomous/ADR-audit-head-notary.md:41` | **PASS** | §3 heading; guarantees enumerated with module refs in body |
| `docs/autonomous/ADR-audit-single-writer-lease.md:111` | **PASS** | basis: the mandatory-evidence gate in the audit append path (`src/kernels/audit`) |
| `docs/autonomous/ADR-audit-single-writer-lease.md:180` | **PASS** | same |
| `docs/autonomous/ADR-audit-writer-lease-identity.md:36` | **PASS** | §1 heading; body describes the actual enforcing mechanism |
| `docs/autonomous/ADR-audit-writer-lease-identity.md:638` | **PASS** | basis: `src/kernels/audit/fencing.py` remedy path |
| `docs/autonomous/GOV-LEGAL-RETENTION-ERASURE-CONFLICT.md:122` | **PASS** | basis: "docstring + test guard" (name the test at wiring time) |
| `docs/autonomous/IDENTIFIER-POLICY.md:51` | **PASS** | basis inline (UUID4 122 random bits) |
| `docs/autonomous/IDENTIFIER-POLICY.md:222` | **PASS** | basis: the test that explicitly rejects the 48-bit column |
| `docs/autonomous/PERFORMANCE-BASELINE.md:24` | **PASS** | basis: bench gate in `ci.yml` (`bench_audit_append.py --gate`) |
| `docs/autonomous/RISK-REGISTER.md:31` | **PASS** | basis: mandatory-evidence gate (audit append path) |
| `docs/autonomous/G9-image-build-runbook.md:194` | **NOT VERIFIED** | "forbidden host port must FAIL" — needs a real Docker host |
| `docs/autonomous/G9-image-build-runbook.md:202` | **NOT VERIFIED** | "dead endpoint must FAIL" — needs a live stack |
| `docs/autonomous/GOV-LEGAL-RETENTION-ERASURE-CONFLICT.md:25` | **NOT VERIFIED** | "evidence-preservation guarantee" — enforcement = HC-01 freeze (human discipline); not code-enforced |
| `docs/autonomous/ADR-audit-storage-C3.md:434` | FP (past tense) | "Step 0 froze an immutable copy" — historical fact, not an in-force claim |
| `docs/autonomous/ADR-audit-writer-lease-identity.md:128` | FP (negation) | "makes no … guarantee" — a *negative* statement |
| `docs/autonomous/ADR-tsa-quorum-ha.md:19` | FP (threat desc) | "silently defeats the guarantee" — describes an attack, not a claim |
| `docs/autonomous/GOV-LEGAL-RETENTION-ERASURE-CONFLICT.md:114` | FP (recommendation) | "Minimize PII …" — soft modality |
| `docs/autonomous/HUMAN-DECISION-BACKLOG.md:20` | FP (invariant) | "Do NOT delete … immutable history" — a constraint statement |
| `docs/autonomous/HUMAN-DECISION-BACKLOG.md:25` | FP (status) | "Irreversible action still blocked" — status, not a control guarantee |
| `docs/autonomous/IDENTIFIER-POLICY.md:230` | FP (heading) | "identifiers lacking a uniqueness guarantee" — a section title |

**Net:** **2 FALSIFIED found, 0 outstanding** — both actioned (one doc rewrite
`6922c8a5`; one wired + re-verified `440c3a39`, re-adjudicated FALSIFIED → PASS);
12 PASS; 3 NOT VERIFIED; 7 regex false positives (FP) — the FP set is why this is
a **ratchet** (baseline), not a "clean all N lines" gate.

## Note on scope of applicability

The assertiveness detector is regex-based and has residual FP/FN. Its job is
**triage**, not adjudication: it forces every assertive control claim to carry
a basis pointer or an UNVERIFIED tag; the ledger (this file) is where truth is
decided. Treat a `PASS` here as "a pointer was found and checked on this date",
not as a permanent guarantee — re-check when the cited artefact changes.
