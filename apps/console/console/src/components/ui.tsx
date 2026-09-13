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

import type { ReactNode } from 'react'
import type { Tone } from '../lib/format'
import { toneVar } from '../lib/format'
import { IconAlert, IconRefresh } from './icons'

export function Card({
  title,
  sub,
  action,
  span = 6,
  children,
}: {
  title: string
  sub?: string
  action?: ReactNode
  span?: 3 | 4 | 5 | 6 | 7 | 8 | 12
  children: ReactNode
}) {
  return (
    <section className={`os-card os-span-${span}`}>
      <div className="os-card-head">
        <div>
          <h2 className="os-card-title">{title}</h2>
          {sub && <p className="os-card-sub">{sub}</p>}
        </div>
        {action}
      </div>
      <div className="os-card-body">{children}</div>
    </section>
  )
}

export function Loading({ rows = 3 }: { rows?: number }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 9 }}>
      {Array.from({ length: rows }, (_, index) => (
        <div key={index} className="os-skeleton" style={{ height: index === 0 ? 22 : 38 }} />
      ))}
    </div>
  )
}

export function Failure({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="os-empty">
      <IconAlert size={17} />
      <strong>数据不可用</strong>
      <span>{message}</span>
      {onRetry && (
        <button className="os-link" onClick={onRetry} style={{ marginTop: 4 }}>
          <IconRefresh size={12} /> 重试
        </button>
      )}
    </div>
  )
}

export function Empty({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="os-empty">
      <strong>{title}</strong>
      {hint && <span>{hint}</span>}
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
  if (data === null) return loading ? <Loading rows={rows} /> : <Empty title="暂无数据" />
  if (isEmpty?.(data)) {
    return <Empty title={emptyTitle ?? '暂无数据'} hint={emptyHint} />
  }
  return <>{children(data)}</>
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
