import { memo, useCallback, useMemo, useState } from 'react';
import type { Plugin, PluginStatus } from '../../types';
import { cn, formatRelativeTime, formatNumber } from '../../utils';

export interface PluginManagerProps {
  plugins: Plugin[];
  loading?: boolean;
  onToggle?: (plugin: Plugin, enabled: boolean) => void;
  onUninstall?: (plugin: Plugin) => void;
  onOpenSettings?: (plugin: Plugin) => void;
  className?: string;
}

const STATUS_META: Record<PluginStatus, { label: string; className: string }> = {
  installed: {
    label: '已安装',
    className:
      'bg-slate-100 text-slate-600 dark:bg-slate-700/50 dark:text-slate-300',
  },
  enabled: {
    label: '已启用',
    className:
      'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-400',
  },
  disabled: {
    label: '已禁用',
    className:
      'bg-slate-100 text-slate-500 dark:bg-slate-700/50 dark:text-slate-400',
  },
  error: {
    label: '错误',
    className: 'bg-red-100 text-red-700 dark:bg-red-500/15 dark:text-red-400',
  },
  update_available: {
    label: '有更新',
    className:
      'bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-400',
  },
};

const PERMISSION_LABELS: Record<string, string> = {
  network: '网络访问',
  filesystem: '文件系统',
  memory: '记忆读写',
  knowledge: '知识库',
  tools: '工具调用',
  agents: 'Agent 管理',
};

function permissionLabel(permission: string): string {
  return PERMISSION_LABELS[permission] ?? permission;
}

interface PluginRowProps {
  plugin: Plugin;
  onToggle?: (plugin: Plugin, enabled: boolean) => void;
  onUninstall?: (plugin: Plugin) => void;
  onOpenSettings?: (plugin: Plugin) => void;
}

const PluginRow = memo(function PluginRow({
  plugin,
  onToggle,
  onUninstall,
  onOpenSettings,
}: PluginRowProps) {
  const { manifest, status } = plugin;
  const enabled = status === 'enabled';
  const meta = STATUS_META[status];

  const handleToggle = useCallback(() => {
    onToggle?.(plugin, !enabled);
  }, [onToggle, plugin, enabled]);

  const handleUninstall = useCallback(() => {
    onUninstall?.(plugin);
  }, [onUninstall, plugin]);

  const handleSettings = useCallback(() => {
    onOpenSettings?.(plugin);
  }, [onOpenSettings, plugin]);

  return (
    <li
      className={cn(
        'flex flex-col gap-3 rounded-xl border border-slate-200 bg-white p-4 transition-shadow',
        'hover:shadow-md dark:border-slate-700 dark:bg-slate-800/60',
        status === 'error' && 'border-red-300 dark:border-red-500/40',
      )}
    >
      <div className="flex items-start gap-3">
        {/* 图标 */}
        <div
          aria-hidden
          className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br from-brand-500 to-brand-700 text-lg text-white"
        >
          🧩
        </div>

        {/* 名称与描述 */}
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h4 className="truncate text-sm font-semibold text-slate-900 dark:text-slate-100">
              {manifest.name}
            </h4>
            <span className="text-xs text-slate-400 dark:text-slate-500">
              v{manifest.version}
            </span>
            <span
              className={cn(
                'inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium',
                meta.className,
              )}
            >
              {meta.label}
            </span>
          </div>
          {manifest.description && (
            <p className="mt-1 line-clamp-2 text-xs text-slate-500 dark:text-slate-400">
              {manifest.description}
            </p>
          )}
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-slate-400 dark:text-slate-500">
            {manifest.author && <span>作者：{manifest.author}</span>}
            <span>安装于 {formatRelativeTime(plugin.installedAt)}</span>
            {typeof plugin.downloads === 'number' && (
              <span>下载 {formatNumber(plugin.downloads)}</span>
            )}
            {typeof plugin.rating === 'number' && (
              <span aria-label={`评分 ${plugin.rating.toFixed(1)}`}>
                ⭐ {plugin.rating.toFixed(1)}
              </span>
            )}
          </div>
        </div>

        {/* 启用开关 */}
        <button
          type="button"
          role="switch"
          aria-checked={enabled}
          aria-label={enabled ? `禁用 ${manifest.name}` : `启用 ${manifest.name}`}
          onClick={handleToggle}
          disabled={status === 'error'}
          className={cn(
            'relative h-6 w-11 shrink-0 rounded-full transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500',
            enabled ? 'bg-brand-600' : 'bg-slate-300 dark:bg-slate-600',
            status === 'error' && 'cursor-not-allowed opacity-40',
          )}
        >
          <span
            aria-hidden
            className={cn(
              'absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition-transform',
              enabled ? 'translate-x-[22px]' : 'translate-x-0.5',
            )}
          />
        </button>
      </div>

      {/* 错误信息 */}
      {status === 'error' && plugin.errorMessage && (
        <p
          role="alert"
          className="rounded-lg bg-red-50 px-3 py-2 text-xs text-red-600 dark:bg-red-500/10 dark:text-red-400"
        >
          {plugin.errorMessage}
        </p>
      )}

      {/* 权限标签 */}
      {manifest.permissions && manifest.permissions.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-[11px] text-slate-400 dark:text-slate-500">
            权限：
          </span>
          {manifest.permissions.map((permission) => (
            <span
              key={permission}
              className="inline-flex items-center rounded-md border border-slate-200 bg-slate-50 px-1.5 py-0.5 text-[11px] text-slate-600 dark:border-slate-600 dark:bg-slate-700/40 dark:text-slate-300"
            >
              {permissionLabel(permission)}
            </span>
          ))}
        </div>
      )}

      {/* 操作 */}
      <div className="flex items-center justify-end gap-2 border-t border-slate-100 pt-2 dark:border-slate-700/60">
        {status === 'update_available' && (
          <button
            type="button"
            className="rounded-lg px-3 py-1.5 text-xs font-medium text-amber-600 transition-colors hover:bg-amber-50 dark:text-amber-400 dark:hover:bg-amber-500/10"
          >
            更新
          </button>
        )}
        <button
          type="button"
          onClick={handleSettings}
          className="rounded-lg px-3 py-1.5 text-xs font-medium text-slate-600 transition-colors hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-700/60"
        >
          设置
        </button>
        <button
          type="button"
          onClick={handleUninstall}
          className="rounded-lg px-3 py-1.5 text-xs font-medium text-red-600 transition-colors hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-500/10"
        >
          卸载
        </button>
      </div>
    </li>
  );
});

