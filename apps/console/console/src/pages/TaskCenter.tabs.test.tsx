/**
 * 任务中心标签：默认标签正确、点另一标签切换内容。
 * 只测标签切换；任务列表本身的回归在 TaskCenter.test.tsx。
 *
 * @vitest-environment jsdom
 */

import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { TaskCenter } from './TaskCenter'

function reply(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('任务中心标签', () => {
  beforeEach(() => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const path = String(input).split('?')[0]
        // Goals.tsx 直接读 goals.data.goals.length：空对象会崩，给真形状。
        if (path === '/v1/goals') return reply({ goals: [] })
        if (path === '/v1/projects') return reply({ projects: [] })
        return reply({})
      }),
    )
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  test('默认打开任务中心标签', async () => {
    render(<TaskCenter query="" />)

    expect(screen.getByRole('tab', { name: '任务中心' }).getAttribute('aria-selected')).toBe(
      'true',
    )
    // 台账接口返回空对象时如实说暂未提供（不是假空表）；任务列表卡片
    // 与台账迁移卡片各有一处「暂未提供」，所以这里只断言卡片标题唯一。
    await screen.findByText('任务列表')
    expect(screen.getAllByText(/暂未提供/).length).toBeGreaterThan(0)
  })

  test('点工作流切换内容，任务列表消失', async () => {
    render(<TaskCenter query="" />)
    await screen.findByText('任务列表')

    fireEvent.click(screen.getByRole('tab', { name: '工作流' }))

    expect(screen.getByRole('tab', { name: '工作流' }).getAttribute('aria-selected')).toBe(
      'true',
    )
    await screen.findByText('运行实例')
    expect(screen.queryByText('任务列表')).toBeNull()
  })

  test('点目标与任务、项目各切换到对应内容', async () => {
    render(<TaskCenter query="" />)
    await screen.findByText('任务列表')

    fireEvent.click(screen.getByRole('tab', { name: '目标与任务' }))
    await screen.findByText('目标列表')

    fireEvent.click(screen.getByRole('tab', { name: '项目' }))
    await screen.findByText('新建项目')
  })

  test('initialTab=goals 直接打开目标与任务标签', async () => {
    render(<TaskCenter query="" initialTab="goals" />)

    expect(screen.getByRole('tab', { name: '目标与任务' }).getAttribute('aria-selected')).toBe(
      'true',
    )
    await screen.findByText('目标列表')
  })
})
