# EAP Click-Through — Expected Results (Gate Criteria)

For each gate, the **exact assertions that must pass for BLOCKED → PASS**, and the **concrete
evidence** the run collects (screenshots via `page.screenshot`, JSON from API calls, console logs).
Run evidence is produced by `npx playwright test --reporter=json,list`.

---

## P1 — Console, login, employee lifecycle (p1_console.spec.ts)

**Gate flips to PASS when ALL hold:**
1. `GET /` loads the SPA; `.os-page-title` is visible and reads `总览`.
2. UI login (`#os-principal` / `#os-secret` / `button.os-btn`) reaches the shell (`X-Liuhao-Token` issued).
3. `/?p=employees` renders `.os-page-title` = `我 AI 员工` and the **hire form** (`button "雇佣员工"`) is visible.
4. `POST /v1/employees` returns 2xx; the name appears in `GET /v1/employees` → `employees[].name`.
5. After hire, **pause/resume controls** exist (`button` matching `暂停|恢复` on the runtime tab).
6. `POST /v1/employees/{agentId}/pause` and `/resume` flip `GET /v1/employees` agent `status` to `paused` then back.
7. After remove (`DELETE /v1/employees/{name}`), the name is gone from `GET /v1/employees` and stays gone after a reload (persistence).

**Evidence collected:**
- Screenshot on failure (`screenshot: 'only-on-failure'`).
- API JSON: `hire`, `GET /v1/employees` (before/after pause/resume/remove).
- DOM assertions: page titles, hire-form button, pause/resume buttons, employee name text.

---

## P5 — Sovereignty / Approval Center (p5_sovereignty.spec.ts)

**Gate flips to PASS when ALL hold:**
1. `/?p=approval` renders `.os-page-title` = `审批中心`.
2. `.approval-posture` visible; `GET /v1/policy/enforcement` → `enabled === true` and `enforced_actions.length > 0`.
3. UI `.approval-chip` count == `enforced_actions.length`; posture pill text = `已武装`.
4. Issuing a grant via the form (`select.profile-select` + reason + `button "记录授权"`) makes the grant appear in `.approval-grants li` **and** `GET /v1/policy/approvals` returns an active grant covering the action (approved:true).
5. **Honest boundary:** after issuance, `GET /v1/policy/enforcement` STILL has `enabled === true` and the action remains in `enforced_actions` (the grant did NOT unchain the kernel).
6. Revoke (`button "撤销"`) removes the grant from the list and from `GET /v1/policy/approvals`.

**Negative test (separate, skipped on host without machine token):** a machine-principal token → `POST /v1/policy/approvals` returns **400**.

**Evidence collected:**
- API JSON: `/v1/policy/enforcement` (before + after), `/v1/policy/approvals` (before/after/after-revoke).
- DOM assertions: posture block, chips, grant rows, revoke button.
- Screenshot on failure.

> The literal "retry armed action → 409" cannot be reproduced over REST: the gateway exposes **no**
> action-executing route by design (src/gateway/main.py:546). The boundary is proven by the
> kernel-stays-armed invariant instead.

---

## P9 — Identity & Permissions, read-only (p9_identity.spec.ts)

**Gate flips to PASS when ALL hold:**
1. `/?p=identity` renders `.os-page-title` = `身份与权限`.
2. `GET /v1/identity/summary` → `total_principals > 0`; the `身份总览 / Identity Summary` card and a non-empty `.os-kv-value` render.
3. `GET /v1/identity/principals` returns `principals.length > 0`; the `主体清单 / Principals` table renders ≥1 `tbody tr`.
4. The `权限（只读） / Permissions` card renders.
5. Unknown-id lookup: fill the lookup input + click `查询` → `.os-error` contains `主体不存在或已被清除`; and `GET /v1/identity/principals/{unknown}` returns **404**.
6. **Read-only:** no button matching `授权|创建|删除|保存授权|grant` exists on the page.

**Evidence collected:**
- API JSON: `/v1/identity/summary`, `/v1/identity/principals`, unknown-id 404 probe.
- DOM assertions: three card titles, principals table rows, `.os-error` text, forbidden-button count == 0.
- Screenshot on failure.

---

## P10 — Workflow end-to-end (p10_workflow.spec.ts)

**Gate flips to PASS when ALL hold:**
1. `POST /v1/goals` (background) returns `goal_id`.
2. `/?p=goals` lists the goal (row with the natural-language text visible) and `GET /v1/goals` contains `goal_id`.
3. Polling `GET /v1/goals/{id}` eventually yields `artifacts.length > 0` (a delivered artifact).
4. `/?p=files` loads (`工作区文件`).
5. `GET /v1/files/content?path=<artifact>` returns 200 with `content.length > 0` and `size > 0`.

**Evidence collected:**
- API JSON: goal create response, `GET /v1/goals`, `GET /v1/goals/{id}` (artifacts), `GET /v1/files/content`.
- DOM assertions: overview title, goals title + goal row, files title.
- Screenshot on failure.

> Goal creation is driven via `POST /v1/goals` (the same endpoint `CreateGoalForm` calls), because the
> Goals page itself is strictly read-only. Artifact generation depends on a configured LLM provider and may
> need the extended per-spec timeout (180s) on slower hosts.
