import { memo, useCallback, useState } from 'react';
import type { NavItem } from '../../types';
import { cn } from '../../utils';

export interface SidebarProps {
  items: NavItem[];
  activeId?: string;
  collapsed?: boolean;
  onNavigate?: (item: NavItem) => void;
  onToggleCollapse?: (collapsed: boolean) => void;
  header?: React.ReactNode;
  footer?: React.ReactNode;
  className?: string;
}

interface NavNodeProps {
  item: NavItem;
  depth: number;
  activeId?: string;
  collapsed: boolean;
  onNavigate?: (item: NavItem) => void;
}

const NavNode = memo(function NavNode({
  item,
  depth,
  activeId,
  collapsed,
  onNavigate,
}: NavNodeProps) {
  const [expanded, setExpanded] = useState(true);
  const hasChildren = Boolean(item.children && item.children.length > 0);
  const active = item.id === activeId;

  const handleClick = useCallback(() => {
    if (item.disabled) return;
    if (hasChildren && !collapsed) {
      setExpanded((prev) => !prev);
    }
    if (item.path || !hasChildren) {
      onNavigate?.(item);
    }
  }, [item, hasChildren, collapsed, onNavigate]);

  return (
    <li>
      <button
        type="button"
        onClick={handleClick}
        disabled={item.disabled}
        aria-current={active ? 'page' : undefined}
        aria-expanded={hasChildren ? expanded : undefined}
        title={collapsed ? item.label : undefined}
        style={collapsed ? undefined : { paddingLeft: `${12 + depth * 16}px` }}
        className={cn(
          'group flex w-full items-center gap-3 rounded-lg py-2 text-sm transition-colors',
          collapsed ? 'justify-center px-0' : 'pr-3',
          active
            ? 'bg-brand-50 font-medium text-brand-700 dark:bg-brand-500/15 dark:text-brand-300'
            : 'text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-700/60',
          item.disabled && 'cursor-not-allowed opacity-40',
        )}
      >
        {item.icon && (
          <span aria-hidden className="shrink-0 text-base">
            {item.icon}
          </span>
        )}
        {!collapsed && (
          <>
            <span className="min-w-0 flex-1 truncate text-left">
              {item.label}
            </span>
            {item.badge !== undefined && (
              <span className="shrink-0 rounded-full bg-brand-100 px-1.5 py-0.5 text-[10px] font-semibold text-brand-700 dark:bg-brand-500/20 dark:text-brand-300">
                {item.badge}
              </span>
            )}
            {hasChildren && (
              <svg
                aria-hidden
                className={cn(
                  'h-3.5 w-3.5 shrink-0 text-slate-400 transition-transform',
                  expanded && 'rotate-90',
                )}
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth={2}
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="m9 18 6-6-6-6" />
              </svg>
            )}
          </>
        )}
        {collapsed && item.badge !== undefined && (
          <span className="absolute right-1 top-1 h-1.5 w-1.5 rounded-full bg-brand-600" />
        )}
      </button>

      {hasChildren && expanded && !collapsed && (
        <ul className="mt-0.5 space-y-0.5">
          {(item.children ?? []).map((child) => (
            <NavNode
              key={child.id}
              item={child}
              depth={depth + 1}
              activeId={activeId}
              collapsed={collapsed}
              onNavigate={onNavigate}
            />
          ))}
        </ul>
      )}
    </li>
  );
});

export const Sidebar = memo(function Sidebar({
  items,
  activeId,
  collapsed = false,
  onNavigate,
  onToggleCollapse,
  header,
  footer,
  className,
}: SidebarProps) {
  const handleToggle = useCallback(() => {
    onToggleCollapse?.(!collapsed);
  }, [onToggleCollapse, collapsed]);

  return (
    <aside
      aria-label="侧边导航"
      className={cn(
        'flex h-full flex-col border-r border-slate-200 bg-white transition-[width] duration-200 dark:border-slate-700 dark:bg-slate-900/60',
        collapsed ? 'w-16' : 'w-60',
        className,
      )}
    >
      {header && (
        <div
          className={cn(
            'flex h-14 shrink-0 items-center border-b border-slate-100 dark:border-slate-700/60',
            collapsed ? 'justify-center px-0' : 'px-4',
          )}
        >
          {header}
        </div>
      )}

      <nav aria-label="主导航" className="min-h-0 flex-1 overflow-y-auto p-2">
        <ul className="space-y-0.5">
          {items.map((item) => (
            <NavNode
              key={item.id}
              item={item}
              depth={0}
              activeId={activeId}
              collapsed={collapsed}
              onNavigate={onNavigate}
            />
          ))}
        </ul>
      </nav>

      <div className="shrink-0 border-t border-slate-100 p-2 dark:border-slate-700/60">
        {footer}
        {onToggleCollapse && (
          <button
            type="button"
            onClick={handleToggle}
            aria-label={collapsed ? '展开侧边栏' : '折叠侧边栏'}
            className={cn(
              'flex w-full items-center rounded-lg py-2 text-sm text-slate-500 transition-colors hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-slate-700/60',
              collapsed ? 'justify-center' : 'gap-3 px-3',
            )}
          >
            <svg
              aria-hidden
              className={cn(
                'h-4 w-4 transition-transform',
                collapsed && 'rotate-180',
              )}
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth={2}
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="m11 17-5-5 5-5" />
              <path d="m18 17-5-5 5-5" />
            </svg>
            {!collapsed && <span>收起导航</span>}
          </button>
        )}
      </div>
    </aside>
  );
});

export default Sidebar;
