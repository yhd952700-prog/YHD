/**
 * 错误边界 —— 让一个渲染异常不再等于整棵 React 树白屏。
 *
 * 为什么必须有（这不是"锦上添花的兜底"）
 * --------------------------------------
 * React 的默认行为是：任何渲染期异常都会**卸载整棵树**，`#root` 里什么都不剩。
 * 全仓此前没有 `ErrorBoundary` / `componentDidCatch`，于是：
 *
 *   * `Operations.tsx` 渲染一个后端从不发送的字段 → `formatCompact(undefined)`
 *     抛 `TypeError` → **整个驾驶舱白屏**（已修，但根因是缺少边界）；
 *   * 任何一个页面新增的展示分支出错 → 所有页面都看不到，包括那些本来完好的；
 *   * 崩溃往往正好发生在**最需要观测**的时刻（审计链断裂、provider 异常），
 *     而白屏让人连"系统出问题了"这件事都看不到 —— 一个人类主权主体在系统故障时
 *     失去了界面。
 *
 * 三条设计约束
 * ------------
 * 1. **说真话**：显示真实的错误类型与消息，不写"出错了，请重试"这种无信息文案。
 *    用户要能判断这是自己的操作问题还是系统问题。
 * 2. **能恢复且不丢上下文**：重试是重新渲染子树（清掉错误状态），**不是刷新页面**。
 *    刷新会丢掉会话与正在进行的操作 —— 那不是恢复，是从头再来。
 * 3. **报告出去**：错误进 console.error，带上边界标签。否则就是静默失败。
 */

import { Component } from 'react'
import type { ErrorInfo, ReactNode } from 'react'
import { IconAlert, IconRefresh } from './icons'

/**
 * 把任意被抛出的值变成一句人能读的话。
 *
 * 为什么不能直接 `err.message`：被抛出的东西未必是 `Error`（`throw undefined`、
 * rejection 一个字符串、第三方回调抛对象都真实存在）。`err?.message` 在这些情况下
 * 是 `undefined`，界面会显示字符串 "undefined" —— 那不是信息，是更坏的噪声。
 */
export function describeRenderError(thrown: unknown): string {
  if (thrown instanceof Error) {
    return thrown.message ? `${thrown.name}: ${thrown.message}` : thrown.name
  }
  if (typeof thrown === 'string' && thrown.trim()) return thrown
  if (thrown === null) return 'null（代码里 throw 了 null，没有任何错误信息）'
  if (thrown === undefined) {
    return 'undefined（代码里 throw 了 undefined，没有任何错误信息）'
  }
  try {
    const text = JSON.stringify(thrown)
    // 循环引用会让 JSON.stringify 抛；对象/数字也要能被读出来。
    return text && text !== '{}' ? text : `无法识别的错误值：${String(thrown)}`
  } catch {
    return `无法识别的错误值：${String(thrown)}`
  }
}

interface Props {
  children: ReactNode
  /**
   * 这个边界罩住了哪一块界面。会显示在错误面板上，让用户立刻知道该去看哪里 ——
   * 「整页坏了」和「审计面板坏了」的排查方向完全不同。
   */
  label: string
  /** 自定义兜底 UI（完全接管渲染）。默认渲染可操作的错误面板。 */
  fallback?: ReactNode
}

interface State {
  error: unknown
  caught: boolean
}

/**
 * React 目前没有 hooks 版的错误边界 —— 官方唯一支持的实现仍是 class 组件 +
 * `componentDidCatch`。这不是技术选型偏好，是 React 的硬约束。
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null, caught: false }

  static getDerivedStateFromError(error: unknown): State {
    return { error, caught: true }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // 报告出去，而不是静默失败。没有这一步，一个崩溃在控制台里不留任何痕迹。
    console.error(
      `[console] 渲染异常，被「${this.props.label}」边界兜住：`,
      error,
      info.componentStack,
    )
  }

  /** 重新渲染子树：清掉错误状态即可，不刷新页面（刷新会丢会话与操作）。 */
  private readonly retry = (): void => {
    this.setState({ error: null, caught: false })
  }

  render(): ReactNode {
    if (!this.state.caught) return this.props.children

    if (this.props.fallback !== undefined) return this.props.fallback

    return (
      <div className="os-crash" role="alert" data-testid="error-boundary">
        <div className="os-crash-head">
          <IconAlert size={15} />
          <strong>「{this.props.label}」这一块没能显示出来</strong>
        </div>
        <p className="os-crash-message">{describeRenderError(this.state.error)}</p>
        <p className="os-crash-note">
          这是这一块的显示问题，不是整台系统的结论：其它页面仍可用。点「重试」会重新
          渲染这一块（不刷新页面，不会丢失登录与正在进行的操作）。
        </p>
        <button className="os-link" onClick={this.retry} style={{ marginTop: 4 }} type="button">
          <IconRefresh size={12} /> 重试
        </button>
      </div>
    )
  }
}
