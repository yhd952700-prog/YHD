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

/** 时间戳 → 相对时间。超过 7 天显示日期。任何输入都不抛。 */
export function relativeTime(timestamp: number): string {
  if (!Number.isFinite(timestamp) || timestamp <= 0) return '未知时间'
  // `Math.max(0, …)` 让未来时间戳（时钟漂移 / 写入端时钟超前）显示「刚刚」，
  // 而不是「-3 分钟前」—— 后者是一个会被当成数据错误去追查的假信号。
  const seconds = Math.max(0, Date.now() / 1000 - timestamp)
  if (seconds < 60) return '刚刚'
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前`
  if (seconds < 7 * 86400) return `${Math.floor(seconds / 86400)} 天前`
  try {
    return new Date(timestamp * 1000).toLocaleDateString('zh-CN')
  } catch {
    // 极端时间戳下 toLocaleDateString 可能抛（无效 Date）。
    // 展示层抛异常 = 整页白屏，所以这里必须兜住。
    return '未知时间'
  }
}

/**
 * 秒 → 人类可读时长。任何输入都不抛。
 *
 * 缺失与零是两件事：`undefined` 显示「—」（后端没发这个字段），
 * 而真实的 `0` 仍然显示「0 秒」。把它们混成同一个值就是一句静默的谎。
 */
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

/**
 * 审计事件类型 → 中文标签。
 *
 * 未知类型原样显示，**绝不返回 undefined**：React 渲染 `undefined` 会静默变成
 * 一个空白单元格 —— 那是一个没人看得出异常的字段缺失，等于把后端的缺口藏起来。
 * 字段真的缺失时显示「未知」，让人知道这里本来应该有东西。
 */
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
  const key = typeof type === 'string' ? type : ''
  if (!key) return '未知'
  return EVENT_LABELS[key] ?? key
}

/** 审计 outcome → 中文标签。未知原样显示；字段缺失显示「未知」。 */
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
  const key = typeof outcome === 'string' ? outcome : ''
  if (!key) return '未知'
  return OUTCOME_LABELS[key] ?? key
}

export function outcomeTone(outcome: string): Tone {
  if (outcome === 'allow' || outcome === 'allowed' || outcome === 'granted') return 'ok'
  if (outcome === 'deny' || outcome === 'denied' || outcome === 'login_denied') return 'danger'
  if (outcome === 'defer' || outcome === 'conditional') return 'warn'
  if (outcome === 'login_success') return 'accent'
  return 'muted'
}

/**
 * 把任意值安全地转成字符串，用于展示或搜索匹配。
 *
 * 后端契约有时**撒谎**：`ActivityItem.principal` 在 `contracts.ts` 里声明为 `string`，
 * 但后端 `e.get("principal_id")` 实际可能返回 `null`。这种「类型说非空、运行时却为空」
 * 的落差会让 `value.toLowerCase()` 在 null 上直接抛 `TypeError`、整页白屏。
 *
 * `safeText` 是应对这种落差的总函数：null / undefined / 非字符串数字一律返回 `''`，
 * 字符串原样返回，数字 / 布尔转成字符串。调用方再 `.toLowerCase()` 就绝不会崩。
 * 缺失值返回空串（而非 `'—'`），因为这里是「参与搜索/拼接」的语境，
 * 显示层缺失才用 `'—'`（见各页面渲染处）。
 */
export function safeText(value: unknown): string {
  if (value === null || value === undefined) return ''
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  return ''
}

/**
 * 把任意值安全地转成数组。
 *
 * 同为契约落差兜底：`AppSummary.capabilities` 在契约里声明为 `string[]`，
 * 但后端可能不发这个字段（undefined）。`[...app.capabilities]` 在 undefined 上会抛
 * 「undefined is not iterable」，`app.capabilities.length` 会抛「Cannot read properties
 * of undefined」，两者都会白屏。`safeArray` 是非数组一律返回 `[]` 的总函数，
 * 让「缺少列表」降级成「空列表」而不是崩溃。
 */
export function safeArray<T = unknown>(value: unknown): T[] {
  return Array.isArray(value) ? (value as T[]) : []
}
