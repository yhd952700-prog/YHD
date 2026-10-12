import {
  memo,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import type { AppNotification, NotificationLevel } from '../../types';
import { cn, formatRelativeTime } from '../../utils';

export interface NotificationCenterProps {
  notifications: AppNotification[];
  onMarkRead?: (id: string) => void;
  onMarkAllRead?: () => void;
  onClear?: () => void;
  onAction?: (notification: AppNotification) => void;
  className?: string;
}

const LEVEL_META: Record<
  NotificationLevel,
  { icon: string; className: string }
> = {
  info: {
    icon: 'ℹ️',
    className: 'bg-blue-100 text-blue-600 dark:bg-blue-500/15 dark:text-blue-400',
  },
  success: {
    icon: '✅',
    className:
      'bg-emerald-100 text-emerald-600 dark:bg-emerald-500/15 dark:text-emerald-400',
  },
  warning: {
    icon: '⚠️',
    className:
      'bg-amber-100 text-amber-600 dark:bg-amber-500/15 dark:text-amber-400',
  },
  error: {
    icon: '⛔',
    className: 'bg-red-100 text-red-600 dark:bg-red-500/15 dark:text-red-400',
  },
};

interface NotificationItemProps {
  notification: AppNotification;
  onMarkRead?: (id: string) => void;
  onAction?: (notification: AppNotification) => void;
}

const NotificationItem = memo(function NotificationItem({
  notification,
  onMarkRead,
  onAction,
}: NotificationItemProps) {
  const meta = LEVEL_META[notification.level];

  const handleClick = useCallback(() => {
    if (!notification.read) onMarkRead?.(notification.id);
    if (notification.actionUrl) onAction?.(notification);
  }, [notification, onMarkRead, onAction]);

  return (
    <li>
      <button
        type="button"
        onClick={handleClick}
        className={cn(
          'flex w-full items-start gap-3 rounded-xl px-3 py-2.5 text-left transition-colors',
          notification.read
            ? 'opacity-70 hover:bg-slate-50 dark:hover:bg-slate-700/40'
            : 'bg-brand-50/60 hover:bg-brand-50 dark:bg-brand-500/10 dark:hover:bg-brand-500/15',
        )}
      >
        <span
          aria-hidden
          className={cn(
            'flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-sm',
            meta.className,
          )}
        >
          {meta.icon}
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-2">
            <span className="truncate text-sm font-medium text-slate-800 dark:text-slate-100">
              {notification.title}
            </span>
            {!notification.read && (
              <span
                aria-label="未读"
                className="h-1.5 w-1.5 shrink-0 rounded-full bg-brand-600"
              />
            )}
          </span>
          {notification.message && (
            <span className="mt-0.5 line-clamp-2 block text-xs text-slate-500 dark:text-slate-400">
              {notification.message}
            </span>
          )}
          <span className="mt-1 flex items-center gap-2 text-[11px] text-slate-400 dark:text-slate-500">
            {notification.source && <span>{notification.source} ·</span>}
            <time dateTime={notification.timestamp}>
              {formatRelativeTime(notification.timestamp)}
            </time>
            {notification.actionLabel && (
              <span className="font-medium text-brand-600 dark:text-brand-400">
                {notification.actionLabel} →
              </span>
            )}
          </span>
        </span>
      </button>
    </li>
  );
});

export const NotificationCenter = memo(function NotificationCenter({
  notifications,
  onMarkRead,
  onMarkAllRead,
  onClear,
  onAction,
  className,
}: NotificationCenterProps) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  const unreadCount = useMemo(
    () => notifications.filter((item) => !item.read).length,
    [notifications],
  );

  const toggle = useCallback(() => {
    setOpen((prev) => !prev);
  }, []);

  // 点击外部关闭
  useEffect(() => {
    if (!open) return undefined;
    const handlePointerDown = (event: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', handlePointerDown);
    document.addEventListener('keydown', handleEscape);
    return () => {
      document.removeEventListener('mousedown', handlePointerDown);
      document.removeEventListener('keydown', handleEscape);
    };
  }, [open]);

  return (
    <div ref={rootRef} className={cn('relative', className)}>
      <button
        type="button"
        onClick={toggle}
        aria-label={unreadCount > 0 ? `通知，${unreadCount} 条未读` : '通知'}
        aria-expanded={open}
        aria-haspopup="true"
        className="relative rounded-xl p-2 text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-700 dark:text-slate-400 dark:hover:bg-slate-700/60 dark:hover:text-slate-200"
      >
        <svg
          aria-hidden
          className="h-5 w-5"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={1.8}
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9" />
          <path d="M13.7 21a2 2 0 0 1-3.4 0" />
        </svg>
        {unreadCount > 0 && (
          <span className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-red-500 px-1 text-[10px] font-semibold text-white">
            {unreadCount > 99 ? '99+' : unreadCount}
          </span>
        )}
      </button>

      {open && (
        <div
          role="dialog"
          aria-label="通知中心"
          className="absolute right-0 z-50 mt-2 w-80 origin-top-right rounded-2xl border border-slate-200 bg-white shadow-xl dark:border-slate-700 dark:bg-slate-800"
        >
          <header className="flex items-center justify-between border-b border-slate-100 px-4 py-3 dark:border-slate-700/60">
            <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
              通知
              {unreadCount > 0 && (
                <span className="ml-1.5 rounded-full bg-brand-100 px-1.5 py-0.5 text-[11px] font-medium text-brand-700 dark:bg-brand-500/20 dark:text-brand-300">
                  {unreadCount} 未读
                </span>
              )}
            </h3>
            <div className="flex items-center gap-1">
              {unreadCount > 0 && (
                <button
                  type="button"
                  onClick={onMarkAllRead}
                  className="rounded-lg px-2 py-1 text-xs text-brand-600 transition-colors hover:bg-brand-50 dark:text-brand-400 dark:hover:bg-brand-500/10"
                >
                  全部已读
                </button>
              )}
              {notifications.length > 0 && (
                <button
                  type="button"
                  onClick={onClear}
                  className="rounded-lg px-2 py-1 text-xs text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-600 dark:hover:bg-slate-700/60 dark:hover:text-slate-300"
                >
                  清空
                </button>
              )}
            </div>
          </header>

          {notifications.length === 0 ? (
            <p className="py-12 text-center text-sm text-slate-400 dark:text-slate-500">
              暂无通知
            </p>
          ) : (
            <ul className="max-h-96 space-y-1 overflow-y-auto p-2">
              {notifications.map((notification) => (
                <NotificationItem
                  key={notification.id}
                  notification={notification}
                  onMarkRead={onMarkRead}
                  onAction={onAction}
                />
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
});

export default NotificationCenter;
