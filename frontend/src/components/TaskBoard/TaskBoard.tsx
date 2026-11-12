import React, { memo, useCallback, useMemo, useState } from 'react';
import type { Task, TaskColumn, TaskStatus } from '../../types';
import { TaskCard } from '../TaskCard';
import { cn } from '../../utils';

export interface TaskBoardProps {
  columns: TaskColumn[];
  /** 拖拽中任务 id（用于高亮放置区） */
  draggingTaskId?: string | null;
  /** 任务移动回调（HTML5 DnD 落点） */
  onTaskMove?: (taskId: string, status: TaskStatus, targetIndex?: number) => void;
  onTaskClick?: (task: Task) => void;
  onAddTask?: (status: TaskStatus) => void;
  /** 是否显示 "新增任务" 按钮 */
  allowAdd?: boolean;
  /** 列标题右侧徽标 */
  renderColumnBadge?: (column: TaskColumn) => React.ReactNode;
  loading?: boolean;
  className?: string;
}

const COLUMN_ACCENTS: Record<TaskStatus, string> = {
  backlog: 'bg-slate-400',
  todo: 'bg-sky-500',
  in_progress: 'bg-brand-500',
  review: 'bg-amber-500',
  done: 'bg-emerald-500',
  blocked: 'bg-red-500',
};

interface BoardColumnProps {
  column: TaskColumn;
  dragOver: boolean;
  draggingTaskId?: string | null;
  onTaskMove?: (taskId: string, status: TaskStatus, targetIndex?: number) => void;
  onTaskClick?: (task: Task) => void;
  onAddTask?: (status: TaskStatus) => void;
  setDragOver: (status: TaskStatus | null) => void;
  renderColumnBadge?: (column: TaskColumn) => React.ReactNode;
  setLocalDragging: (id: string | null) => void;
}

const BoardColumn = memo<BoardColumnProps>(function BoardColumn({
  column,
  dragOver,
  draggingTaskId,
  onTaskMove,
  onTaskClick,
  onAddTask,
  setDragOver,
  renderColumnBadge,
  setLocalDragging,
}) {
  const wipExceeded = column.wipLimit !== undefined && column.tasks.length > column.wipLimit;

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragOver(null);
      const taskId = e.dataTransfer.getData('text/task-id');
      if (taskId) onTaskMove?.(taskId, column.id);
    },
    [column.id, onTaskMove, setDragOver],
  );

  return (
    <section
      aria-label={column.title}
      onDragOver={(e) => {
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
        setDragOver(column.id);
      }}
      onDragLeave={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node)) setDragOver(null);
      }}
      onDrop={handleDrop}
      className={cn(
        'flex w-72 shrink-0 flex-col rounded-xl border bg-slate-50/60 transition-colors dark:bg-slate-900/40',
        dragOver
          ? 'border-brand-400 bg-brand-50/60 dark:border-brand-600 dark:bg-brand-950/30'
          : 'border-slate-200 dark:border-slate-800',
      )}
    >
      <header className="flex items-center gap-2 px-3 py-2.5">
        <span className={cn('h-2 w-2 rounded-full', COLUMN_ACCENTS[column.id])} />
        <h3 className="text-sm font-semibold text-slate-700 dark:text-slate-200">{column.title}</h3>
        <span
          className={cn(
            'rounded-full px-1.5 py-0.5 text-[11px] tabular-nums',
            wipExceeded
              ? 'bg-red-100 text-red-600 dark:bg-red-950 dark:text-red-400'
              : 'bg-slate-200/70 text-slate-500 dark:bg-slate-800 dark:text-slate-400',
          )}
        >
          {column.tasks.length}
          {column.wipLimit !== undefined && `/${column.wipLimit}`}
        </span>
        <div className="ml-auto flex items-center gap-1">
          {renderColumnBadge?.(column)}
          {onAddTask && (
            <button
              type="button"
              onClick={() => onAddTask(column.id)}
              className="rounded-md p-1 text-slate-400 hover:bg-slate-200 hover:text-slate-600 dark:hover:bg-slate-800"
              aria-label={`在 ${column.title} 新增任务`}
            >
              <svg className="h-4 w-4" viewBox="0 0 20 20" fill="currentColor">
                <path d="M10.75 4.75a.75.75 0 00-1.5 0v4.5h-4.5a.75.75 0 000 1.5h4.5v4.5a.75.75 0 001.5 0v-4.5h4.5a.75.75 0 000-1.5h-4.5v-4.5z" />
              </svg>
            </button>
          )}
        </div>
      </header>

      <div className="flex flex-1 flex-col gap-2 overflow-y-auto px-2 pb-2" style={{ minHeight: 120 }}>
        {column.tasks.map((task) => (
          <TaskCard
            key={task.id}
            task={task}
            draggable={Boolean(onTaskMove)}
            dragging={task.id === draggingTaskId}
            onClick={onTaskClick}
            onDragStart={(t, e) => {
              e.dataTransfer.setData('text/task-id', t.id);
              e.dataTransfer.effectAllowed = 'move';
              setLocalDragging(t.id);
            }}
            onDragEnd={() => setLocalDragging(null)}
          />
        ))}

        {column.tasks.length === 0 && (
          <div
            className={cn(
              'flex flex-1 items-center justify-center rounded-lg border border-dashed py-8 text-xs',
              dragOver
                ? 'border-brand-400 text-brand-500'
                : 'border-slate-300 text-slate-400 dark:border-slate-700',
            )}
          >
            {dragOver ? '释放以移动到此处' : '拖拽任务到此处'}
          </div>
        )}
      </div>
    </section>
  );
});

/**
 * TaskBoard - 看板视图，基于原生 HTML5 Drag & Drop 实现列间拖拽。
 */
export const TaskBoard = memo<TaskBoardProps>(function TaskBoard({
  columns,
  draggingTaskId: externalDraggingId,
  onTaskMove,
  onTaskClick,
  onAddTask,
  allowAdd = false,
  renderColumnBadge,
  loading = false,
  className,
}) {
  const [dragOverStatus, setDragOverStatus] = useState<TaskStatus | null>(null);
  const [localDraggingId, setLocalDraggingId] = useState<string | null>(null);

  const draggingId = externalDraggingId ?? localDraggingId;
  const totalTasks = useMemo(
    () => columns.reduce((sum, col) => sum + col.tasks.length, 0),
    [columns],
  );

  const handleAddTask = allowAdd ? onAddTask : undefined;

  if (loading) {
    return (
      <div className={cn('flex gap-4 overflow-x-auto pb-2', className)}>
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="flex w-72 shrink-0 flex-col gap-2 rounded-xl border border-slate-200 p-3 dark:border-slate-800">
            <div className="lh-skeleton h-5 w-24" />
            <div className="lh-skeleton h-24 w-full" />
            <div className="lh-skeleton h-24 w-full" />
          </div>
        ))}
      </div>
    );
  }

  return (
    <div className={cn('flex flex-col gap-2', className)}>
      <div
        className="flex gap-4 overflow-x-auto pb-3"
        role="list"
        aria-label={`任务看板，共 ${totalTasks} 个任务`}
      >
        {columns.map((column) => (
          <BoardColumn
            key={column.id}
            column={column}
            dragOver={dragOverStatus === column.id}
            draggingTaskId={draggingId}
            onTaskMove={onTaskMove}
            onTaskClick={onTaskClick}
            onAddTask={handleAddTask}
            setDragOver={setDragOverStatus}
            renderColumnBadge={renderColumnBadge}
            setLocalDragging={setLocalDraggingId}
          />
        ))}
      </div>
    </div>
  );
});
