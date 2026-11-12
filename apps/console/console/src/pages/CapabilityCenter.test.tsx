/**
 * 能力中心合并页：默认标签正确、点另一标签切换内容。
 *
 * @vitest-environment jsdom
 */

import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { CapabilityCenter } from './CapabilityCenter'

function reply(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

function installFetch() {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input).split('?')[0]
      if (path === '/v1/dashboard/roster') {
        return reply({
          totals: { employees: 0, kernels: 0, layers: 0, registry_entries: 0 },
          layers: [],
          kernels: [],
          bands: [],
          domains: [],
          items: [],
          registered_count: 0,
        })
      }
      if (path === '/v1/plugins') return reply({ plugins: [], count: 0 })
      return reply({})
    }),
  )
}

describe('能力中心', () => {
  beforeEach(() => {
    installFetch()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  test('默认打开技能中心标签', async () => {
    render(<CapabilityCenter query="" />)

    expect(screen.getByRole('tab', { name: '技能中心' }).getAttribute('aria-selected')).toBe(
      'true',
    )
    await screen.findByText('技能列表')
  })

  test('点工具中心切换内容，技能列表消失', async () => {
    render(<CapabilityCenter query="" />)
    await screen.findByText('技能列表')

    fireEvent.click(screen.getByRole('tab', { name: '工具中心' }))

    expect(screen.getByRole('tab', { name: '工具中心' }).getAttribute('aria-selected')).toBe(
      'true',
    )
    await screen.findByText('本地工具')
    expect(screen.queryByText('技能列表')).toBeNull()
  })

  test('点能力注册表、业务中心、应用各切换到对应内容', async () => {
    render(<CapabilityCenter query="" />)
    await screen.findByText('技能列表')

    fireEvent.click(screen.getByRole('tab', { name: '能力注册表' }))
    await screen.findByText('大脑地图')

    fireEvent.click(screen.getByRole('tab', { name: '业务中心' }))
    await screen.findByText('业务能力清单')

    fireEvent.click(screen.getByRole('tab', { name: '应用' }))
    await screen.findByText('应用列表')
  })

  test('initialTab=apps 直接打开应用标签', async () => {
    render(<CapabilityCenter query="" initialTab="apps" />)

    expect(screen.getByRole('tab', { name: '应用' }).getAttribute('aria-selected')).toBe('true')
    await screen.findByText('应用列表')
    expect(screen.queryByText('技能列表')).toBeNull()
  })
})
