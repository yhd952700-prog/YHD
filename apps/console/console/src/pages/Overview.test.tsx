/**
 * 总览页"第一次用"引导回归。
 *
 * 全新环境（没有真实模型 / 还没有员工）首屏必须直接给出下一步入口，
 * 而不是让用户自己对着一屏 KPI 猜。
 */

import { afterEach, describe, expect, test, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { Overview } from './Overview'
import { UNKNOWN_PROVIDER } from '../lib/providerStatus'

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

function roster(employees: number) {
  return {
    available: true,
    totals: { kernels: 0, layers: 0, registry_entries: 0, employees },
    kernels: [],
    layers: [],
    domains: [],
    bands: [],
    providers: {
      registered_count: 0,
      configured_count: 0,
      active: {},
      items: [],
    },
  }
}

function mockDashboard(employeeCount: number) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input)
      if (path.startsWith('/v1/dashboard/roster')) return json(roster(employeeCount))
      if (path.startsWith('/v1/dashboard/activity')) return json({ activity: [] })
      if (path.startsWith('/v1/dashboard/summary')) {
        return json({ sessions: { count: 0 }, audit: { total_events: 0 } })
      }
      if (path.startsWith('/v1/dashboard/analytics')) {
        // 形状必须与 `AnalyticsPayload` 一致：Overview 会直接读
        // `timeseries.error` / `breakdown.error`，缺字段会在渲染时抛异常。
        return json({
          timeseries: { available: false, error: '还没有足够的审计事件', days: [] },
          breakdown: { available: false, error: '还没有审计事件', items: [] },
        })
      }
      if (path === '/v1/ready') return json({ checks: {} })
      return json({})
    }),
  )
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('总览第一次用引导', () => {
  test('没有真实模型、还没有员工时，首屏给出三个可点入口', async () => {
    mockDashboard(0)
    const onNavigate = vi.fn()
    render(<Overview query="" provider={UNKNOWN_PROVIDER} onNavigate={onNavigate} />)

    expect(await screen.findByLabelText('第一次使用')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '配置模型' }))
    fireEvent.click(screen.getByRole('button', { name: '创建第一位员工' }))
    fireEvent.click(screen.getByRole('button', { name: '导入第一份知识' }))
    fireEvent.click(screen.getByRole('button', { name: '「鎏灏管家」' }))

    expect(onNavigate.mock.calls.map((call) => call[0])).toEqual([
      'settings',
      'my-employees',
      'knowledge',
      'butler',
    ])
  })

  test('模型与员工都就绪后不再打扰', async () => {
    mockDashboard(1)
    render(
      <Overview
        query=""
        provider={{ ...UNKNOWN_PROVIDER, isReal: true }}
        onNavigate={vi.fn()}
      />,
    )

    await screen.findByText('AI 员工总数')
    expect(screen.queryByLabelText('第一次使用')).toBeNull()
    expect(screen.queryByRole('button', { name: '配置模型' })).toBeNull()
  })

  test('首屏数据还没回来时显示“正在检查”，不提前报“当前无异常”', () => {
    // 所有接口都挂着：数据没回来，就不能给“无异常”这种结论。
    vi.stubGlobal(
      'fetch',
      vi.fn(() => new Promise<Response>(() => {})),
    )
    render(<Overview query="" provider={UNKNOWN_PROVIDER} onNavigate={vi.fn()} />)

    expect(screen.getByText('正在检查依赖与近期失败…')).toBeTruthy()
    expect(screen.queryByText(/当前无异常/)).toBeNull()
  })
})
