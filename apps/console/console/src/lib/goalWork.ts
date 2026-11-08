/**
 * 管家派活：把后端稳定错误码翻译成**下一步该做什么**。
 *
 * 关键差别（不能笼统报「提交失败」）：
 *   * `agent_not_found` → 这个人不存在，重新选；
 *   * `agent_unavailable` → 这个人被暂停，先去恢复；
 *   * `task_not_retryable` → 不是失败态，没东西可重试；
 *   * `goal_running` → 目标还在跑，等结束再单任务重试。
 *
 * 放在 `lib/` 而不是 `pages/Butler.tsx`：这是纯函数，和组件文件混在一起会让
 * Vite Fast Refresh 无法热更新整个页面（组件文件只应导出组件）。
 */

import { ApiError } from './api'
import {
  BUDGET_DENIAL_MESSAGE,
  BUDGET_DENIAL_NEXT_STEP,
  PERMISSION_DENIAL_MESSAGE,
} from './errorMessage'

function errorText(error: unknown): string {
  if (error instanceof Error && error.message) {
    // 英文异常不上屏；已经是中文的说明原样保留。
    return /[\u4e00-\u9fff]/.test(error.message)
      ? error.message
      : '操作没有完成，请稍后重试。'
  }
  return '操作没有完成，请稍后重试。'
}

export function goalWorkMessage(error: unknown, fallback: string): string {
  const code = error instanceof ApiError ? error.code : null
  // 预算不足 ≠ 权限不足：402 说预算 + 出口，403 才说权限。
  if (
    code === 'budget_exceeded' ||
    (error instanceof ApiError && error.status === 402)
  ) {
    return `${BUDGET_DENIAL_MESSAGE}${BUDGET_DENIAL_NEXT_STEP}`
  }
  switch (code) {
    case 'agent_not_found':
    case 'employee_not_found':
      return '指定的员工不存在，或不在当前可见的名册里。不会换成其他员工执行，请重新选择。'
    case 'employee_disabled':
      return '这位员工已停用，不能接受指派。请先到员工页启用它，再重新派活。'
    case 'agent_unavailable':
    case 'employee_unavailable':
      return '这位员工当前没有可用的执行个体（暂停中），不能接受指派。请先到员工页恢复，再重新派活。'
    case 'assign_target_required':
      return '必须且只能指定一个执行者：员工，或某个执行个体。请重新选择。'
    case 'task_not_retryable':
      return '这个任务当前不是失败态，没有可重试的内容。只有失败任务能单独重试。'
    case 'goal_running':
      return '这个目标还在运行。等它结束后，再回来单独重试失败的任务。'
    case 'task_not_found':
      return '已经找不到这个任务（可能已被清理）。刷新目标后按最新任务卡操作。'
    case 'goal_not_found':
      return '这个目标已经不存在（可能已被清理）。'
    case 'permission_denied':
      return PERMISSION_DENIAL_MESSAGE
    default:
      return `${fallback}：${errorText(error)}`
  }
}
