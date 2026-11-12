# EAP Click-Through — Failure Interpretation

For each spec, what each failure **category** means and where to look. Read this before triaging a
red gate — most reds are seed/config problems, not product bugs.

---

## Shared / cross-cutting

| Symptom | Meaning | Fix |
|---|---|---|
| Every spec fails at `loginViaUi` (cannot find `#os-principal`, or login card never clears) | Gateway not reachable, or no seeded human credential. `GET /v1/auth/config` → `secret_store.configured` false, or `login_eligible_humans == 0`. | Seed via `scripts/register_human_identity.py --principal eap_admin --password-stdin`; set `EAP_PRINCIPAL`/`EAP_SECRET`. |
| Navigation shows overview (`总览`) instead of the target page | `?p=` routing issue, or page key unknown to `KNOWN_PAGES` in `App.tsx`. | Confirm the gateway is the SPA host (root `/` serves index.html). No `/console` prefix exists. |
| A `404` page (not the SPA) is returned | Static mount / reverse-proxy misconfig — the SPA is not served at `/`. | Verify the single-port gateway serves the console `index.html` at `/` (NOT a separate `/console` route). |

---

## P1 (p1_console.spec.ts)

| Failure | Meaning |
|---|---|
| Overview title not `总览` | SPA didn't load / wrong base URL. |
| `POST /v1/employees` not 2xx | EmployeeStore/backend not persisted or endpoint path changed. |
| Employee name not in `GET /v1/employees` | Hire didn't persist — check backend lifecycle (EmployeeStore on persisted volume). |
| No `暂停/恢复` buttons after hire | Runtime tab not opened, or agents weren't spawned for the employee. |
| Pause/resume status doesn't flip | Agent pause/resume endpoint or agent id mapping changed. |
| Employee reappears after `DELETE` | Removal not persisted (volume not mounted / wrong volume). |

---

## P5 (p5_sovereignty.spec.ts)

| Failure | Meaning |
|---|---|
| `.approval-posture` not visible | Not authenticated on the approval surface, or `/v1/policy/enforcement` requires a token you don't have. |
| `enforced_actions.length == 0` / pill not `已武装` | Kernel policy **not armed** — `LIUHAO_KERNEL_POLICY_ENFORCE` not set to `CRITICAL` (or misconfigured). This is a seed/config issue, not a bug. |
| Chip count mismatch | Frontend posture snapshot out of sync with `/v1/policy/enforcement` (should match). |
| Grant not in list after "记录授权" | Issue flow / `POST /v1/policy/approvals` failing (non-human token → 400, or backend error). |
| **Honest-boundary assertion fails** (enforcement no longer armed after grant) | This would be a REAL product regression — the grant must NEVER disarm the kernel. Investigate immediately. |
| Revoke doesn't remove the grant | `DELETE /v1/policy/approvals/{id}` failing. |

> Unexpected `409` anywhere in P5 is **expected and honest** (kernel interception), not a bug.

---

## P9 (p9_identity.spec.ts)

| Failure | Meaning |
|---|---|
| `total_principals == 0` | Registry empty or `LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY` unset → registry refuses all rows ("0 admitted humans"). Set the integrity key. |
| Principals table has no rows | `GET /v1/identity/principals` empty or endpoint changed. |
| Unknown-id lookup does **not** show `主体不存在或已被清除` | Frontend not surfacing the real 404 (would be a product bug — honesty regression). Also assert `GET .../{id}` returns 404. |
| A forbidden button IS found (`授权/创建/删除/保存授权`) | The page gained a write/grant control — violates the read-only contract. Product bug. |

---

## P10 (p10_workflow.spec.ts)

| Failure | Meaning |
|---|---|
| `POST /v1/goals` not 2xx | Goal creation endpoint failing. |
| Goal never appears in `/?p=goals` | List endpoint or rendering issue. |
| `artifacts` never populates within timeout | Execution didn't run — **no LLM provider configured**, or the job errored. Check `/v1/identity`/provider status and backend logs. This is environmental, not necessarily a product bug. |
| `/v1/files/content` 404/413/415 | Artifact path wrong, or file binary/oversized. Backend returns real status — read it. |

> If the artifact generation is slow, raise the per-spec timeout (already 180s) on the host.
