# ADR-HOLD-SCOPE — Scope and Limits of the `HOLD` Freeze

| | |
|---|---|
| **Status** | LOCKED (decision D27) |
| **Decision** | D27 |
| **Date** | 2026-09-24 |
| **Owner** | Human (LIUHAO owner / boss) |
| **Supersedes** | — |
| **Related** | D22 (derived audit view + red line on `audit_store.db`), D17 (risk-graded audit enforcement), `human_sovereignty_override` |

---

## 1. Context

LIUHAO needs a single, well-understood operational switch — `HOLD` — that can
stop the system from *moving itself forward* (deploying, auto-editing code,
releasing through CI) while an incident, investigation, or human review is in
progress. Without a precisely-scoped definition, `HOLD` risks either being too
weak (a deploy sneaks past it) or too strong (**silently blocking a human's
explicit, sovereign override**, which would defeat the entire point of the
human-sovereignty architecture).

Two hard constraints frame this decision:

1. **D22 red line** — the original `audit_store.db` must **never be deleted,
   reordered, or overwritten** by any tool. Any change to the audit record of
   record is out of scope for an operational freeze and must go through its own
   human-decision path.
2. **`human_sovereignty_override` is the highest-authority channel** — it is the
   mechanism by which the human owner deliberately overrides automated policy.
   A freeze must not be able to swallow that channel.

This ADR therefore defines *exactly* what `HOLD` freezes, *exactly* what it does
**not** gate, and the invariant that any human-override side-effects still obey.

---

## 2. Decision

### 2.1 What `HOLD` FREEZES (in scope)

When `HOLD` is active, the following automated forward-motion paths are frozen:

| # | Frozen path | Rationale |
|---|---|---|
| F1 | **Deployment / promotion to any environment** (build → deploy artifact step) | Stops a bad/under-review build from reaching production. |
| F2 | **Automatic code changes** generated or applied by agents, pipelines, or "auto-fix" tooling | Prevents unattended mutation of the codebase during a freeze. |
| F3 | **CI release gating / publish steps** (the release-promotion jobs) | The freeze is meaningless if CI can still cut and publish a release. |

`HOLD` is a freeze of *automated forward motion*. It is intentionally narrow.

### 2.2 What `HOLD` does NOT gate (out of scope)

| # | Not gated by `HOLD` | Why |
|---|---|---|
| N1 | **`human_sovereignty_override`** (the human owner's explicit override channel) | The human owner's deliberate override is the supreme authority; a freeze must never be able to veto the human. Gating it would invert the sovereignty model. |
| N2 | **Read-only forensic / audit tooling** (e.g. the D22 derived audit view) | These only read and never mutate the record of record; they are safe to run during a freeze and are often *needed* during one. |
| N3 | **Local verification / safeguard scripts that only read or report** | Same as N2 — reporting must continue so the freeze can be observed. |

### 2.3 Invariant — override side-effects remain bound by the D22 red line

Any side-effect produced by a `human_sovereignty_override` (whether or not
`HOLD` is active) **continues to obey the D22 red line**:

> The original `audit_store.db` is **never deleted, reordered, or overwritten**.
> Every override is *appended* to the chain as a new, hash-linked event. The
> override may change *runtime posture*; it may **never** rewrite *history*.

`HOLD` does not — and cannot — relax this invariant. An override under `HOLD`
still writes a new auditable event; it does not edit or remove prior events.

### 2.4 Optional `HOLD` env gate for deploy / CI jobs (recommended, not mandatory)

Deploy and CI release jobs SHOULD consult an explicit gate before promoting:

* **Env var:** `LIUHAO_HOLD=1` (or any non-empty truthy value).
* **Behaviour:** if set, the deploy/release job MUST abort *before* the
  promotion step (F1/F3) and exit non-zero, surfacing a clear `HOLD active`
  message. Automatic code-change agents (F2) MUST also check this gate and stop.
* **Not a security boundary:** the env gate is an operational safeguard, not an
  authorization control. The authoritative guarantee is the code-level freeze in
  §2.1; the env gate is the convenient, visible switch.
* **Human override still passes:** a job that is blocked by `HOLD` MUST still
  permit an explicit `human_sovereignty_override` to proceed (per N1), and that
  override's audit event is appended (per §2.3), never rewriting history.

---

## 3. Consequences

### Positive
* A single, unambiguous switch stops automated forward motion without disabling
  the human owner.
* The audit record of record is explicitly carved out of the freeze, reinforcing
  the D22 red line.
* Deploy/CI jobs get a simple, inspectable gate.

### Negative / costs
* Two code paths must co-exist: the automated freeze gate and the
  human-override channel. They must be tested together so the freeze can never
  silently swallow an override (regression risk — see §4).
* `HOLD` is operational, not a security control; over-reliance on the env flag
  alone would be a false sense of safety.

### Rollback / change
* To widen the freeze, extend §2.1 (and the env-gate check) via a new decision.
* To narrow it, the same. The out-of-scope guarantees in §2.2 / §2.3 are
  **not** negotiable without a superseding human decision — they protect the
  human-sovereignty and audit-integrity bedrock.

---

## 4. Acceptance / verification notes
* A test MUST exist proving that, with `HOLD` active, an automated deploy/release
  is blocked, **while** a `human_sovereignty_override` is still permitted and
  appends (never edits) the audit chain.
* A test MUST exist proving that D22-style read-only tooling runs uninterrupted
  under `HOLD`.
* No code in this repo may, under `HOLD`, delete/reorder/overwrite
  `audit_store.db`.
