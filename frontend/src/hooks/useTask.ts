import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { Task, TaskColumn, TaskFilter, TaskStatus } from '../types';

export const TASK_STATUS_ORDER: TaskStatus[] = [
  'backlog',
  'todo',
  'in_progress',
  'review',
  'done',
  'blocked',
];

export const TASK_STATUS_LABELS: Record<TaskStatus, string> = {
  backlog: '待规划',
  todo: '待处理',
  in_progress: '进行中',
  review: '待评审',
  done: '已完成',
  blocked: '已阻塞',
};

export interface UseTaskOptions {
  fetcher?: () => Promise<Task[]>;
  onUpdate?: (taskId: string, patch: Partial<Task>) => Promise<void>;
  onCreate?: (task: Omit<Task, 'id' | 'createdAt'>) => Promise<Task>;
  onDelete?: (taskId: string) => Promise<void>;
  autoLoad?: boolean;
}

export interface UseTaskResult {
  tasks: Task[];
  columns: TaskColumn[];
  loading: boolean;
  error: Error | null;
  filter: TaskFilter;
  draggingTaskId: string | null;
  setFilter: (filter: TaskFilter) => void;
  moveTask: (taskId: string, status: TaskStatus, targetIndex?: number) => void;
  updateTask: (taskId: string, patch: Partial<Task>) => Promise<void>;
  createTask: (task: Omit<Task, 'id' | 'createdAt'>) => Promise<Task | null>;
  deleteTask: (taskId: string) => Promise<void>;
  startDrag: (taskId: string) => void;
  endDrag: () => void;
  refresh: () => Promise<void>;
  stats: { total: number; done: number; inProgress: number; blocked: number };
}

function applyFilter(tasks: Task[], filter: TaskFilter): Task[] {
  const search = filter.search?.trim().toLowerCase();
  return tasks.filter((task) => {
    if (search) {
      const haystack = [task.title, task.description, ...(task.labels ?? [])]
        .filter(Boolean)
        .join(' ')
        .toLowerCase();
      if (!haystack.includes(search)) return false;
    }
    if (filter.statuses?.length && !filter.statuses.includes(task.status)) return false;
    if (filter.priorities?.length && !filter.priorities.includes(task.priority)) return false;
    if (filter.agentId && task.agentId !== filter.agentId) return false;
    if (filter.assigneeId && task.assigneeId !== filter.assigneeId) return false;
    if (filter.labels?.length && !filter.labels.some((l) => task.labels?.includes(l))) {
      return false;
    }
    return true;
  });
}

/**
 * useTask - 任务看板状态管理，含拖拽移动与乐观更新。
 */
export function useTask(options: UseTaskOptions = {}): UseTaskResult {
  const { fetcher, onUpdate, onCreate, onDelete, autoLoad = true } = options;

  const [tasks, setTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [filter, setFilter] = useState<TaskFilter>({});
  const [draggingTaskId, setDraggingTaskId] = useState<string | null>(null);

  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const refresh = useCallback(async () => {
    if (!fetcher) return;
    setLoading(true);
    setError(null);
    try {
      const data = await fetcher();
      if (mountedRef.current) setTasks(data);
    } catch (e) {
      if (mountedRef.current) setError(e instanceof Error ? e : new Error(String(e)));
    } finally {
      if (mountedRef.current) setLoading(false);
    }
  }, [fetcher]);

  useEffect(() => {
    if (autoLoad) void refresh();
  }, [autoLoad, refresh]);

  const filteredTasks = useMemo(() => applyFilter(tasks, filter), [tasks, filter]);

  const columns = useMemo<TaskColumn[]>(
    () =>
      TASK_STATUS_ORDER.map((status) => ({
        id: status,
        title: TASK_STATUS_LABELS[status],
        tasks: filteredTasks.filter((t) => t.status === status),
      })),
    [filteredTasks],
  );

  const moveTask = useCallback(
    (taskId: string, status: TaskStatus, targetIndex?: number) => {
      setTasks((prev) => {
        const task = prev.find((t) => t.id === taskId);
        if (!task || task.status === status) return prev;

        const moved: Task = {
          ...task,
          status,
          updatedAt: new Date().toISOString(),
          completedAt: status === 'done' ? new Date().toISOString() : task.completedAt,
          progress: status === 'done' ? 100 : task.progress,
        };

        const rest = prev.filter((t) => t.id !== taskId);
        if (targetIndex === undefined) {
          return [...rest, moved];
        }

        // 插入到目标列的指定位置
        const result: Task[] = [];
        let inserted = false;
        let colIndex = 0;
        for (const t of rest) {
          if (!inserted && t.status === status && colIndex === targetIndex) {
            result.push(moved);
            inserted = true;
          }
          if (t.status === status) colIndex += 1;
          result.push(t);
        }
        if (!inserted) result.push(moved);
        return result;
      });

      void onUpdate?.(taskId, { status });
    },
    [onUpdate],
  );

  const updateTask = useCallback(
    async (taskId: string, patch: Partial<Task>) => {
      const previous = tasks;
      setTasks((prev) =>
        prev.map((t) =>
          t.id === taskId ? { ...t, ...patch, updatedAt: new Date().toISOString() } : t,
        ),
      );
      if (onUpdate) {
        try {
          await onUpdate(taskId, patch);
        } catch (e) {
          if (mountedRef.current) {
            setTasks(previous);
            setError(e instanceof Error ? e : new Error(String(e)));
          }
        }
      }
    },
    [tasks, onUpdate],
  );

  const createTask = useCallback(
    async (task: Omit<Task, 'id' | 'createdAt'>): Promise<Task | null> => {
      if (!onCreate) return null;
      try {
        const created = await onCreate(task);
        if (mountedRef.current) setTasks((prev) => [created, ...prev]);
        return created;
      } catch (e) {
        if (mountedRef.current) setError(e instanceof Error ? e : new Error(String(e)));
        return null;
      }
    },
    [onCreate],
  );

  const deleteTask = useCallback(
    async (taskId: string) => {
      const previous = tasks;
      setTasks((prev) => prev.filter((t) => t.id !== taskId));
      if (onDelete) {
        try {
          await onDelete(taskId);
        } catch (e) {
          if (mountedRef.current) {
            setTasks(previous);
            setError(e instanceof Error ? e : new Error(String(e)));
          }
        }
      }
    },
    [tasks, onDelete],
  );

  const startDrag = useCallback((taskId: string) => setDraggingTaskId(taskId), []);
  const endDrag = useCallback(() => setDraggingTaskId(null), []);

  const stats = useMemo(
    () => ({
      total: tasks.length,
      done: tasks.filter((t) => t.status === 'done').length,
      inProgress: tasks.filter((t) => t.status === 'in_progress').length,
      blocked: tasks.filter((t) => t.status === 'blocked').length,
    }),
    [tasks],
  );

  return {
    tasks,
    columns,
    loading,
    error,
    filter,
    draggingTaskId,
    setFilter,
    moveTask,
    updateTask,
    createTask,
    deleteTask,
    startDrag,
    endDrag,
    refresh,
    stats,
  };
}
