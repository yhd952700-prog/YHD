/**
 * 首屏三行状态带：正在做 / 等你确认 / 刚完成。
 *
 * 三行只回答三个问题：现在在跑什么、什么卡在等我、最近跑成了什么。
 * 数据只来自两个已有接口（`/v1/goals`、`/v1/policy/approvals/pending`），
 * 由**一个** 30 秒轮询统一驱动，不按区块各取一遍。
 *
 * 边界（与 PRD 的 S1 一致）：
 * - 「等你确认」只做查看/跳转，不做就地批准 —— 批准仍在审批中心，避免一次
 *   误触就签发凭据；
 * - 三行各自有空态、失败态；403 说「无权查看」，不说成「没有待办」；
 * - 任何一行失败只降级该行，其余行照常显示；
 * - 页面在后台时不轮询，切回来立即补一次。
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, apiFetchWithStatus } from '../lib/api'
import { actionLabel, useNow } from '../lib/format'
import type { GoalSummary, GoalsResponse, PendingApprovalsResponse } from '../lib/operator'
import {
  STATUS_BAND_LIMIT,
  STATUS_BAND_REFRESH_MS,
  artifactName,
  artifactPaths,
  completedAgoLabel,
  createdAgoLabel,
  goalOwnerLabel,
  goalProgressLabel,
  goalProgressRatio,
  goalStateLabel,
  hasCompletedAt,
  pendingSummary,
  pickCompletedGoals,
  pickPendingApprovals,
  pickRunningGoals,
  resolveVisibleScope,
  riskLabel,
  riskToneFor,
  waitingLabel,
} from '../lib/statusBand'
import { Card } from './ui'

type Source<T> = {
  data: T | null
  error: string | null
  /** 真实 HTTP 状态：403 与「空列表」是两件事。 */
  status: number | null
  loading: boolean
}

function firstLoad<T>(): Source<T> {
  return { data: null, error: null, status: null, loading: true }
}

function toSource<T>(result: PromiseSettledResult<{ data: T }>): Source<T> {
  if (result.status === 'fulfilled') {
    return { data: result.value.data, error: null, status: null, loading: false }
  }
  const reason: unknown = result.reason
  return {
    // 失败时清空数据：一行显示「读不到」，比把上一轮的旧数据继续当成现在更诚实。
    data: null,
    error: reason instanceof Error && reason.message ? reason.message : '这一行没有读到内容',
    status: reason instanceof ApiError ? reason.status : null,
    loading: false,
  }
}

/**
 * 一个定时器同时取两个来源。
 *
 * 不复用 `useApi` 是因为它是「一个端点一个定时器」的形状；这里要确保三行共用
 * 同一次刷新。超时、401 清理会话等能力仍由 `apiFetchWithStatus` 提供。
 */
