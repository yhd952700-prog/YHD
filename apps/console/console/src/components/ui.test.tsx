/**
 * 回归测试：`components/ui.tsx` 的 `Guard` 状态机契约。
 *
 * 为什么测它：`Guard` 是控制台所有面板统一的「加载中 / 失败 / 空 / 数据」四态入口，
 * 全仓十几个页面都靠它避免"漏掉失败分支把错误静默成空数据"。但它此前零测试，
 * 一旦有人改坏 `if (error) / if (data === null)` 的优先级，整站错误态都会退化。
 * 这里把四种态钉死。
 *
 * 同时印证：页面层（Apps/Goals/Projects）已移除 Guard 之外手写的
 * `apps.error && <p>` 重复渲染 —— 错误只应由 Guard 的 `Failure` 呈现一次。
 */

import { describe, expect, test, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { Guard } from './ui'

describe('Guard 四态契约', () => {
  test('error 为真 → 渲染 Failure，且带可重试入口', () => {
    const onRetry = vi.fn()
    render(
      <Guard loading={false} error="审计链断裂，请检查 AUDIT_DB_PATH" data={null} onRetry={onRetry}>
        {() => <span>不应出现</span>}
      </Guard>,
    )
    // Failure 的标题与真实后端原因都出现。
    expect(screen.getByText('数据不可用')).toBeTruthy()
    expect(screen.getByText('审计链断裂，请检查 AUDIT_DB_PATH')).toBeTruthy()
    // 错误时不应渲染数据子树。
    expect(screen.queryByText('不应出现')).toBeNull()
    // 重试入口是按钮，可点击。
    const retry = screen.getByRole('button', { name: /重试/ })
    retry.click()
    expect(onRetry).toHaveBeenCalledTimes(1)
  })

  test('error 为空串 → 不当作错误（不渲染 Failure）', () => {
    // useApi 把 error 置为 null 或真实消息，空串理论上不会来，但状态机须稳健。
    render(
      <Guard loading={false} error={''} data={null}>
        {() => <span>fallback</span>}
      </Guard>,
    )
    expect(screen.getByText('暂无数据')).toBeTruthy()
    expect(screen.queryByText('数据不可用')).toBeNull()
  })

  test('loading 且 data 为 null → 渲染 Loading 骨架，不渲染数据', () => {
    const { container } = render(
      <Guard loading error={null} data={null}>
        {() => <span>数据内容</span>}
      </Guard>,
    )
    expect(container.querySelector('.os-skeleton')).toBeTruthy()
    expect(screen.queryByText('数据内容')).toBeNull()
  })

  test('有 data 且 isEmpty 为真 → 渲染 Empty（用 emptyTitle），不渲染数据', () => {
    render(
      <Guard
        loading={false}
        error={null}
        data={[] as unknown as { length: number }}
        isEmpty={(d: { length: number }) => d.length === 0}
        emptyTitle="暂无应用"
        emptyHint="系统启动时若未注册任何内置插件。"
      >
        {() => <span>数据内容</span>}
      </Guard>,
    )
    expect(screen.getByText('暂无应用')).toBeTruthy()
    expect(screen.getByText('系统启动时若未注册任何内置插件。')).toBeTruthy()
    expect(screen.queryByText('数据内容')).toBeNull()
  })

  test('有 data 且非 empty → 渲染 children', () => {
    render(
      <Guard
        loading={false}
        error={null}
        data={[1]}
        isEmpty={(d: number[]) => d.length === 0}
      >
        {() => <span>真实内容</span>}
      </Guard>,
    )
    expect(screen.getByText('真实内容')).toBeTruthy()
  })

  test('error 优先于 loading：二者同时在场时显示失败而非骨架', () => {
    const { container } = render(
      <Guard loading error="后端 500" data={null} onRetry={() => {}}>
        {() => <span>x</span>}
      </Guard>,
    )
    expect(screen.getByText('后端 500')).toBeTruthy()
    // 失败态不应再画骨架。
    expect(container.querySelector('.os-skeleton')).toBeNull()
  })
})

describe('状态组件的无障碍角色', () => {
  test('Failure 标记 role=alert，错误能被读屏软件播报', () => {
    const { container } = render(
      <Guard loading={false} error="审计链断裂" data={null} onRetry={() => {}}>
        {() => <span>x</span>}
      </Guard>,
    )
    const alert = container.querySelector('[role="alert"]')
    expect(alert).toBeTruthy()
    expect(alert?.textContent).toContain('审计链断裂')
  })

  test('Loading 标记 role=status 且 aria-busy=true', () => {
    const { container } = render(
      <Guard loading error={null} data={null}>
        {() => <span>x</span>}
      </Guard>,
    )
    const status = container.querySelector('[role="status"]')
    expect(status).toBeTruthy()
    expect(status?.getAttribute('aria-busy')).toBe('true')
  })

  test('Empty 标记 role=status', () => {
    const { container } = render(
      <Guard
        loading={false}
        error={null}
        data={[] as unknown as { length: number }}
        isEmpty={(d: { length: number }) => d.length === 0}
        emptyTitle="暂无应用"
      >
        {() => <span>x</span>}
      </Guard>,
    )
    const status = container.querySelector('[role="status"]')
    expect(status).toBeTruthy()
    expect(status?.textContent).toContain('暂无应用')
  })
})
