/**
 * 「我的员工」补齐四项能力的界面契约：复制、导出、质量评估、预算。
 *
 * 这些断言只覆盖「按钮确实打到对应端点」「结果如实渲染」：
 * 预算的拦截在后端做（`calls_limit` 超限 402），前端不负责"提示一下就放行"。
 */
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MyEmployees } from './MyEmployees'

const employee = {
  name: '小周',
  agent_count: 1,
  agents: [
    { id: '小周-a0', agent_id: '小周-a0', agent_type: 'general', status: 'idle' },
  ],
  role_description: '整理周报',
  default_capabilities: ['file_write'],
  employment_status: 'active',
  read_only: false,
  stats: {
    name: '小周',
    agent_count: 1,
    total_tasks_submitted: 3,
    total_tasks_completed: 2,
    total_tasks_failed: 1,
    task_queue_length: 0,
  },
}

const quality = {
  employee: '小周',
  available: true,
  source: 'ledger:liuhao_tasks.sqlite',
  generated_at: '2026-11-11T10:00:00+08:00',
  definitions: {},
  sample: 3,
  status_counts: { completed: 2, failed: 1 },
  completed: 2,
  failed: 1,
  cancelled: 0,
  in_progress: 0,
  terminal: 3,
  completion_rate: 0.667,
  failure_rate: 0.333,
  avg_duration_seconds: null,
  duration_sample: 0,
  duration_missing: 3,
  manual_rework_count: 2,
  manual_retry_count: 1,
  takeover_count: 1,
  handed_off_count: 0,
  auto_retry_attempts: 0,
  last_task_at: null,
  notes: ['没有完整的起止时间，平均耗时算不出来。'],
}

const budget = {
  employee: '小周',
  configured: true,
  policy: { enabled: true, calls_limit: 5, tokens_limit: null, cost_limit: null, period: 'day' },
  period_label: '每天',
  window: '2026-11-11',
  period_start: '2026-11-11T00:00:00+08:00',
  next_reset_at: '2026-11-12T00:00:00+08:00',
  usage: {
    calls: 1,
    tokens: 120,
    cost: 0.0012,
    cost_complete: true,
    cost_unknown_calls: 0,
    last_source: 'usage_meter',
    updated_at: '2026-11-11T10:00:00+08:00',
  },
  remaining: { calls: 4, tokens: null, cost: null },
  exceeded: false,
  enforced: true,
  violations: [],
  notes: [],
}

const importPlan = {
  dry_run: true,
  on_conflict: 'skip',
  plan: [
    {
      index: 1,
      requested_name: '新人',
      final_name: '新人',
      action: 'create',
      reason: '将新建',
      errors: [],
      warnings: [],
      will_be_disabled: true,
    },
  ],
  summary: { create: 1, rename: 0, skip: 0, error: 0 },
  notes: [],
}

function json(data: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => data,
    text: async () => JSON.stringify(data),
    headers: new Headers(),
  } as unknown as Response
}

const calls: Array<{ url: string; init?: RequestInit }> = []

beforeEach(() => {
  calls.length = 0
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      calls.push({ url, init })
      if (url.includes('/v1/employees/capabilities')) {
        return json({ capabilities: [], count: 0, ids: [] })
      }
      if (url.endsWith('/copy')) {
        return json(
          {
            created: true,
            source: '小周',
            name: '小周-copy',
            employment_status: 'disabled',
            copied: { skills: [], capabilities: ['file_write'], prompts: ['planner'] },
            warnings: [],
            notes: [],
          },
          201,
        )
      }
      if (url.includes('/export')) {
        return json({
          format: 'liuhao.employee-config',
          format_version: 1,
          exported_at: '2026-11-11T10:00:00+08:00',
          employee: { name: '小周' },
          notes: [],
        })
      }
      if (url.includes('/quality')) return json(quality)
      if (url.includes('/budget')) {
        if (init?.method === 'PUT') return json(budget)
        if (init?.method === 'DELETE') return json({ deleted: true })
        return json(budget)
      }
      if (url.includes('/import/preview')) return json(importPlan)
      if (url.endsWith('/import')) {
        return json({
          dry_run: false,
          results: [{ name: '新人', status: 'created', message: '已创建（默认停用）' }],
          created_count: 1,
          skipped_count: 0,
          failed_count: 0,
          notes: [],
        })
      }
      return json({
        agents: [],
        employees: [employee],
        count: 1,
        employee_count: 1,
        stats: {
          name: 'default',
          agent_count: 0,
          total_tasks_submitted: 0,
          total_tasks_completed: 0,
          total_tasks_failed: 0,
          task_queue_length: 0,
        },
      })
    }),
  )
  // 导出会触发浏览器下载：jsdom 里替换掉真实跳转与 Blob URL。
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
  Object.defineProperty(URL, 'createObjectURL', {
    configurable: true,
    writable: true,
    value: vi.fn(() => 'blob:mock'),
  })
  Object.defineProperty(URL, 'revokeObjectURL', {
    configurable: true,
    writable: true,
    value: vi.fn(),
  })
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