export const PluginManager = memo(function PluginManager({
  plugins,
  loading = false,
  onToggle,
  onUninstall,
  onOpenSettings,
  className,
}: PluginManagerProps) {
  const [filter, setFilter] = useState<'all' | PluginStatus>('all');

  const filtered = useMemo(() => {
    if (filter === 'all') return plugins;
    return plugins.filter((plugin) => plugin.status === filter);
  }, [plugins, filter]);

  const counts = useMemo(() => {
    const map = new Map<PluginStatus, number>();
    for (const plugin of plugins) {
      map.set(plugin.status, (map.get(plugin.status) ?? 0) + 1);
    }
    return map;
  }, [plugins]);

  const filters: { key: 'all' | PluginStatus; label: string }[] = [
    { key: 'all', label: `全部 (${plugins.length})` },
    { key: 'enabled', label: `已启用 (${counts.get('enabled') ?? 0})` },
    { key: 'disabled', label: `已禁用 (${counts.get('disabled') ?? 0})` },
    {
      key: 'update_available',
      label: `可更新 (${counts.get('update_available') ?? 0})`,
    },
    { key: 'error', label: `错误 (${counts.get('error') ?? 0})` },
  ];

  return (
    <section
      aria-label="插件管理"
      className={cn(
        'rounded-2xl border border-slate-200 bg-slate-50 p-4 dark:border-slate-700 dark:bg-slate-900/40',
        className,
      )}
    >
      <header className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <h3 className="text-base font-semibold text-slate-900 dark:text-slate-100">
          插件管理
        </h3>
        <div
          role="tablist"
          aria-label="插件状态筛选"
          className="flex flex-wrap gap-1 rounded-lg bg-white p-1 dark:bg-slate-800"
        >
          {filters.map((item) => (
            <button
              key={item.key}
              type="button"
              role="tab"
              aria-selected={filter === item.key}
              onClick={() => setFilter(item.key)}
              className={cn(
                'rounded-md px-2.5 py-1 text-xs font-medium transition-colors',
                filter === item.key
                  ? 'bg-brand-600 text-white'
                  : 'text-slate-500 hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-slate-700/60',
              )}
            >
              {item.label}
            </button>
          ))}
        </div>
      </header>

      {loading ? (
        <ul className="space-y-3" aria-busy="true">
          {Array.from({ length: 3 }).map((_, index) => (
            <li key={index} className="lh-skeleton h-28 rounded-xl" />
          ))}
        </ul>
      ) : filtered.length === 0 ? (
        <p className="py-10 text-center text-sm text-slate-400 dark:text-slate-500">
          暂无插件
        </p>
      ) : (
        <ul className="space-y-3">
          {filtered.map((plugin) => (
            <PluginRow
              key={plugin.id}
              plugin={plugin}
              onToggle={onToggle}
              onUninstall={onUninstall}
              onOpenSettings={onOpenSettings}
            />
          ))}
        </ul>
      )}
    </section>
  );
});

export default PluginManager;
