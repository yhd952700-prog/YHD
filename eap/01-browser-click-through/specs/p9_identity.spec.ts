// EAP P9 click-through. STATUS: READY (spec authored). EXECUTION: BLOCKED-on-host (no headless browser in authoring env). NOT EXECUTED here — run on a Docker+Playwright host.
//
// Validates: Identity page (lib/operator -> GET /v1/identity/{summary,principals,principals/{id},permissions})
// renders three real tables with non-empty data; a single-principal lookup for an unknown id returns the
// honest "主体不存在或已被清除" message (real 404); and the page is STRICTLY READ-ONLY — there are no
// write/grant buttons.

import { test, expect } from '@playwright/test'
import { authHeaders, loginViaUi } from './_helpers'

test('P9 identity: three tables render, unknown id is honest 404, page is read-only', async ({ page, request }) => {
  await loginViaUi(page)
  await page.goto('/?p=identity')
  await expect(page.locator('.os-page-title')).toHaveText('身份与权限')

  const headers = await authHeaders(page)

  // --- Three real, non-empty data tables ---
  const summary = await request.get('/v1/identity/summary', { headers })
  expect(summary.ok()).toBeTruthy()
  const summaryBody = (await summary.json()) as { total_principals: number }
  expect(summaryBody.total_principals, 'identity registry should be seeded with principals').toBeGreaterThan(0)

  // Summary card present + carries a real count.
  await expect(page.getByText('身份总览 / Identity Summary')).toBeVisible()
  await expect(page.locator('.os-kv-value').first()).toBeVisible()

  // Principals table renders rows.
  const principals = await request.get('/v1/identity/principals', { headers })
  const principalsBody = (await principals.json()) as { principals: unknown[] }
  expect(principalsBody.principals.length).toBeGreaterThan(0)
  await expect(page.getByText('主体清单 / Principals')).toBeVisible()
  await expect(page.locator('.os-table').first().locator('tbody tr').first()).toBeVisible()

  // Permissions card renders (read-only; an empty grant set is acceptable, but the card must exist).
  await expect(page.getByText('权限（只读） / Permissions')).toBeVisible()

  // --- Single-principal lookup: unknown id -> honest 404 message ---
  const unknownId = `eap-unknown-${Date.now()}`
  // The lookup input is the .os-input with a unique placeholder (pinned to avoid ambiguity).
  const lookup = page.getByPlaceholder('例如 system / human:xxx / agent 的 hex id')
  await lookup.fill(unknownId)
  await page.getByRole('button', { name: '查询' }).click()
  // Identity.tsx renders <p class="os-error">主体不存在或已被清除：{detailError}</p>.
  await expect(page.locator('.os-error')).toContainText('主体不存在或已被清除')

  // Cross-check the API contract: unknown id -> real 404.
  const probe = await request.get(`/v1/identity/principals/${encodeURIComponent(unknownId)}`, { headers })
  expect(probe.status()).toBe(404)

  // --- Page is STRICTLY READ-ONLY: no write/grant buttons ---
  // The only button present is the read-only "查询" lookup; there must be no 授权/创建/删除/保存授权 button.
  const forbidden = page.getByRole('button', { name: /授权|创建|删除|保存授权|grant/i })
  await expect(forbidden).toHaveCount(0)
})
