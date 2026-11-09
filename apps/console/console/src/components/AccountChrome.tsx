/**
 * 账号面的公共装饰件：桩徽标、只读横幅、一次性凭据展示。
 *
 * 只使用既有 `--os-*` 令牌与字号，不引入第二套配色；品牌元素未改一个字。
 */

import { useState } from 'react'
import type { DataSource } from '../lib/accounts'
import { IconAlert, IconLock } from './icons'

/** 桩徽标：只要数据来自契约桩，就必须显示，避免被误读成真实数据。 */
export function StubBadge({ source }: { source: DataSource }) {
  if (source !== 'stub') return null
  return (
    <span
      role="status"
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        padding: '3px 9px',
        borderRadius: 999,
        border: '1px solid color-mix(in srgb, var(--os-warn) 55%, transparent)',
        color: 'var(--os-warn)',
        background: 'color-mix(in srgb, var(--os-warn) 12%, transparent)',
        fontSize: 12,
        fontWeight: 600,
      }}
    >
      <IconAlert size={12} /> 本地契约桩 · 待联调
    </span>
  )
}

/**
 * 只读查看横幅：进入即显眼，退出入口固定在同一位置。
 *
 * 服务端尚无"生效视角"端点（IA C1），因此这里**不宣称**已切到对方的数据：
 * 明确写出"仍是当前账号的数据"，避免把只读开关误读成真实视角切换。
 */
export function ReadOnlyBanner({
  principal,
  onExit,
}: {
  principal: string
  onExit: () => void
}) {
  return (
    <div
      role="status"
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: 12,
        marginBottom: 14,
        padding: '10px 14px',
        borderRadius: 10,
        border: '1px solid color-mix(in srgb, var(--os-warn) 50%, transparent)',
        background: 'color-mix(in srgb, var(--os-warn) 10%, var(--os-panel))',
        color: 'var(--os-text)',
        fontSize: 13,
      }}
    >
      <span style={{ display: 'inline-flex', flexDirection: 'column', gap: 2 }}>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
          <IconLock size={15} />
          只读查看 <strong>{principal}</strong> · 写操作已禁用
        </span>
        <span style={{ fontSize: 12, color: 'var(--os-text-dim)' }}>
          视角端点尚未上线；当前页面仍显示你自己的数据，不会假装已切换到对方。
        </span>
      </span>
      <button
        className="os-btn os-btn-ghost"
        onClick={onExit}
        style={{ width: 'auto', padding: '4px 10px' }}
      >
        退出只读
      </button>
    </div>
  )
}

/** 一次性凭据（激活码 / 恢复码）：只展示一次，提供复制。 */
export function OneTimeSecret({
  label,
  value,
  notice,
  onDone,
  doneLabel = '我已妥善保存',
}: {
  label: string
  value: string
  notice?: string
  onDone?: () => void
  doneLabel?: string
}) {
  // 三态：未复制 / 已复制 / 复制失败（Clipboard API 在非安全来源不可用时要明说）。
  const [copyState, setCopyState] = useState<'idle' | 'ok' | 'fail'>('idle')
  const copy = () => {
    if (!navigator.clipboard?.writeText) {
      setCopyState('fail')
      return
    }
    navigator.clipboard.writeText(value).then(
      () => setCopyState('ok'),
      () => setCopyState('fail'),
    )
  }
  return (
    <div className="os-note" style={{ alignItems: 'flex-start', flexDirection: 'column' }}>
      <strong style={{ fontSize: 13 }}>{label}（只显示这一次）</strong>
      <code
        style={{
          width: '100%',
          padding: '8px 10px',
          borderRadius: 8,
          background: 'var(--os-panel-2)',
          border: '1px solid color-mix(in srgb, var(--os-line, var(--os-text-dim)) 30%, transparent)',
          fontSize: 14,
          letterSpacing: '0.04em',
          wordBreak: 'break-all',
        }}
      >
        {value}
      </code>
      {notice && <span style={{ fontSize: 12, color: 'var(--os-text-dim)' }}>{notice}</span>}
      <div style={{ display: 'flex', gap: 8, marginTop: 2 }}>
        <button type="button" className="os-btn" style={{ width: 'auto' }} onClick={copy}>
          {copyState === 'ok' ? '已复制' : copyState === 'fail' ? '复制失败，请手动选中' : '复制'}
        </button>
        {onDone && (
          <button type="button" className="os-btn os-btn-ghost" onClick={onDone}>
            {doneLabel}
          </button>
        )}
      </div>
    </div>
  )
}
