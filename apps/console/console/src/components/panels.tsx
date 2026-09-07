import { useState } from 'react'
import {
  ACTIVITY,
  AI_TASKS,
  BIZ_REALITY,
  KEY_METRICS,
  MARKET,
  PIPELINE,
  SYSTEM_HEALTH,
  TODAYS_BRIEF,
  type TaskItem,
} from '../lib/dashboardData'

/** ============ 今日 CEO 简报 ============ */
export function TodayBrief() {
  return (
    <div className="panel brief-panel">
      <div className="panel-title">
        <span className="brief-title-txt">今日CEO简报</span>
        <span className="brief-title-en">Today's Brief</span>
        <span className="tag-ai">AI</span>
      </div>
      <ul className="brief-list">
        {TODAYS_BRIEF.map((b, i) => (
          <li key={i} className={`brief-item prio-${b.color}`}>
            <span className="brief-icon">{b.icon}</span>
            <span className="brief-text">{b.text}</span>
            <span className={`brief-prio p-${b.priority === '最优优先' ? 'top' : b.priority === '进行中' ? 'mid' : 'low'}`}>
              {b.priority}
            </span>
          </li>
        ))}
      </ul>
      <button className="brief-more">
        查看完整 AI 建议 <span className="arrow">→</span>
      </button>
    </div>
  )
}

