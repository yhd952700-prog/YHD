/**
 * Owner 全部任务 —— 只有 Owner 能进。
 *
 * 列出 Owner 子树内所有账号的目标与任务（来自 `GET /v1/owner/tasks`），
 * 按账号分组。没有任务时显示空态，不放演示任务。普通用户既看不到入口，
 * 调接口也会被后端 403 拒绝。
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { Card, Empty, Failure, Loading } from '../components/ui'
import { loadOwnerTasks, type OwnerTasksPayload } from '../lib/owner'

export const OWNER_TASKS_PAGE = {
  key: 'owner-tasks',
  navLabel: '全部任务',
  title: '全部任务',
  sub: 'Owner 专属：所有人的目标与任务都在这里',
} as const

export function OwnerTasks() {
  const [data, setData] = useState<OwnerTasksPayload | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setData(await loadOwnerTasks())
    } catch (err) {
      setError(err instanceof Error ? err.message : '读取失败，请稍后重试')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const byOwner = useMemo(() => {
    const groups = new Map<string, OwnerTasksPayload['goals']>()
    for (const goal of data?.goals ?? []) {
      const list = groups.get(goal.owner_account_id) ?? []
      list.push(goal)
      groups.set(goal.owner_account_id, list)
    }
    return [...groups.entries()].sort(([a], [b]) => a.localeCompare(b))
  }, [data])

  if (loading) return <Loading rows={4} />
  if (error || !data) {
    return <Failure message={error ?? '读取失败，请稍后重试'} onRetry={() => void load()} />
  }

  return (
    <div className="os-grid">
      <div className="os-span-12 os-kpi-row">
        <div className="os-kpi">
          <div className="os-kpi-label">目标</div>
          <div className="os-kpi-value">{data.goal_count}</div>
          <div className="os-kpi-foot">全部账号合计</div>
        </div>
        <div className="os-kpi">
          <div className="os-kpi-label">任务</div>
          <div className="os-kpi-value">{data.task_count}</div>
          <div className="os-kpi-foot">全部账号合计</div>
        </div>
        <div className="os-kpi">
          <div className="os-kpi-label">涉及账号</div>
          <div className="os-kpi-value">{data.owners.length}</div>
          <div className="os-kpi-foot">有目标在跑的账号数</div>
        </div>
      </div>

      {data.goals.length === 0 ? (
        <div className="os-span-12">
          <Empty title="还没有任务" hint="任何人提交目标后，这里会按账号列出全部目标与任务。" />
        </div>
      ) : (
        byOwner.map(([owner, goals]) => (
          <div className="os-span-12" key={owner}>
            <Card title={owner}>
              <ul>
                {goals.map((goal) => (
                  <li key={goal.goal_id ?? goal.title}>
                    <strong>{goal.title || '（无标题）'}</strong> · {goal.state}
                    {goal.tasks.length === 0 ? (
                      ' · 暂无任务'
                    ) : (
                      <ul>
                        {goal.tasks.map((task) => (
                          <li key={task.task_id ?? `${goal.goal_id}-${task.status}`}>
                            {task.task_id ?? '任务'} · {task.status}
                            {task.assignees.length > 0
                              ? ` · ${task.assignees.join('、')}`
                              : ''}
                          </li>
                        ))}
                      </ul>
                    )}
                  </li>
                ))}
              </ul>
            </Card>
          </div>
        ))
      )}
    </div>
  )
}
