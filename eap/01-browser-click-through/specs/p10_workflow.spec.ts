// EAP P10 click-through. STATUS: READY (spec authored). EXECUTION: BLOCKED-on-host (no headless browser in authoring env). NOT EXECUTED here — run on a Docker+Playwright host.
//
// Validates an end-to-end realistic job from the user seat:
//   login -> create a goal -> it appears in the Goals list -> a delivered artifact is surfaced in Files
//   -> the artifact content is retrievable via /v1/files/content.
//
// NOTE (honest): the Goals page is STRICTLY READ-ONLY (no create form — see Goals.tsx). The create-goal
// form actually lives on the employees "runtime" tab (CreateGoalForm). To keep this spec focused on the
// workflow surface, goal creation is driven via POST /v1/goals (the same endpoint CreateGoalForm calls),
// with background:true so the closed loop actually runs. This mirrors tests/gateway/test_p10_real_job_e2e.py.

import { test, expect } from '@playwright/test'
import { authHeaders, loginViaUi } from './_helpers'

test('P10 workflow: create goal -> appears -> artifact delivered -> content retrievable', async ({ page, request }) => {
  // Job execution + artifact generation can take longer than the default 30s; widen for this spec only.
  test.setTimeout(180_000)

  await loginViaUi(page)
  await page.goto('/')
  await expect(page.locator('.os-page-title')).toHaveText('总览')

  const headers = await authHeaders(page)
  const naturalLanguage = `EAP click-through p10 probe ${Date.now()}`

  // 1) Create a goal (operator.createGoal -> POST /v1/goals). background:true so it really runs.
  const create = await request.post('/v1/goals', {
    headers,
    data: {
      natural_language: naturalLanguage,
      scope: 'L1',
      plan_mode: 'auto',
      background: true,
    },
  })
  expect(create.ok(), `goal creation failed: ${await create.text()}`).toBeTruthy()
  const created = (await create.json()) as { goal_id: string }
  const goalId = created.goal_id
  expect(goalId, 'backend did not return a goal_id').toBeTruthy()

  // 2) The goal appears in the Goals list (navigate /?p=goals, poll the API list too).
  await page.goto('/?p=goals')
  await expect(page.locator('.os-page-title')).toHaveText('目标与任务')
  // The table row renders the (truncated) natural language.
  await expect(page.getByText(naturalLanguage, { exact: false }).first()).toBeVisible({ timeout: 20_000 })

  const listProbe = await request.get('/v1/goals', { headers })
  const listBody = (await listProbe.json()) as { goals: { goal_id: string }[] }
  expect(listBody.goals.some((g) => g.goal_id === goalId)).toBeTruthy()

  // 3) Wait for a delivered artifact (GET /v1/goals/{id} -> artifacts[]). Poll, since execution is async.
  let artifactPath: string | undefined
  await expect
    .poll(
      async () => {
        const detail = await request.get(`/v1/goals/${encodeURIComponent(goalId)}`, { headers })
        if (!detail.ok()) return undefined
        const d = (await detail.json()) as { artifacts?: string[]; state?: string }
        if (d.artifacts && d.artifacts.length > 0) {
          artifactPath = d.artifacts[0]
          return artifactPath
        }
        return undefined
      },
      { timeout: 150_000, intervals: [3_000, 3_000, 5_000] },
    )
    .toBeTruthy()

  expect(artifactPath, 'goal finished with no delivered artifact').toBeTruthy()

  // 4) The artifact is surfaced in Files (/?p=files). The Files page lists the real workspace; the entry
  //    may live in a subdirectory, so we assert the page loads and the artifact name is reachable via API.
  await page.goto('/?p=files')
  await expect(page.locator('.os-page-title')).toHaveText('工作区文件')

  // 5) Artifact content is retrievable via /v1/files/content (operator.readFile).
  const content = await request.get(
    `/v1/files/content?path=${encodeURIComponent(artifactPath!)}`,
    { headers },
  )
  expect(content.ok(), `artifact content fetch failed: ${await content.text()}`).toBeTruthy()
  const contentBody = (await content.json()) as { content: string; size: number }
  expect(contentBody.size).toBeGreaterThan(0)
  expect(contentBody.content.length).toBeGreaterThan(0)
})
