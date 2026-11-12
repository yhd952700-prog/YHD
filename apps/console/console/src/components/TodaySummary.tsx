/**
 * 操作台顶部「今天小结」—— 一句话 + 小字说明。
 *
 * 文案拼装在 lib/todaySummary.ts（纯函数，可单测）；这里只负责渲染：
 * 三项全无时显示空态「今日数据暂未取到」，不画假数字。
 */
import { buildTodaySummary } from '../lib/todaySummary'
import type { TodaySummaryInput } from '../lib/todaySummary'

export function TodaySummary({ data }: { data: TodaySummaryInput }) {
  const view = buildTodaySummary(data)
  if (!view) {
    return (
      <p className="os-today" role="status">
        今日数据暂未取到
      </p>
    )
  }
  return (
    <div className="os-today" role="status">
      <span>{view.items.join('、')}</span>
      {view.notes.map((note) => (
        <span key={note} className="os-today-note">
          {note}
        </span>
      ))}
    </div>
  )
}
