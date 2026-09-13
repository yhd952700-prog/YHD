/**
 * 展示层格式化与时间工具。
 *
 * 为什么单独成文件：这些是**非组件**的纯函数/钩子。把它们和组件放在同一个模块里，
 * 会让 Fast Refresh 失效（改了工具函数整个组件文件都要重新挂载，本地状态丢失），
 * 也会让"这个文件到底导出什么"变得含混。组件归 `components/`，工具归这里。
 */

import { useEffect, useState } from 'react'

/** 语义色调。图表、状态点、徽标共用同一套取值，避免每个组件各造一套字符串。 */
export type Tone = 'ok' | 'warn' | 'danger' | 'accent' | 'muted'

/** 时间戳 → 相对时间。超过 7 天显示日期。 */
export function relativeTime(timestamp: number): string {
  if (!timestamp) return '未知时间'
  const seconds = Math.max(0, Date.now() / 1000 - timestamp)
  if (seconds < 60) return '刚刚'
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前`
  if (seconds < 7 * 86400) return `${Math.floor(seconds / 86400)} 天前`
  return new Date(timestamp * 1000).toLocaleDateString('zh-CN')
}

/** 秒 → 人类可读时长。 */
export function humanDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return '—'
  if (seconds < 60) return `${Math.round(seconds)} 秒`
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分 ${Math.round(seconds % 60)} 秒`
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  if (hours < 24) return `${hours} 小时 ${minutes} 分`
  return `${Math.floor(hours / 24)} 天 ${hours % 24} 小时`
}

/**
 * 走动的当前时刻（毫秒）。
 *
 * 为什么需要它：在渲染期间直接调 `Date.now()` 是不纯的 —— 同一个 props 会渲染出
 * 不同结果，React 的纯度检查会拦下来。正确做法是让**时间成为状态**，由定时器从
 * 外部世界推进（这也是 effect 的正当用途：同步一个外部系统）。
 */
export function useNow(intervalMs = 30_000): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), intervalMs)
    return () => window.clearInterval(timer)
  }, [intervalMs])
  return now
}

/** 语义色调 → CSS 变量后缀。`muted` 落在 `--os-text-faint` 上。 */
export function toneVar(tone: Tone): string {
  return tone === 'muted' ? 'text-faint' : tone
}

/** 审计事件类型 → 中文标签（未知类型原样显示，不编造）。 */
const EVENT_LABELS: Record<string, string> = {
  access_allowed: '访问放行',
  access_denied: '访问拒绝',
  access_check: '访问校验',
  policy_eval: '策略评估',
  human_sovereignty_override: '人类主权',
  kernel_status: '内核状态',
  kernel_implementation: '内核实现',
  phase_gate: '阶段门',
  state_change: '状态变更',
  role_grant: '角色授予',
  role_revoke: '角色撤销',
  permission_check: '权限校验',
  conditional_access: '条件放行',
  deferred_access: '延后处理',
}

export function eventLabel(type: string): string {
  return EVENT_LABELS[type] ?? type
}

/** 审计 outcome → 中文标签（未知原样显示）。 */
const OUTCOME_LABELS: Record<string, string> = {
  allow: '通过',
  allowed: '通过',
  deny: '拒绝',
  denied: '拒绝',
  conditional: '有条件',
  defer: '延后',
  granted: '已授权',
  revoked: '已撤销',
  login_success: '登录成功',
  login_denied: '登录失败',
  logout: '登出',
}

export function outcomeLabel(outcome: string): string {
  return OUTCOME_LABELS[outcome] ?? outcome
}

export function outcomeTone(outcome: string): Tone {
  if (outcome === 'allow' || outcome === 'allowed' || outcome === 'granted') return 'ok'
  if (outcome === 'deny' || outcome === 'denied' || outcome === 'login_denied') return 'danger'
  if (outcome === 'defer' || outcome === 'conditional') return 'warn'
  if (outcome === 'login_success') return 'accent'
  return 'muted'
}
