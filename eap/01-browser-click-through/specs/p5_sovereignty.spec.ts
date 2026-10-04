// EAP P5 click-through. STATUS: READY (spec authored). EXECUTION: BLOCKED-on-host (no headless browser in authoring env). NOT EXECUTED here — run on a Docker+Playwright host.
//
// Validates: ApprovalCenter shows the enforcement posture block (enabled + enforced_actions chips),
// a human token can issue a grant (records authorization, approved:true) and revoke it, and the
// HONEST BOUNDARY that issuing a grant does NOT release kernel interception (the kernel stays armed).
//
// Honest boundary detail (verified in src/gateway/main.py:546): the gateway deliberately exposes NO
// REST route that executes a kernel action, so a literal "retry the armed action -> 409" cannot be
// reproduced through the console's HTTP surface. The boundary is therefore proven observably: after a
// grant is issued, GET /v1/policy/enforcement still reports enabled=true with the action in
// enforced_actions — the grant did not unchain the kernel. (A machine-principal token cannot issue a
// grant at all: it is rejected with 400 by the kernel whitelist, tested separately below.)

import { test, expect, type APIRequestContext } from '@playwright/test'
import { authHeaders, loginViaUi } from './_helpers'

test('P5 sovereignty: posture, grant issue + revoke, and honest boundary', async ({ page, request }) => {
  await loginViaUi(page)
  await page.goto('/?p=approval')
  await expect(page.locator('.os-page-title')).toHaveText('审批中心')

  const headers = await authHeaders(page)

  // --- Enforcement posture block visible, with an enabled state + enforced_actions chips ---
  const posture = page.locator('.approval-posture')
  await expect(posture).toBeVisible()

  // Read the authoritative snapshot from the API (policyClient.fetchEnforcement -> GET /v1/policy/enforcement).
  const enf = await request.get('/v1/policy/enforcement', { headers })
  expect(enf.ok()).toBeTruthy()
  const snap = (await enf.json()) as {
    enabled: boolean
    enforced_actions: string[]
    count: number
  }
  expect(snap.enabled, 'kernel policy enforcement must be armed (LIUHAO_KERNEL_POLICY_ENFORCE=CRITICAL)').toBe(true)
  expect(snap.enforced_actions.length, 'expected at least one enforced action chip').toBeGreaterThan(0)

  // UI chips must reflect the same enforced_actions.
  const chips = page.locator('.approval-chip')
  await expect(chips).toHaveCount(snap.enforced_actions.length)
  // The posture pill reads "已武装" when enabled (ApprovalCenter Posture component).
  await expect(page.locator('.approval-posture .health-pill')).toHaveText('已武装')

  // --- Issue a grant via the form (records authorization, NOT "放行") ---
  const action = snap.enforced_actions[0]
  // select.profile-select holds the enforced actions; pick the first one.
  await page.locator('select.profile-select').selectOption({ label: action })
  // Reason input: pinned by its unique placeholder (the token/ttl inputs also use .profile-input, so avoid class-only matching).
  await page.getByPlaceholder('授权理由（记入审计）').fill('EAP click-through grant')
  // ttl defaults to 300; leave as-is. Issue via the "记录授权" button (NOT "放行").
  await page.getByRole('button', { name: '记录授权' }).click()

  // Grant appears in the list with the action and is_active (approved:true).
  const grantLocator = page.locator('.approval-grants li').filter({ hasText: action })
  await expect(grantLocator).toBeVisible()

  // Confirm via API: GET /v1/policy/approvals returns a grant covering `action` that is active.
  const approvals = await request.get('/v1/policy/approvals', { headers })
  expect(approvals.ok()).toBeTruthy()
  const approvalBody = (await approvals.json()) as {
    grants: { actions: string[]; is_active: boolean; approved?: boolean }[]
  }
  const issued = approvalBody.grants.find((g) => g.actions.includes(action) && g.is_active)
  expect(issued, `no active grant covering ${action} after issuance`).toBeDefined()

  // --- HONEST BOUNDARY: issuing a grant does NOT release kernel interception ---
  // Enforcement must STILL be armed and the action must STILL be in enforced_actions after the grant.
  const enfAfter = await request.get('/v1/policy/enforcement', { headers })
  const snapAfter = (await enfAfter.json()) as { enabled: boolean; enforced_actions: string[] }
  expect(snapAfter.enabled).toBe(true)
  expect(snapAfter.enforced_actions).toContain(action)
  // Documented explicitly: a literal 409 cannot be reproduced over REST (gateway has no action route),
  // so the kernel-stays-armed invariant is the observable proof of the boundary.

  // --- Revoke the grant; it must disappear from the list ---
  await grantLocator.getByRole('button', { name: '撤销' }).click()
  await expect(grantLocator).toHaveCount(0)

  const approvalsAfter = await request.get('/v1/policy/approvals', { headers })
  const afterBody = (await approvalsAfter.json()) as {
    grants: { actions: string[]; is_active: boolean }[]
  }
  const stillThere = afterBody.grants.find((g) => g.actions.includes(action) && g.is_active)
  expect(stillThere, 'grant should be gone after revoke').toBeUndefined()
})

test('P5 negative: a machine-principal token cannot issue a grant (expect 400)', async ({ request }) => {
  // Machine principals (system / liuhao-internal-service) are kernel-whitelisted OUT of sovereignty
  // (policyClient.BUILTIN_MACHINE_PRINCIPALS); POST /v1/policy/approvals is rejected with 400.
  //
  // A valid JWT signed for a machine principal cannot be minted from the test harness (we do not have
  // the token secret), and `scripts/register_human_identity.py` refuses to register those built-ins,
  // so a real machine token cannot be seeded here. Skipped with this explanation; the host can run it
  // by issuing a machine token via `scripts/issue_console_token.py --principal system` and asserting 400.
  test.skip(true, 'Machine-principal token cannot be seeded in the authoring env; seed on host via issue_console_token.py --principal system and expect 400 from POST /v1/policy/approvals.')

  // Reference implementation (runs only when un-skipped on a host that has such a token):
  const machineToken = process.env.EAP_MACHINE_TOKEN || ''
  const res = await (request as APIRequestContext).post('/v1/policy/approvals', {
    headers: {
      'X-Liuhao-Token': `Bearer ${machineToken}`,
      Authorization: `Bearer ${machineToken}`,
      'Content-Type': 'application/json',
    },
    data: { actions: ['file_write'], reason: 'should be rejected', ttl_seconds: 300 },
  })
  expect(res.status()).toBe(400)
})
