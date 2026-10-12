import { memo, useCallback, useEffect, useRef, useState } from 'react';
import { avatarColor, cn, initials } from '../../utils';

export interface HeaderUser {
  name: string;
  email?: string;
  avatarUrl?: string;
  role?: string;
}

export interface HeaderProps {
  title?: string;
  logo?: React.ReactNode;
  user?: HeaderUser;
  center?: React.ReactNode;
  actions?: React.ReactNode;
  userMenuItems?: { id: string; label: string; icon?: string; danger?: boolean }[];
  onUserMenuSelect?: (id: string) => void;
  sticky?: boolean;
  className?: string;
}

interface UserMenuProps {
  user: HeaderUser;
  items: { id: string; label: string; icon?: string; danger?: boolean }[];
  onSelect?: (id: string) => void;
}

const UserMenu = memo(function UserMenu({ user, items, onSelect }: UserMenuProps) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  const toggle = useCallback(() => {
    setOpen((prev) => !prev);
  }, []);

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

  const handleSelect = useCallback(
    (id: string) => () => {
      onSelect?.(id);
      setOpen(false);
    },
    [onSelect],
  );

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        onClick={toggle}
        aria-label="用户菜单"
        aria-expanded={open}
        aria-haspopup="menu"
        className="flex items-center gap-2 rounded-xl p-1.5 transition-colors hover:bg-slate-100 dark:hover:bg-slate-700/60"
      >
        {user.avatarUrl ? (
          <img
            src={user.avatarUrl}
            alt={user.name}
            className="h-8 w-8 rounded-full object-cover"
          />
        ) : (
          <span
            aria-hidden
            className={cn(
              'flex h-8 w-8 items-center justify-center rounded-full text-xs font-semibold text-white',
              avatarColor(user.name),
            )}
          >
            {initials(user.name)}
          </span>
        )}
        <span className="hidden text-left sm:block">
          <span className="block max-w-[120px] truncate text-sm font-medium text-slate-800 dark:text-slate-100">
            {user.name}
          </span>
          {user.role && (
            <span className="block text-[11px] text-slate-400 dark:text-slate-500">
              {user.role}
            </span>
          )}
        </span>
        <svg
          aria-hidden
          className={cn(
            'h-3.5 w-3.5 text-slate-400 transition-transform',
            open && 'rotate-180',
          )}
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={2}
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="m6 9 6 6 6-6" />
        </svg>
      </button>

      {open && (
        <div
          role="menu"
          aria-label="用户菜单"
          className="absolute right-0 z-50 mt-2 w-52 origin-top-right rounded-2xl border border-slate-200 bg-white py-1.5 shadow-xl dark:border-slate-700 dark:bg-slate-800"
        >
          <div className="border-b border-slate-100 px-4 pb-2.5 pt-1 dark:border-slate-700/60">
            <p className="truncate text-sm font-medium text-slate-900 dark:text-slate-100">
              {user.name}
            </p>
            {user.email && (
              <p className="truncate text-xs text-slate-400 dark:text-slate-500">
                {user.email}
              </p>
            )}
          </div>
          <ul className="pt-1">
            {items.map((item) => (
              <li key={item.id}>
                <button
                  type="button"
                  role="menuitem"
                  onClick={handleSelect(item.id)}
                  className={cn(
                    'flex w-full items-center gap-2.5 px-4 py-2 text-sm transition-colors',
                    item.danger
                      ? 'text-red-600 hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-500/10'
                      : 'text-slate-600 hover:bg-slate-50 dark:text-slate-300 dark:hover:bg-slate-700/40',
                  )}
                >
                  {item.icon && <span aria-hidden>{item.icon}</span>}
                  {item.label}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
});

const DEFAULT_USER_MENU = [
  { id: 'profile', label: '个人资料', icon: '👤' },
  { id: 'settings', label: '偏好设置', icon: '⚙️' },
  { id: 'logout', label: '退出登录', icon: '🚪', danger: true },
];

export const Header = memo(function Header({
  title = 'LiuHao AI OS',
  logo,
  user,
  center,
  actions,
  userMenuItems = DEFAULT_USER_MENU,
  onUserMenuSelect,
  sticky = true,
  className,
}: HeaderProps) {
  return (
    <header
      className={cn(
        'z-40 flex h-14 items-center gap-4 border-b border-slate-200 bg-white/90 px-4 backdrop-blur dark:border-slate-700 dark:bg-slate-900/80',
        sticky && 'sticky top-0',
        className,
      )}
    >
      {/* 左侧：Logo + 标题 */}
      <div className="flex min-w-0 shrink-0 items-center gap-2.5">
        {logo ?? (
          <span
            aria-hidden
            className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-brand-500 to-brand-700 text-sm font-bold text-white"
          >
            鎏
          </span>
        )}
        <h1 className="truncate text-base font-semibold text-slate-900 dark:text-slate-100">
          {title}
        </h1>
      </div>

      {/* 中间插槽（如 SearchBar） */}
      {center && <div className="min-w-0 flex-1 px-2">{center}</div>}
      {!center && <div className="flex-1" />}

      {/* 右侧操作区 */}
      <div className="flex shrink-0 items-center gap-1.5">
        {actions}
        {user && (
          <>
            <span
              aria-hidden
              className="mx-1 hidden h-6 w-px bg-slate-200 dark:bg-slate-700 sm:block"
            />
            <UserMenu
              user={user}
              items={userMenuItems}
              onSelect={onUserMenuSelect}
            />
          </>
        )}
      </div>
    </header>
  );
});

export default Header;