function useStatusSources() {
  const [goals, setGoals] = useState<Source<GoalsResponse>>(firstLoad)
  const [approvals, setApprovals] = useState<Source<PendingApprovalsResponse>>(firstLoad)
  const [nonce, setNonce] = useState(0)
  const inFlight = useRef(false)

  useEffect(() => {
    let alive = true

    const load = async () => {
      if (inFlight.current) return
      inFlight.current = true
      const [goalResult, approvalResult] = await Promise.allSettled([
        apiFetchWithStatus<GoalsResponse>('/v1/goals'),
        apiFetchWithStatus<PendingApprovalsResponse>('/v1/policy/approvals/pending'),
      ])
      inFlight.current = false
      if (!alive) return
      setGoals(toSource(goalResult))
      setApprovals(toSource(approvalResult))
    }

    // 首次进入即使页面在后台也取一次；之后的轮询在隐藏时跳过。
    void load()
    const timer = window.setInterval(() => {
      if (!document.hidden) void load()
    }, STATUS_BAND_REFRESH_MS)
    const onVisible = () => {
      if (!document.hidden) void load()
    }
    document.addEventListener('visibilitychange', onVisible)

    return () => {
      alive = false
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [nonce])

  const reload = useCallback(() => setNonce((value) => value + 1), [])
  return { goals, approvals, reload }
}

export function StatusBand({ onNavigate }: { onNavigate: (key: string) => void }) {
  const { goals, approvals, reload } = useStatusSources()
  const now = useNow(STATUS_BAND_REFRESH_MS)

  const running = pickRunningGoals(goals.data?.goals)
  const pending = pickPendingApprovals(approvals.data?.items)
  const completed = pickCompletedGoals(goals.data?.goals)
  // 「空」有两种原因：真的没有，或你看不到别人的。只有后端明确说 `self` 才补后一句。
  const scope = resolveVisibleScope(goals.data)

  // 只有响应确实带了 `enforcement.enabled === false` 才补这句话；字段缺失时不猜。
  const enforcementOff = approvals.data?.enforcement?.enabled === false

  return (
    <Card
      title="今天的状态"
      sub="正在做、等你确认、刚完成 —— 每 30 秒自动刷新一次"
      span={12}
      action={
        <button type="button" className="os-link" onClick={reload}>
          刷新
        </button>
      }
    >
      <div className="os-band">
        {/* ---------- 正在做 ---------- */}
        <section className="os-band-line" aria-labelledby="band-running">
          <BandHead
            id="band-running"
            title="正在做"
            count={goals.data ? running.length : null}
            onOpenAll={running.length > 0 ? () => onNavigate('goals') : undefined}
            openAllLabel="查看全部"
          />
          {goals.error ? (
            <BandFailure message={goals.error} onRetry={reload} />
          ) : goals.loading ? (
            <p className="os-hint" role="status">
              正在读取…
            </p>
          ) : running.length === 0 ? (
            <div className="os-band-empty">
              <span>现在没有正在执行或排队的目标。</span>
              {scope === 'self' && <span>这里只显示你自己的目标；别人名下的执行不会出现在这一行。</span>}
              <button type="button" className="os-link" onClick={() => onNavigate('butler')}>
                交办一件事 →
              </button>
            </div>
          ) : (
            <>
              <div className="os-list">
                {running.slice(0, STATUS_BAND_LIMIT).map((goal) => (
                  <RunningRow key={goal.goal_id} goal={goal} now={now} />
                ))}
              </div>
              {running.length > STATUS_BAND_LIMIT && (
                <p className="os-band-more">
                  还有 {running.length - STATUS_BAND_LIMIT} 条，点「查看全部」到任务页看。
                </p>
              )}
            </>
          )}
        </section>

        {/* ---------- 等你确认 ---------- */}
        <section className="os-band-line" aria-labelledby="band-pending">
          <BandHead
            id="band-pending"
            title="等你确认"
            count={approvals.data ? pending.length : null}
            onOpenAll={pending.length > 0 ? () => onNavigate('approval') : undefined}
            openAllLabel="去审批中心"
          />
          {approvals.status === 403 ? (
            // 403 不是空列表：这里说清楚「你看不到」，而不是让用户以为天下太平。
            <div className="os-band-empty">
              <span>当前账号无权查看审批待办，这里不显示具体条目。</span>
              <span>用有审批权限的账号登录后才会看到。</span>
            </div>
          ) : approvals.error ? (
            <BandFailure message={approvals.error} onRetry={reload} />
          ) : approvals.loading ? (
            <p className="os-hint" role="status">
              正在读取…
            </p>
          ) : pending.length === 0 ? (
            <div className="os-band-empty">
              <span>没有等你确认的事。</span>
              {enforcementOff && <span>当前没有启用策略拦截，所以不会有动作停下来等你点头。</span>}
            </div>
          ) : (
            <>
              <div className="os-list">
                {pending.slice(0, STATUS_BAND_LIMIT).map((item) => (
                  <PendingRow
                    key={item.pending_id}
                    item={item}
                    now={now}
                    onOpen={() => onNavigate('approval')}
                  />
                ))}
              </div>
              {pending.length > STATUS_BAND_LIMIT && (
                <p className="os-band-more">
                  还有 {pending.length - STATUS_BAND_LIMIT} 条，点「去审批中心」逐条处理。
                </p>
              )}
            </>
          )}
        </section>

        {/* ---------- 刚完成 ---------- */}
        <section className="os-band-line" aria-labelledby="band-completed">
          <BandHead
            id="band-completed"
            title="刚完成"
            count={goals.data ? completed.length : null}
            onOpenAll={completed.length > 0 ? () => onNavigate('goals') : undefined}
            openAllLabel="查看全部"
          />
          {goals.error ? (
            <BandFailure message={goals.error} onRetry={reload} />
          ) : goals.loading ? (
            <p className="os-hint" role="status">
              正在读取…
            </p>
          ) : completed.length === 0 ? (
            <div className="os-band-empty">
              <span>
                还没有「跑完并写出文件」的目标。
                跑完但没写出文件的目标不会放在这里，可以去任务页看原因。
              </span>
              {scope === 'self' && (
                <span>这里只显示你自己的目标；别人完成的目标不会出现在这一行。</span>
              )}
              <button type="button" className="os-link" onClick={() => onNavigate('goals')}>
                去任务页看看 →
              </button>
            </div>
          ) : (
            <>
              <div className="os-list">
                {completed.slice(0, STATUS_BAND_LIMIT).map((goal) => (
                  <CompletedRow key={goal.goal_id} goal={goal} now={now} />
                ))}
              </div>
              {completed.length > STATUS_BAND_LIMIT && (
                <p className="os-band-more">
                  还有 {completed.length - STATUS_BAND_LIMIT} 条，点「查看全部」到任务页看。
                </p>
              )}
            </>
          )}
        </section>
      </div>
    </Card>
  )
}

function BandHead({
  id,
  title,
  count,
  onOpenAll,
  openAllLabel,
}: {
  id: string
  title: string
  count: number | null
  onOpenAll?: () => void
  openAllLabel: string
}) {
  return (
    <div className="os-band-line-head">
      <h3 className="os-band-line-title" id={id}>
        {title}
        {count !== null && count > 0 && <span className="os-badge">{count} 条</span>}
      </h3>
      {onOpenAll && (
        <button type="button" className="os-link" onClick={onOpenAll}>
          {openAllLabel} →
        </button>
      )}
    </div>
  )
}

function BandFailure({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="os-band-error" role="alert">
      <span>这一行暂时读不到：{message}</span>
      <button type="button" className="os-link" onClick={onRetry}>
        重试
      </button>
    </div>
  )
}

function RunningRow({ goal, now }: { goal: GoalSummary; now: number }) {
  const failed = Number(goal.failed_tasks) > 0 ? Number(goal.failed_tasks) : 0
  const ratio = goalProgressRatio(goal)
  const meta = [
    goalOwnerLabel(goal),
    goalStateLabel(goal.state),
    goalProgressLabel(goal),
    createdAgoLabel(goal.created_at, now),
  ]
    .filter(Boolean)
    .join(' · ')

  return (
    <div className="os-row">
      <span
        className="os-dot"
        style={{ background: failed > 0 ? 'var(--os-danger)' : 'var(--os-accent)' }}
      />
      <div className="os-row-main">
        <div className="os-row-title" title={goal.natural_language}>
          {goal.natural_language || '未命名目标'}
          {failed > 0 && <span className="os-band-fail"> · {failed} 步失败</span>}
        </div>
        <div className="os-row-sub">{meta}</div>
        {ratio !== null && (
          <div className="os-bar">
            <div className="os-bar-fill" style={{ width: `${Math.round(ratio * 100)}%` }} />
          </div>
        )}
      </div>
    </div>
  )
}

function PendingRow({
  item,
  now,
  onOpen,
}: {
  item: PendingApprovalsResponse['items'][number]
  now: number
  onOpen: () => void
}) {
  const action = actionLabel(item.action)
  // 后端的 reason 目前是同一句"动作被策略暂缓"（对所有卡片都一样），
  // 拿它当标题会得到三行完全相同的字；动作名才是能区分彼此的信息。
  const title = action ? `${action} 被拦下，等你确认` : pendingSummary(item)
  const meta = [waitingLabel(item.blocked_at, now), item.reason ?? ''].filter(Boolean).join(' · ')

  return (
    <button
      type="button"
      className="os-row os-band-entry"
      onClick={onOpen}
      title="点开去审批中心处理；这里只做查看，不会直接批准"
    >
      <span className="os-row-main">
        <span className="os-row-title">{title}</span>
        <span className="os-row-sub">{meta}</span>
      </span>
      <span
        className="os-badge"
        data-tone={riskToneFor(item.risk_level)}
        title={item.risk_level ? `原始等级：${item.risk_level}` : undefined}
      >
        风险 {riskLabel(item.risk_level)}
      </span>
      <span className="os-row-tail">查看 →</span>
    </button>
  )
}

function CompletedRow({ goal, now }: { goal: GoalSummary; now: number }) {
  const artifacts = artifactPaths(goal)
  const extra = Math.max(0, artifacts.length - 1)
  const sub = [
    artifacts.length > 0 ? artifactName(artifacts[0]) : '产物路径未记录',
    extra > 0 ? `另有 ${extra} 个产物` : '',
    completedAgoLabel(goal.created_at, now, goal.completed_at),
  ]
    .filter(Boolean)
    .join(' · ')
  const finishedKnown = hasCompletedAt(goal)

  return (
    <div className="os-row">
      <span className="os-dot" style={{ background: 'var(--os-ok)' }} />
      <div className="os-row-main">
        <div className="os-row-title" title={goal.natural_language}>
          {goal.natural_language || '未命名目标'}
        </div>
        <div
          className="os-row-sub"
          title={
            finishedKnown
              ? '完成时间来自本地服务的完成记录'
              : '本地服务还没给出完成时间，这里显示的是创建时间'
          }
        >
          {sub}
        </div>
      </div>
    </div>
  )
}