/** ============ 核心业务数据 ============ */
type RangeKey = 'd7' | 'd30' | 'd90'
export function KeyMetrics() {
  const [range, setRange] = useState<RangeKey>('d30')
  return (
    <div className="panel metrics-panel">
      <div className="panel-title">
        <span>核心业务数据</span>
        <span className="metrics-en">Key Metrics</span>
        <div className="range-switch">
          {(
            [
              ['d7', '7天'],
              ['d30', '30天'],
              ['d90', '90天'],
            ] as [RangeKey, string][]
          ).map(([k, label]) => (
            <button
              key={k}
              className={`range-btn ${range === k ? 'on' : ''}`}
              onClick={() => setRange(k)}
            >
              {label}
            </button>
          ))}
        </div>
      </div>
      <div className="metric-list">
        {KEY_METRICS.map((m) => (
          <div key={m.key} className="metric">
            <div className="metric-head">
              <span className="metric-label">{m.label}</span>
              <span className="metric-en">{m.en}</span>
              <span className={`metric-delta ${m.dir === 'up' ? 'up' : ''}`}>{m.delta}</span>
            </div>
            <div className="metric-value">
              {range === 'd7' ? m.seg.d7 : range === 'd90' ? m.seg.d90 : m.value}
              <span className="metric-unit">{m.unit}</span>
            </div>
            <div className="metric-bar">
              <span className="metric-bar-fill" style={{ width: `${Number(range === 'd7' ? m.seg.d7 : range === 'd90' ? m.seg.d90 : m.value) > 100 ? 100 : Number(range === 'd7' ? m.seg.d7 : range === 'd90' ? m.seg.d90 : m.value)}%` }} />
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

/** ============ 销售 Pipeline ============ */
export function Pipeline() {
  return (
    <div className="panel pipeline-panel">
      <div className="panel-title">
        <span>销售 Pipeline</span>
        <span className="pipe-hint">6 个机会进行中</span>
      </div>
      <div className="pipeline">
        {PIPELINE.map((s) => (
          <div key={s.label} className={`pipe-col ${s.value > 0 ? 'has' : ''}`}>
            <div className="pipe-head">{s.label}</div>
            <div className="pipe-num">
              {s.value === 0 ? '—' : s.value}
              {s.delta && <span className="pipe-delta">{s.delta}</span>}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

/** ============ 业务数据中心 ============ */
export function BizReality() {
  return (
    <div className="panel biz-panel">
      <div className="panel-title">
        <span>业务数据中心</span>
        <span className="biz-en">Business Reality</span>
      </div>
      <div className="biz-grid">
        {BIZ_REALITY.map((b) => (
          <div key={b.key} className={`biz-chip ${b.status}`}>
            <span className="biz-name">{b.label}</span>
            <span className="biz-progress">
              <span className="biz-bar" style={{ width: `${b.pct}%` }} />
            </span>
            <span className="biz-status">{b.status === 'done' ? '已完成' : '待补充'}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

/** ============ 全球市场聚焦 ============ */
export function MarketFocus() {
  return (
    <div className="panel market-panel">
      <div className="panel-title">
        <span>全球市场聚焦</span>
        <span className="biz-en">Market Focus</span>
      </div>
      <div className="market-body">
        <div className="market-globe">
          <div className="globe-core" />
          <div className="globe-pin p1" />
          <div className="globe-pin p2" />
          <div className="globe-pin p3" />
          <div className="globe-label">泰国 · 东南亚</div>
        </div>
        <ul className="market-rank">
          {MARKET.map((m, i) => (
            <li key={m.name} className={m.hot ? 'hot' : ''}>
              <span className="rank-no">{i + 1}</span>
              <span className="rank-name">{m.name}</span>
              <span className="rank-val">{m.score}</span>
            </li>
          ))}
        </ul>
      </div>
      <button className="market-more">查看市场分析 <span className="arrow">→</span></button>
    </div>
  )
}

/** ============ AI 任务中心 ============ */
export function TaskCenter() {
  const [filter, setFilter] = useState<'all' | 'running' | 'done' | 'archived'>('all')
  const filtered: TaskItem[] = AI_TASKS.filter((t) => filter === 'all' || t.state === filter)
  return (
    <div className="panel tasks-panel">
      <div className="panel-title">
        <span>AI任务中心</span>
        <span className="biz-en">Mission & Tasks</span>
        <div className="task-tabs">
          {(
            [
              ['all', '全部任务'],
              ['running', '进行中'],
              ['done', '已完成'],
              ['archived', '已归档'],
            ] as ['all' | 'running' | 'done' | 'archived', string][]
          ).map(([k, label]) => (
            <button
              key={k}
              className={`task-tab ${filter === k ? 'on' : ''}`}
              onClick={() => setFilter(k)}
            >
              {label}
            </button>
          ))}
        </div>
      </div>
      <ul className="task-list">
        {filtered.map((t, i) => (
          <li key={i}>
            <span className="task-text">{t.text}</span>
            <span className={`task-prio p-${t.priority === '最优优先' ? 'top' : t.priority === '中优先级' ? 'low' : 'mid'}`}>
              {t.priority}
            </span>
          </li>
        ))}
      </ul>
      <button className="task-enter">进入任务中心 <span className="arrow">→</span></button>
    </div>
  )
}

/** ============ 最近动态 ============ */
export function Activity() {
  return (
    <div className="panel activity-panel">
      <div className="panel-title">
        <span>最近动态</span>
        <span className="biz-en">Activity</span>
        <button className="activity-more">查看全部</button>
      </div>
      <ul className="activity-list">
        {ACTIVITY.map((a, i) => (
          <li key={i}>
            <span className="act-time">{a.time}</span>
            <span className="act-text">{a.text}</span>
            <span className="act-tag">{a.tag}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

/** ============ 系统状态（真实遥测） ============ */
interface HealthProps {
  ready: boolean | null
  liveSessions: number
  uptime: number
}
function fmtDuration(total: number): string {
  if (total < 0) return '—'
  const d = Math.floor(total / 86400)
  const h = Math.floor((total % 86400) / 3600)
  const m = Math.floor((total % 3600) / 60)
  return d > 0 ? `${d}天 ${h}时` : h > 0 ? `${h}时 ${m}分` : `${m}分`
}
export function SystemHealth({ ready, uptime }: HealthProps) {
  return (
    <div className="panel health-panel">
      <div className="panel-title">
        <span>系统状态</span>
        <span className="biz-en">System Health</span>
        <span className={`health-pill ${ready ? 'ok' : ready === null ? 'na' : 'bad'}`}>
          {ready === null ? '检测中' : ready ? '全部正常' : '离线'}
        </span>
      </div>
      <ul className="health-list">
        {SYSTEM_HEALTH.map((h) => {
          let value = h.state === 'ok' ? '正常' : '受控'
          if (h.liveKey === 'provider') value = ready ? '在线' : '离线'
          if (h.liveKey === 'ready') value = ready ? '在线' : '离线'
          return (
            <li key={h.label}>
              <span className="health-dot" />
              <span className="health-name">{h.label}</span>
              <span className="health-val">{value}</span>
            </li>
          )
        })}
      </ul>
      <div className="health-live">
        <span>运行时长</span>
        <b>{fmtDuration(uptime)}</b>
      </div>
    </div>
  )
}

/** ============ 我们的目标 ============ */
export function OurGoal() {
  return (
    <div className="goal-card">
      <span className="goal-badge">🏆</span>
      <div className="goal-text">
        <span className="goal-label">我们的目标</span>
        <span className="goal-main">Build a Global Brand — Achieve 100M</span>
      </div>
    </div>
  )
}