async function rowOf() {
  const name = await screen.findByText('小周', { selector: '.os-emp-name' })
  const row = name.closest('.os-row')
  if (!row) throw new Error('没有找到员工行')
  return row as HTMLElement
}

describe('员工补齐能力', () => {
  it('复制：打 copy 端点，并明确提示副本已停用', async () => {
    render(<MyEmployees query="" />)
    const row = await rowOf()

    fireEvent.click(within(row).getByRole('button', { name: '复制' }))

    await waitFor(() =>
      expect(screen.getByText(/已复制「小周」为「小周-copy」/)).toBeTruthy(),
    )
    expect(screen.getByText(/副本默认停用/)).toBeTruthy()
    expect(calls.some((item) => item.url.endsWith('/v1/employees/%E5%B0%8F%E5%91%A8/copy'))).toBe(
      true,
    )
  })

  it('导出：取回配置并触发下载', async () => {
    render(<MyEmployees query="" />)
    const row = await rowOf()

    fireEvent.click(within(row).getByRole('button', { name: '导出配置' }))

    await waitFor(() => expect(screen.getByText(/已导出「小周」的配置/)).toBeTruthy())
    const exported = calls.find((item) => item.url.includes('/export'))
    expect(exported).toBeTruthy()
    expect(URL.createObjectURL).toHaveBeenCalled()
  })

  it('质量：如实显示可算的指标，算不出来的写「算不出来」', async () => {
    render(<MyEmployees query="" />)
    const row = await rowOf()

    fireEvent.click(within(row).getByRole('button', { name: '质量评估' }))

    await waitFor(() => expect(screen.getByText('完成率')).toBeTruthy())
    expect(screen.getByText('66.7%')).toBeTruthy()
    expect(screen.getByText('33.3%')).toBeTruthy()
    // 平均耗时没有完整起止时间：不写 0 秒，也不编。
    expect(screen.getByText('算不出来')).toBeTruthy()
    expect(screen.getByText(/2 次（人工重试 1 次 · 人工接管 1 次）/)).toBeTruthy()
  })

  it('预算：展示消耗/剩余，保存时把上限原样提交', async () => {
    render(<MyEmployees query="" />)
    const row = await rowOf()

    fireEvent.click(within(row).getByRole('button', { name: '预算' }))

    await waitFor(() => expect(screen.getByText(/本期调用/)).toBeTruthy())
    expect(screen.getByText(/1 \/ 5 次/)).toBeTruthy()
    expect(screen.getAllByText(/不限/).length).toBeGreaterThan(0)

    const input = screen.getByLabelText('调用次数上限') as HTMLInputElement
    fireEvent.change(input, { target: { value: '7' } })
    fireEvent.click(screen.getByRole('button', { name: '保存预算' }))

    await waitFor(() => {
      const put = calls.find((item) => item.init?.method === 'PUT')
      expect(put).toBeTruthy()
      expect(JSON.parse(String(put?.init?.body))).toMatchObject({
        calls_limit: 7,
        tokens_limit: null,
        cost_limit: null,
        period: 'day',
      })
    })
  })

  it('导入：先预览再确认，两步都带同一份配置', async () => {
    render(<MyEmployees query="" />)
    await rowOf()

    fireEvent.change(screen.getByLabelText('员工配置 JSON'), {
      target: { value: '{"employee":{"name":"新人"}}' },
    })
    fireEvent.click(screen.getByRole('button', { name: '预览（不会保存）' }))

    await waitFor(() => expect(screen.getByRole('cell', { name: '新建' })).toBeTruthy())

    fireEvent.click(screen.getByRole('button', { name: '确认导入' }))

    await waitFor(() => expect(screen.getByText(/导入完成：新建 1 位/)).toBeTruthy())
    const confirmed = calls.find(
      (item) => item.url.endsWith('/v1/employees/import') && item.init?.method === 'POST',
    )
    expect(confirmed).toBeTruthy()
    expect(JSON.parse(String(confirmed?.init?.body)).confirm).toBe(true)
  })
})
