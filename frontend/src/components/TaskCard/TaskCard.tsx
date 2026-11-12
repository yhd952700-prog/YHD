import React, { memo, useMemo } from 'react';
import type { Task, TaskPriority } from '../../types';
import { avatarColor, cn, formatRelativeTime, initials } from '../../utils';

export interface TaskCardProps {
  task: Task;
  /** 是否正在被拖拽 */
  dragging?: boolean;
  /** 是否可选中拖拽（启用 draggable 属性） */
  draggable?: boolean;
  compact?: boolean;
  onClick?: (task: Task) => void;
  onDragStart?: (task: Task, e: React.DragEvent) => void;
  onDragEnd?: (task: Task, e: React.DragEvent) => void;
  className?: string;
}

export const PRIORITY_META: Record<TaskPriority, { label: string; className: string; bar: string }> = {
  critical: { label: '紧急', className: 'bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300', bar: 'bg-red-500' },
  high: { label: '高', className: 'bg-orange-100 text-orange-700 dark:bg-orange-950 dark:text-orange-300', bar: 'bg-orange-500' },
  medium: { label: '中', className: 'bg-blue-100 text-blue-700 dark:bg-blue-950 dark:text-blue-300', bar: 'bg-blue-500' },
  low: { label: '低', className: 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300', bar: 'bg-slate-400' },
};

interface ProgressBarProps {
  value: number; // 0 - 100
  colorClass?: string;
  size?: 'sm' | 'md';
}

export const ProgressBar: React.FC<ProgressBarProps> = ({ value, colorClass, size = 'sm' }) => {
  const clamped = Math.min(100, Math.max(0, value));
  return (
    <div
      role="progressbar"
      aria-valuenow={clamped}
      aria-valuemin={0}
      aria-valuemax={100}
      className={cn('w-full overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800', size === 'sm' ? 'h-1.5' : 'h-2.5')}
    >
      <div
        className={cn(
          'h-full rounded-full transition-all duration-500',
          colorClass ?? (clamped >= 100 ? 'bg-emerald-500' : 'bg-brand-500'),
        )}
        style={{ width: `${clamped}%` }}
      />
    </div>
  );
};

/**
 * TaskCard - 任务卡片，含状态、优先级、进度条与执行者信息。
 */
export const TaskCard = memo<TaskCardProps>(function TaskCard({
  task,
  dragging = false,
  draggable = false,
  compact = false,
  onClick,
  onDragStart,
  onDragEnd,
  className,
}) {
  const priority = PRIORITY_META[task.priority];
  const completedSteps = useMemo(
    () => task.steps?.filter((s) => s.completed).length ?? 0,
    [task.steps],
  );
  const overdue = useMemo(() => {
    if (!task.dueDate || task.status === 'done') return false;
    return new Date(task.dueDate).getTime() < Date.now();
  }, [task.dueDate, task.status]);

  return (
    <div
      draggable={draggable}
      onDragStart={(e) => onDragStart?.(task, e)}
      onDragEnd={(e) => onDragEnd?.(task, e)}
      onClick={() => onClick?.(task)}
      className={cn(
        'lh-card p-3',
        onClick && 'cursor-pointer',
        draggable && 'cursor-grab active:cursor-grabbing',
        dragging && 'rotate-1 opacity-50 shadow-card-hover',
        className,
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <h4
          className={cn(
            'text-sm font-medium leading-snug text-slate-800 dark:text-slate-100',
            task.status === 'done' && 'text-slate-400 line-through',
          )}
        >
          {task.title}
        </h4>
        <span className={cn('lh-badge shrink-0', priority.className)}>{priority.label}</span>
      </div>

      {task.description && !compact && (
        <p className="mt-1.5 line-clamp-2 text-xs text-slate-500 dark:text-slate-400">
          {task.description}
        </p>
      )}

      {task.status !== 'done' && task.progress > 0 && (
        <div className="mt-2.5 flex items-center gap-2">
          <ProgressBar value={task.progress} />
          <span className="shrink-0 text-[11px] tabular-nums text-slate-400">{task.progress}%</span>
        </div>
      )}

      <div className="mt-2.5 flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          {task.assigneeName && (
            <span
              className={cn(
                'flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[10px] font-semibold text-white',
                avatarColor(task.assigneeName),
              )}
              title={task.assigneeName}
            >
              {initials(task.assigneeName)}
            </span>
          )}
          {task.steps && task.steps.length > 0 && (
            <span className="inline-flex items-center gap-1 text-[11px] text-slate-400">
              <svg className="h-3 w-3" viewBox="0 0 20 20" fill="currentColor">
                <path
                  fillRule="evenodd"
                  d="M16.704 4.153a.75.75 0 01.143 1.052l-8 10.5a.75.75 0 01-1.127.075l-4.5-4.5a.75.75 0 011.06-1.06l3.894 3.893 7.48-9.817a.75.75 0 011.05-.143z"
                  clipRule="evenodd"
                />
              </svg>
              {completedSteps}/{task.steps.length}
            </span>
          )}
        </div>

        <div className="flex shrink-0 items-center gap-2 text-[11px]">
          {task.dueDate && (
            <span className={cn(overdue ? 'font-medium text-red-500' : 'text-slate-400')}>
              {overdue ? '已逾期 · ' : ''}
              {formatRelativeTime(task.dueDate)}
            </span>
          )}
        </div>
      </div>

      {task.labels && task.labels.length > 0 && !compact && (
        <div className="mt-2 flex flex-wrap gap-1">
          {task.labels.slice(0, 3).map((label) => (
            <span
              key={label}
              className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-500 dark:bg-slate-800 dark:text-slate-400"
            >
              #{label}
            </span>
          ))}
        </div>
      )}
    </div>
  );
});
