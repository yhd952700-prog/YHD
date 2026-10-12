import React, { memo, useMemo } from 'react';
import { cn } from '../../utils';

export interface DashboardWidget {
  id: string;
  /** 网格列跨度（基于 12 列） */
  colSpan?: 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12;
  /** 网格行跨度 */
  rowSpan?: number;
  /** 小部件标题（渲染在卡片头部，未提供 contentWrapper 时生效） */
  title?: string;
  /** 头部右侧操作区 */
  actions?: React.ReactNode;
  /** 小部件内容 */
  content: React.ReactNode;
  /** 是否隐藏 */
  hidden?: boolean;
}

export interface DashboardProps {
  widgets: DashboardWidget[];
  /** 网格列数，默认 12 */
  columns?: number;
  /** 网格间距（Tailwind gap） */
  gap?: 2 | 3 | 4 | 6;
  /** 标题 */
  title?: string;
  /** 标题右侧操作区 */
  headerActions?: React.ReactNode;
  /** 是否以卡片包裹每个小部件 */
  cardWrapper?: boolean;
  loading?: boolean;
  className?: string;
}

const SPAN_CLASSES: Record<number, string> = {
  1: 'col-span-1',
  2: 'col-span-2',
  3: 'col-span-3',
  4: 'col-span-4',
  5: 'col-span-5',
  6: 'col-span-6',
  7: 'col-span-7',
  8: 'col-span-8',
  9: 'col-span-9',
  10: 'col-span-10',
  11: 'col-span-11',
  12: 'col-span-12',
};

const COLUMN_CLASSES: Record<number, string> = {
  2: 'grid-cols-2',
  3: 'grid-cols-3',
  4: 'grid-cols-4',
  6: 'grid-cols-6',
  12: 'grid-cols-12',
};

/**
 * Dashboard - 仪表盘网格布局容器，支持列跨度小部件与卡片包裹。
 */
export const Dashboard = memo<DashboardProps>(function Dashboard({
  widgets,
  columns = 12,
  gap = 4,
  title,
  headerActions,
  cardWrapper = true,
  loading = false,
  className,
}) {
  const visibleWidgets = useMemo(() => widgets.filter((w) => !w.hidden), [widgets]);

  if (loading) {
    return (
      <div className={cn('flex flex-col gap-4', className)}>
        {title && <div className="lh-skeleton h-7 w-40" />}
        <div className={cn('grid', COLUMN_CLASSES[columns] ?? 'grid-cols-12', `gap-${gap}`)}>
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className={cn('lh-skeleton h-40', SPAN_CLASSES[i === 0 ? 12 : 4])} />
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className={cn('flex flex-col gap-4', className)}>
      {(title || headerActions) && (
        <header className="flex items-center justify-between">
          {title && (
            <h1 className="text-xl font-bold text-slate-900 dark:text-slate-50">{title}</h1>
          )}
          {headerActions && <div className="flex items-center gap-2">{headerActions}</div>}
        </header>
      )}

      <div
        className={cn('grid auto-rows-min', COLUMN_CLASSES[columns] ?? 'grid-cols-12', `gap-${gap}`)}
        role="grid"
        aria-label={title ?? '仪表盘'}
      >
        {visibleWidgets.map((widget) => {
          const spanClass = SPAN_CLASSES[widget.colSpan ?? 4];
          const rowStyle = widget.rowSpan ? { gridRow: `span ${widget.rowSpan} / span ${widget.rowSpan}` } : undefined;

          if (!cardWrapper) {
            return (
              <div key={widget.id} className={spanClass} style={rowStyle}>
                {widget.content}
              </div>
            );
          }

          return (
            <section
              key={widget.id}
              className={cn('lh-card flex flex-col overflow-hidden', spanClass)}
              style={rowStyle}
              aria-label={widget.title}
            >
              {(widget.title || widget.actions) && (
                <header className="flex items-center justify-between border-b border-slate-100 px-4 py-2.5 dark:border-slate-800">
                  <h2 className="text-sm font-semibold text-slate-700 dark:text-slate-200">
                    {widget.title}
                  </h2>
                  {widget.actions}
                </header>
              )}
              <div className="flex-1 p-4">{widget.content}</div>
            </section>
          );
        })}
      </div>
    </div>
  );
});
