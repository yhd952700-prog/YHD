/**
 * 共享 UI 原子组件。
 *
 * 三个约定，贯穿所有页面：
 * 1. `Card` 只负责外壳；标题右侧的 `action` 由调用方给，不预设按钮。
 * 2. `Guard` 统一处理"加载中 / 失败 / 空"三种非正常态，避免每个面板各写一套
 *    而在某些路径上漏掉失败分支（漏掉就等于把错误静默成空数据）。
 * 3. 失败态一律显示后端给的真实原因，不吞成"暂无数据"。
 *
 * 非组件的格式化函数在 `lib/format.ts` —— 混在本文件里会让 Fast Refresh 失效。
 */

import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { isLocalServiceOffline } from '../lib/errorMessage'
import type { Tone } from '../lib/format'
import { toneVar } from '../lib/format'
import { ErrorBoundary } from './ErrorBoundary'
import { IconAlert, IconRefresh } from './icons'

export function Card({
  title,
  sub,
  action,
  span = 6,
  className,
  id,
  children,
}: {
  title: string
  sub?: string
  action?: ReactNode
  span?: 3 | 4 | 5 | 6 | 7 | 8 | 12
  /** 额外状态类（如 sc-panel-active / sc-hud），只给当前焦点容器用。 */
  className?: string
  /** 锚点 id，供页面内跳转定位（不参与样式）。 */
  id?: string
  children: ReactNode
}) {
  return (
    <section id={id} className={`os-card os-span-${span}${className ? ` ${className}` : ''}`}>
      <div className="os-card-head">
        <div>
          <h2 className="os-card-title">{title}</h2>
          {sub && <p className="os-card-sub">{sub}</p>}
        </div>
        {action}
      </div>
      {/* 每个 Card 自带一道边界。这不是可有可无的包装：
          没有它，一个面板的渲染异常会卸载**整棵 React 树**，于是"审计面板里的
          一个字段缺失"会连带把总览、导航、登录态界面全部变成白屏 —— 一个人类
          主体在系统出问题时看不到任何东西。边界把它降级成"这一块坏了，其余照常"，
          并在原地显示真实的错误消息与重试入口。 */}
      <div className="os-card-body">
        <ErrorBoundary label={title}>{children}</ErrorBoundary>
      </div>
    </section>
  )
}

export function Loading({ rows = 3 }: { rows?: number }) {
  // 只有骨架没有文字时，慢请求看起来和"卡死"完全一样：三秒后界面一动不动，
  // 用户没有任何依据判断该继续等还是该去检查服务。这里先给一句"在做什么"，
  // 超过 3 秒再补一句"为什么慢、这时能做什么"。
  const [slow, setSlow] = useState(false)
  useEffect(() => {
    const timer = window.setTimeout(() => setSlow(true), 3000)
    return () => window.clearTimeout(timer)
  }, [])
  return (
    <div role="status" aria-busy="true" aria-live="polite" style={{ display: 'flex', flexDirection: 'column', gap: 9 }}>
      <p className="os-hint" style={{ margin: 0 }}>
        {slow
          ? '还在读取。本地服务较忙时会慢一些；如果一直停在这里，请确认鎏灏正在运行，或回到上一页再进来。'
          : '正在读取…'}
      </p>
      {Array.from({ length: rows }, (_, index) => (
        <div key={index} className="os-skeleton" style={{ height: index === 0 ? 22 : 38 }} />
      ))}
    </div>
  )
}

export function Failure({ message, onRetry }: { message: string; onRetry?: () => void }) {
  // 标题必须说对对象：服务没起来却写"数据不可用"，用户会去怀疑数据本身，
  // 而真正该做的是把鎏灏打开。文案里已经带了对象与动作，标题与它保持一致。
  const offline = isLocalServiceOffline(message)
  return (
    <div className="os-empty" role="alert">
      <IconAlert size={17} />
      <strong>{offline ? '本地服务未运行' : '数据不可用'}</strong>
      <span>{message}</span>
      {onRetry && (
        <button className="os-link" onClick={onRetry} style={{ marginTop: 4 }}>
          <IconRefresh size={12} /> 重试
        </button>
      )}
    </div>
  )
}

export function Empty({
  title,
  hint,
  action,
}: {
  title: string
  hint?: string
  /** 空态同样需要出路：没有内容的页面也得能让用户重读一次。 */
  action?: ReactNode
}) {
  return (
    <div className="os-empty" role="status">
      <strong>{title}</strong>
      {hint && <span>{hint}</span>}
      {action}
    </div>
  )
}

/**
 * 统一处理三种非正常态。
 *
 * `children` 只在数据就绪时渲染 —— 这样页面里不需要写 `data!` 之类断言。
 */
export function Guard<T>({
  loading,
  error,
  data,
  isEmpty,
  emptyTitle,
  emptyHint,
  onRetry,
  rows,
  children,
}: {
  loading: boolean
  error: string | null
  data: T | null
  isEmpty?: (data: T) => boolean
  emptyTitle?: string
  emptyHint?: string
  onRetry?: () => void
  rows?: number
  children: (data: T) => ReactNode
}) {
  if (error) return <Failure message={error} onRetry={onRetry} />

  // 空态一律要给出路：只说「暂无数据」等于让用户自己猜是没数据、没读到、
  // 还是页面坏了。没有内容时至少给一句解释 + 一个可点的重试。
  const retryAction = onRetry ? (
    <button className="os-link" onClick={onRetry} style={{ marginTop: 4 }} type="button">
      <IconRefresh size={12} /> 重试
    </button>
  ) : undefined

  if (data === null) {
    return loading ? (
      <Loading rows={rows} />
    ) : (
      <Empty
        title="暂无数据"
        hint="这一块还没有读到内容。点「重试」再读一次；如果刚打开这一页，稍等片刻也可能自行出现。"
        action={retryAction}
      />
    )
  }
  if (isEmpty?.(data)) {
    return (
      <Empty
        title={emptyTitle ?? '暂无数据'}
        hint={
          emptyHint ??
          '如果这里本该有内容，说明读取结果为空或还没回来；点「重试」重新读一次。'
        }
        action={emptyHint ? undefined : retryAction}
      />
    )
  }
  return <>{children(data)}</>
}

/**
 * 402 预算拒绝的可执行出口：只给一句话不够，必须能点。
 *
 * 路由入口由调用方注入，组件本身不依赖导航实现，页面与聊天面板可以共用。
 */
export function BudgetDenialActions({
  onOpenUsage,
  onConfigureModel,
}: {
  onOpenUsage: () => void
  onConfigureModel: () => void
}) {
  return (
    <div style={{ display: 'flex', gap: 12, marginTop: 8, flexWrap: 'wrap' }}>
      <button type="button" className="os-link" onClick={onOpenUsage}>
        查看用量
      </button>
      <button type="button" className="os-link" onClick={onConfigureModel}>
        换更便宜的模型
      </button>
    </div>
  )
}

/** 带颜色的状态点 + 文案。 */
export function Dot({ tone, label }: { tone: Tone; label: ReactNode }) {
  return (
    <span className="os-legend-item">
      <span className="os-dot" style={{ background: `var(--os-${toneVar(tone)})` }} />
      {label}
    </span>
  )
}
