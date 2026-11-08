import { goalVerdict, type GoalVerdictInput } from '../lib/goalVerdict'

/**
 * 一处目标状态的统一渲染：列表、详情、通知都走这里。
 * 三种终态各有独立色调，且"未完成"会带可重试标记。
 */
export function GoalStatus({
  goal,
  showHint = false,
}: {
  goal: GoalVerdictInput
  showHint?: boolean
}) {
  const verdict = goalVerdict(goal)
  return (
    <span className="os-goal-status" data-verdict={verdict.kind}>
      <span
        className={`os-dot os-tone-${verdict.tone}`}
        title={verdict.hint}
      />
      {verdict.label}
      {verdict.retryable && <span className="os-hint"> · 可重试</span>}
      {showHint && (
        <span className={verdict.retryable ? 'os-error' : 'os-hint'}>
          {' '}
          {verdict.hint}
        </span>
      )}
    </span>
  )
}
