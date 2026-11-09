/**
 * 目标终态 → 界面上能一眼分清的三种结果。
 *
 * 为什么单独抽出来
 * ----------------
 * 以前 `completed` 一律显示绿色的「已完成」，即使这次根本没有写出任何文件；
 * 用户看到的和实际发生的事相反。这里把终态拆成：
 *
 * 1. 已完成并产出（绿色，附产物数量）
 * 2. 已完成但无产出（黄色，明确说"没有写出文件"）
 * 3. 未完成（红色，明确给出"重试 / 改需求"的下一步）
 *
 * 纯函数，列表、详情、通知栏共用同一份判断，避免三个地方各写一套。
 */

import type { GoalSummary } from './operator'

export type GoalVerdictKind =
  | 'delivered'
  | 'empty'
  | 'failed'
  | 'running'
  | 'queued'
  | 'unknown'

export type GoalVerdictTone = 'ok' | 'warning' | 'danger' | 'muted'

/** 产物可以是相对路径字符串，也可以是后端带存在性标记的对象。 */
export type ArtifactRef = string | { path?: string | null; exists?: boolean | null }

export interface GoalVerdictInput {
  state?: string | null
  artifacts?: ArtifactRef[] | null
  task_count?: number
  completed_tasks?: number
  failed_tasks?: number
}

export interface GoalVerdict {
  kind: GoalVerdictKind
  label: string
  tone: GoalVerdictTone
  /** 给人看的下一步；不需要动作时为空字符串。 */
  hint: string
  /** 是否值得（或需要）重试。 */
  retryable: boolean
}

function artifactCount(input: GoalVerdictInput): number {
  const artifacts = input.artifacts
  if (!Array.isArray(artifacts)) return 0
  return artifacts.filter((item) => {
    if (typeof item === 'string') return item.trim().length > 0
    if (!item || typeof item !== 'object') return false
    // 后端明确说文件不存在时，不能继续算“有产物”。
    if (item.exists === false) return false
    return typeof item.path === 'string' && item.path.trim().length > 0
  }).length
}

/** 判断一个目标的真实终态。未知状态原样显示，不猜。 */
export function goalVerdict(input: GoalVerdictInput): GoalVerdict {
  const state = String(input.state || '').toLowerCase()
  if (state === 'running') {
    return {
      kind: 'running',
      label: '进行中',
      tone: 'warning',
      hint: '任务还在执行；结束后这里会给出"有产出 / 无产出 / 未完成"的结论。',
      retryable: false,
    }
  }
  if (state === 'queued') {
    return {
      kind: 'queued',
      label: '排队中',
      tone: 'muted',
      hint: '任务在队列里等待执行，还没有开始。',
      retryable: false,
    }
  }
  if (state === 'completed') {
    const produced = artifactCount(input)
    if (produced > 0) {
      return {
        kind: 'delivered',
        label: '已完成并产出',
        tone: 'ok',
        hint: `产物已写入工作区（${produced} 个文件），可在下方「产生的产物」里打开。`,
        retryable: false,
      }
    }
    const failed = Number(input.failed_tasks || 0)
    if (failed > 0) {
      return {
        kind: 'failed',
        label: '未完成',
        tone: 'danger',
        hint: `有 ${failed} 项任务失败，本次没有可交付结果；请查看原因后重试，或补充更明确的需求。`,
        retryable: true,
      }
    }
    return {
      kind: 'empty',
      label: '已完成但无产出',
      tone: 'warning',
      hint: '任务跑完了，但没有写出任何文件，也没有可交付内容；需要交付物时请补充产物要求后重试。',
      retryable: true,
    }
  }
  if (state === 'failed' || state === 'interrupted') {
    const failed = Number(input.failed_tasks || 0)
    const cause = failed > 0 ? `有 ${failed} 项任务失败，` : ''
    return {
      kind: 'failed',
      label: '未完成',
      tone: 'danger',
      hint: `${cause}本次没有产出；请查看失败原因后重试，若目标本身不清楚请补充需求。`,
      retryable: true,
    }
  }
  if (state === 'cancelled') {
    return {
      kind: 'failed',
      label: '已停止',
      tone: 'danger',
      hint: '执行被中止；已完成的步骤和产物会保留，可查看后再决定是否重新派活。',
      retryable: true,
    }
  }
  return {
    kind: 'unknown',
    label: input.state || '—',
    tone: 'muted',
    hint: '',
    retryable: false,
  }
}

/** 从列表摘要取判断所需字段（列表接口已带 artifacts）。 */
export function goalSummaryVerdict(goal: GoalSummary): GoalVerdict {
  return goalVerdict({
    state: goal.state,
    artifacts: goal.artifacts,
    task_count: goal.task_count,
    completed_tasks: goal.completed_tasks,
    failed_tasks: goal.failed_tasks,
  })
}
