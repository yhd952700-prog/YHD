/**
 * 把「目标与任务」的真实终态翻译成通知栏条目。
 *
 * 为什么单独放一个纯函数
 * --------------------
 * `App.tsx` 的通知聚合已经有认证/依赖/模型三大类；执行结果必须同样可见，否则
 * 用户关掉页面再回来时，只会看到一个"一切正常"的通知栏，而真正失败的任务
 * 被埋在二级页面里。这里只做纯数据映射，方便用单测钉死三件事：
 *
 * 1. 只有终态（完成 / 失败 / 停止）才通知，running 不制造噪音；
 * 2. 只提示最近 24 小时，不把历史失败一次性倒出来（同一件事不重复轰炸）；
 * 3. 文案是中性中文：不出现 `failed` / 异常类名 / 任务 id。
 */

import type { GoalSummary } from './operator'
import { goalSummaryVerdict } from './goalVerdict'

/** 与 `components/Shell` 的 `OsNotification.tone` 保持一致。 */
export type GoalNoticeTone = 'ok' | 'warn' | 'danger' | 'accent' | 'muted'

const WINDOW_SECONDS = 24 * 60 * 60
const MAX_ITEMS = 5
const TEXT_LIMIT = 40

export interface GoalNotice {
  id: string
  tone: GoalNoticeTone
  title: string
  detail: string
}

function shorten(text: string, limit = TEXT_LIMIT): string {
  const value = String(text || '').replace(/\s+/g, ' ').trim()
  if (!value) return '你派出的任务'
  return value.length <= limit ? value : `${value.slice(0, limit - 1)}…`
}

/** 一条 goal 终态 → 通知文案；未知状态返回 null（不猜）。 */
export function goalNotice(goal: GoalSummary): GoalNotice | null {
  const state = (goal.state || '').toLowerCase()
  const text = shorten(goal.natural_language)
  const base = { id: `goal-${goal.goal_id}-${state}` }
  const failed = Number(goal.failed_tasks || 0)
  const verdict = goalSummaryVerdict(goal)
  if (state === 'completed') {
    // 整体 completed 但有子任务失败：不能说"已完成"糊弄过去。
    if (failed > 0) {
      return {
        ...base,
        tone: 'warn',
        title: '任务跑完了，但有未成功的部分',
        detail: `「${text}」里有 ${failed} 项没有成功。可在「目标与任务」查看原因并重试。`,
      }
    }
    if (verdict.kind === 'empty') {
      // completed 但没有写出任何文件：不能给绿色的「已完成」。
      return {
        ...base,
        tone: 'warn',
        title: '任务跑完了，但没有产出文件',
        detail: `「${text}」已完成，但没有写出任何文件，也没有可交付内容。需要交付物时请补充产物要求后重试。`,
      }
    }
    return {
      ...base,
      tone: 'ok',
      title: '任务已完成并产出',
      detail: `「${text}」已完成，产物已写入工作区，可在「目标与任务」查看。`,
    }
  }
  if (state === 'failed' || state === 'interrupted') {
    // 内部状态码与异常文本不进入通知；只给"哪一项、下一步去哪看"。
    const hint = failed > 0 ? `其中 ${failed} 项没有成功。` : ''
    return {
      ...base,
      tone: 'danger',
      title: '有一个任务没有完成',
      detail: `「${text}」没有完成。${hint}可在「目标与任务」查看原因并重试。`,
    }
  }
  if (state === 'cancelled') {
    return {
      ...base,
      tone: 'muted',
      title: '一个任务已停止',
      detail: `「${text}」已停止，没有继续执行。`,
    }
  }
  return null
}

/**
 * 从 `GET /v1/goals` 列表派生通知条目。
 *
 * `nowSeconds` 可注入，便于测试；默认取真实时间。按时间倒序，最多 5 条。
 */
export function deriveGoalNotices(
  goals: GoalSummary[] | undefined | null,
  nowSeconds: number = Date.now() / 1000,
): GoalNotice[] {
  if (!goals || goals.length === 0) return []
  const seen = new Set<string>()
  const items: Array<GoalNotice & { created: number }> = []
  for (const goal of goals) {
    const notice = goalNotice(goal)
    if (!notice) continue
    const created = Number(goal.created_at || 0)
    if (!Number.isFinite(created) || created <= 0) continue
    if (nowSeconds - created > WINDOW_SECONDS) continue
    if (seen.has(notice.id)) continue
    seen.add(notice.id)
    items.push({ ...notice, created })
  }
  items.sort((a, b) => b.created - a.created)
  return items.slice(0, MAX_ITEMS).map(({ created: _created, ...notice }) => notice)
}
