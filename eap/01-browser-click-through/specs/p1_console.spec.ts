// EAP P1 click-through. STATUS: READY (spec authored). EXECUTION: BLOCKED-on-host (no headless browser in authoring env). NOT EXECUTED here — run on a Docker+Playwright host.
//
// Validates: console boots to overview, UI login works, the employee surface
// (hire form + per-agent pause/resume controls) is present, and a real
// hire -> pause -> resume -> remove lifecycle persists on the backend.

import { test, expect, type Page, type APIRequestContext } from '@playwright/test'
import { authHeaders, getToken, loginViaUi } from './_helpers'

/** Switch the employees page to the "runtime" tab where the hire form + agent controls live. */
async function openRuntimeTab(page: Page) {
  // Roster defaults to the 'kernels' tab; runtime controls are behind the 运行时 badge.
  await page.getByRole('button', { name: /运行时/ }).click()
  await expect(page.getByRole('button', { name: '雇佣员工' })).toBeVisible()
}

test('P1 console boots, logs in, and employee lifecycle persists', async ({ page, request }) => {
  test.setTimeout(90_000)

  // 1) Overview loads at base URL (SPA, ?p=overview is the default).
  await page.goto('/')
  const title = page.locator('.os-page-title')
  await expect(title).toBeVisible()
  await expect(title).toHaveText('总览')

  // 2) Login via the real UI (input#os-principal / input#os-secret / button.os-btn).
  await loginViaUi(page)

  // 3) Reach the employee surface.
  await page.goto('/?p=employees')
  await expect(page.locator('.os-page-title')).toHaveText('我 AI 员工')

  // Hire form must exist (HireEmployeeForm -> button "雇佣员工").
  await openRuntimeTab(page)
  await expect(page.getByRole('button', { name: '雇佣员工' })).toBeVisible()

  // 4) Real hire via the API (operator.hireEmployee -> POST /v1/employees).
  const name = `eap-click-${Date.now()}`
  const headers = await authHeaders(page)
  const hire = await request.post('/v1/employees', {
    headers,
    data: { name, agent_count: 2, agent_types: [] },
  })
  expect(hire.ok(), `hire failed: ${await hire.text()}`).toBeTruthy()

  // Employee appears by name in the real roster (GET /v1/employees).
  const list = await request.get('/v1/employees', { headers })
  const body = (await list.json()) as {
    employees?: { name: string; agents: { id: string; status: string }[] }[]
  }
  const employee = (body.employees ?? []).find((e) => e.name === name)
  expect(employee, `employee ${name} not found in GET /v1/employees`).toBeDefined()
  expect(employee!.agents.length).toBeGreaterThan(0)

  // 5) Pause/resume controls exist for the new employee's agents
  //    (AgentActionButtons -> button "暂停" / "恢复" with class .os-btn-ghost).
  await page.goto('/?p=employees')
  await openRuntimeTab(page)
  await expect(page.getByRole('button', { name: /暂停|恢复/ })).not.toHaveCount(0)

  // 6) Drive pause -> resume via the real endpoints (operator.pauseAgent / resumeAgent).
  const agentId = employee!.agents[0].id
  const paused = await request.post(`/v1/employees/${encodeURIComponent(agentId)}/pause`, { headers })
  expect(paused.ok(), `pause failed: ${await paused.text()}`).toBeTruthy()

  const afterPause = await request.get('/v1/employees', { headers })
  const afterPauseBody = (await afterPause.json()) as {
    employees?: { name: string; agents: { id: string; status: string }[] }[]
  }
  const pausedAgent = (afterPauseBody.employees ?? [])
    .find((e) => e.name === name)!
    .agents.find((a) => a.id === agentId)!
  expect(pausedAgent.status).toBe('paused')

  const resumed = await request.post(`/v1/employees/${encodeURIComponent(agentId)}/resume`, { headers })
  expect(resumed.ok(), `resume failed: ${await resumed.text()}`).toBeTruthy()

  // 7) Persistence within the same session: reload, employee still present (EmployeeStore is real & persisted).
  await page.reload()
  await openRuntimeTab(page)
  await expect(page.getByText(name, { exact: false }).first()).toBeVisible()

  // 8) Remove the employee (operator.deleteEmployee -> DELETE /v1/employees/{name}).
  const removed = await request.delete(`/v1/employees/${encodeURIComponent(name)}`, { headers })
  expect(removed.ok(), `remove failed: ${await removed.text()}`).toBeTruthy()

  const afterRemove = await request.get('/v1/employees', { headers })
  const afterRemoveBody = (await afterRemove.json()) as {
    employees?: { name: string }[]
  }
  expect((afterRemoveBody.employees ?? []).some((e) => e.name === name)).toBeFalsy()

  // And it stays gone after a reload (persisted removal).
  await page.reload()
  await openRuntimeTab(page)
  await expect(page.getByText(name, { exact: false })).toHaveCount(0)
})
