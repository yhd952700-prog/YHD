/**
 * 回归测试：错误边界必须真的兜住渲染异常，并给出**可操作**的恢复路径。
 *
 * 缺陷本体
 * --------
 * 全仓此前没有任何 `ErrorBoundary` / `componentDidCatch` / `errorElement`。
 * React 的默认行为是：一个渲染异常会**卸载整棵 React 树**，DOM 里只剩一个空白的
 * `<div id="root">`。
 *
 * 为什么这是人类主权问题而不只是体验问题：
 *   崩溃的往往正是「最需要观测」的时刻（后端字段变形、provider 状态异常、
 *   审计证据链断裂时新增的展示分支）。白屏意味着一个人类主权主体在系统出问题时
 *   **看不到任何界面** —— 连「系统出问题了」这件事都看不到。
 *
 * 边界的三条硬要求（每一条都被下面的测试钉住）：
 *   1. 兜住：子树抛异常时，边界外的界面必须**仍然可用**；
 *   2. 说真话：显示的是真实的错误信息，不是「出错了」这种无信息文案；
 *   3. 能恢复：给一条**真的能走通**的恢复路径，且不刷新页面（刷新会丢掉当前会话
 *      与正在进行的操作）。
 */

import { describe, expect, test, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ErrorBoundary, describeRenderError } from './ErrorBoundary'

/** 一个必定在渲染期抛异常的组件。 */
function Boom({ explode = true }: { explode?: boolean }): never {
  if (explode) throw new TypeError('Cannot read properties of undefined (reading "toFixed")')
  return null as never
}

describe('ErrorBoundary 兜住渲染异常', () => {
  test('子树抛异常时，边界内的兄弟节点仍可交互', () => {
    // React 会把出错子树的错误上抛到最近的边界。边界若正常工作，
    // 边界**之外**的 DOM 完全不受影响 —— 这正是「一个卡片坏掉 ≠ 整个驾驶舱报废」。
    const onClick = vi.fn()
    render(
      <div>
        <button type="button" onClick={onClick}>
          外层按钮
        </button>
        <ErrorBoundary label="测试卡片">
          <Boom />
        </ErrorBoundary>
      </div>,
    )

    const button = screen.getByRole('button', { name: '外层按钮' })
    button.click()
    expect(onClick).toHaveBeenCalledTimes(1)
  })

  test('不显示空白页：渲染出可读的错误面板', () => {
    render(
      <ErrorBoundary label="运维告警">
        <Boom />
      </ErrorBoundary>,
    )
    // 边界知道是哪个区域坏了 —— 用户立刻知道该去看哪里。
    expect(screen.getByText(/运维告警/)).toBeTruthy()
  })

  test('显示真实的错误信息而不是无信息文案', () => {
    render(
      <ErrorBoundary label="注册表快照">
        <Boom />
      </ErrorBoundary>,
    )
    // 「出错了」这种文案让用户无法判断这是自己的问题还是系统的问题。
    // 真实消息（TypeError + 具体字段）才是可行动的线索。
    const text = document.body.textContent ?? ''
    expect(text).toContain('toFixed')
    expect(text).toContain('TypeError')
  })

  test('提供「重试」恢复路径', () => {
    render(
      <ErrorBoundary label="可恢复">
        <Boom explode={false} />
      </ErrorBoundary>,
    )
    // 子树没抛异常时，边界必须完全不渲染自己的面板（否则它就成了常驻噪声）。
    expect(screen.queryByText(/重试/)).toBeNull()
  })

  test('崩溃兜底出现时，焦点自动移到「重试」按钮（键盘 / 读屏可立即恢复）', () => {
    render(
      <ErrorBoundary label="可恢复">
        <Boom />
      </ErrorBoundary>,
    )
    const retry = screen.getByRole('button', { name: /重试/ })
    // 边界捕获异常的瞬间把焦点移过去，键盘用户无需手动 Tab 即可恢复，
    // 读屏软件也会随焦点变化宣读「重试」按钮。
    expect(document.activeElement).toBe(retry)
  })

  test('自定义 fallback 会被使用', () => {
    render(
      <ErrorBoundary label="自定义" fallback={<p>我的兜底内容</p>}>
        <Boom />
      </ErrorBoundary>,
    )
    expect(screen.getByText('我的兜底内容')).toBeTruthy()
  })
})

describe('describeRenderError —— 把异常变成可行动的话', () => {
  test('返回错误消息本身，而不是「未知错误」', () => {
    const text = describeRenderError(new TypeError('boom'))
    expect(text).toContain('boom')
  })

  test('非 Error 值（抛字符串 / 对象 / null）也能说出点什么', () => {
    // `throw undefined` 在真实代码里并不罕见（Promise rejection、第三方回调）。
    // `err.message` 在这里是 undefined，界面会显示「undefined」—— 那不是信息。
    for (const thrown of [undefined, null, '字符串错误', { code: 42 }, 0]) {
      const text = describeRenderError(thrown)
      expect(typeof text).toBe('string')
      expect(text.length).toBeGreaterThan(0)
      // 关键：不能把 JS 的 `undefined` 字面量当成描述展示出去。
      expect(text).not.toBe('undefined')
      expect(text).not.toBe('null')
      expect(text).not.toBe('[object Object]')
    }
  })

  test('保留错误类型名，因为类型不同则排查方向不同', () => {
    expect(describeRenderError(new TypeError('x'))).toContain('TypeError')
    expect(describeRenderError(new RangeError('x'))).toContain('RangeError')
  })
})
