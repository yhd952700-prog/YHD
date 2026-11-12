import React, { memo, useMemo } from 'react';
import type { HealthLevel, ServiceHealth } from '../../types';
import { cn, formatDuration, formatRelativeTime } from '../../utils';

export interface HealthStatusProps {
  services: ServiceHealth[];
  /** 是否显示整体汇总头 */
  showSummary?: boolean;
  /** 布局：列表 / 紧凑徽标 */
  variant?: 'list' | 'badges' | 'grid';
  loading?: boolean;
  onServiceClick?: (service: ServiceHealth) => void;
  className?: string;
}

export const HEALTH_META: Record<HealthLevel, { label: string; dot: string; text: string; bg: string }> = {
  healthy: {
    label: '正常',
    dot: 'bg-emerald-500',
    text: 'text-emerald-600 dark:text-emerald-400',
    bg: 'bg-emerald-50 dark:bg-emerald-950/50',
  },
  degraded: {
    label: '降级',
    dot: 'bg-amber-500',
    text: 'text-amber-600 dark:text-amber-400',
    bg: 'bg-amber-50 dark:bg-amber-950/50',
  },
  unhealthy: {
    label: '故障',
    dot: 'bg-red-500',
    text: 'text-red-600 dark:text-red-400',
    bg: 'bg-red-50 dark:bg-red-950/50',
  },
  unknown: {
    label: '未知',
    dot: 'bg-slate-400',
    text: 'text-slate-500 dark:text-slate-400',
    bg: 'bg-slate-100 dark:bg-slate-800',
  },
};

const OVERALL_ORDER: HealthLevel[] = ['unhealthy', 'degraded', 'unknown', 'healthy'];

export function computeOverall(services: ServiceHealth[]): HealthLevel {
  if (services.length === 0) return 'unknown';
  const levels = services.map((s) => s.level);
  for (const level of OVERALL_ORDER) {
    if (levels.includes(level)) return level;
  }
  return 'healthy';
}

interface ServiceRowProps {
  service: ServiceHealth;
  onClick?: (service: ServiceHealth) => void;
}

const ServiceRow = memo<ServiceRowProps>(function ServiceRow({ service, onClick }) {
  const meta = HEALTH_META[service.level];
  return (
    <div
      role={onClick ? 'button' : undefined}
      tabIndex={onClick ? 0 : undefined}
      onClick={() => onClick?.(service)}
      onKeyDown={(e) => {
        if (onClick && (e.key === 'Enter' || e.key === ' ')) {
          e.preventDefault();
          onClick(service);
        }
      }}
      className={cn(
        'flex items-center gap-3 rounded-lg px-3 py-2.5 transition-colors',
        onClick && 'cursor-pointer hover:bg-slate-50 dark:hover:bg-slate-800/60',
      )}
    >
      <span className="relative flex h-2.5 w-2.5 shrink-0">
        {service.level === 'healthy' && (
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-60" />
        )}
        <span className={cn('relative inline-flex h-2.5 w-2.5 rounded-full', meta.dot)} />
      </span>

      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="truncate text-sm font-medium text-slate-800 dark:text-slate-200">
            {service.name}
          </span>
          <span className={cn('text-xs font-medium', meta.text)}>{meta.label}</span>
        </div>
        {service.message && (
          <p className="mt-0.5 truncate text-xs text-slate-400">{service.message}</p>
        )}
      </div>

      <div className="flex shrink-0 items-center gap-4 text-xs tabular-nums text-slate-400">
        {service.latencyMs !== undefined && (
          <span title="延迟">{formatDuration(service.latencyMs)}</span>
        )}
        {service.uptime !== undefined && (
          <span title="可用率" className={service.uptime < 0.99 ? 'text-amber-500' : undefined}>
            {(service.uptime * 100).toFixed(2)}%
          </span>
        )}
        <span title="最近检查">{formatRelativeTime(service.lastCheckedAt)}</span>
      </div>
    </div>
  );
});

/**
 * HealthStatus - 系统/服务健康状态指示器。
 */
export const HealthStatus = memo<HealthStatusProps>(function HealthStatus({
  services,
  showSummary = true,
  variant = 'list',
  loading = false,
  onServiceClick,
  className,
}) {
  const overall = useMemo(() => computeOverall(services), [services]);
  const counts = useMemo(() => {
    const c: Record<HealthLevel, number> = { healthy: 0, degraded: 0, unhealthy: 0, unknown: 0 };
    for (const s of services) c[s.level] += 1;
    return c;
  }, [services]);

  if (loading) {
    return (
      <div className={cn('space-y-2', className)}>
        <div className="lh-skeleton h-10 w-full" />
        {Array.from({ length: 3 }).map((_, i) => (
          <div key={i} className="lh-skeleton h-12 w-full" />
        ))}
      </div>
    );
  }

  const overallMeta = HEALTH_META[overall];

  return (
    <div className={cn('flex flex-col gap-2', className)}>
      {showSummary && (
        <div className={cn('flex items-center gap-3 rounded-xl px-4 py-3', overallMeta.bg)}>
          <span className="relative flex h-3 w-3">
            {overall === 'healthy' && (
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-60" />
            )}
            <span className={cn('relative inline-flex h-3 w-3 rounded-full', overallMeta.dot)} />
          </span>
          <div className="flex-1">
            <p className={cn('text-sm font-semibold', overallMeta.text)}>
              {overall === 'healthy'
                ? '所有系统运行正常'
                : overall === 'degraded'
                  ? '部分服务性能降级'
                  : overall === 'unhealthy'
                    ? '检测到服务故障'
                    : '健康状态未知'}
            </p>
            <p className="text-xs text-slate-500 dark:text-slate-400">
              {counts.healthy} 正常 · {counts.degraded} 降级 · {counts.unhealthy} 故障
            </p>
          </div>
        </div>
      )}

      {variant === 'badges' ? (
        <div className="flex flex-wrap gap-2">
          {services.map((service) => {
            const meta = HEALTH_META[service.level];
            return (
              <button
                key={service.id}
                type="button"
                onClick={() => onServiceClick?.(service)}
                className={cn('lh-badge gap-1.5 py-1', meta.bg, meta.text)}
                title={service.message}
              >
                <span className={cn('h-1.5 w-1.5 rounded-full', meta.dot)} />
                {service.name}
              </button>
            );
          })}
        </div>
      ) : variant === 'grid' ? (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          {services.map((service) => {
            const meta = HEALTH_META[service.level];
            return (
              <div
                key={service.id}
                onClick={() => onServiceClick?.(service)}
                className={cn(
                  'flex flex-col gap-1 rounded-lg border border-slate-200 p-3 dark:border-slate-800',
                  onServiceClick && 'cursor-pointer hover:border-slate-300 dark:hover:border-slate-700',
                )}
              >
                <div className="flex items-center gap-1.5">
                  <span className={cn('h-2 w-2 rounded-full', meta.dot)} />
                  <span className="truncate text-sm font-medium text-slate-700 dark:text-slate-200">
                    {service.name}
                  </span>
                </div>
                <span className={cn('text-xs', meta.text)}>{meta.label}</span>
                {service.latencyMs !== undefined && (
                  <span className="text-[10px] tabular-nums text-slate-400">
                    {formatDuration(service.latencyMs)}
                  </span>
                )}
              </div>
            );
          })}
        </div>
      ) : (
        <div className="flex flex-col divide-y divide-slate-100 rounded-xl border border-slate-200 dark:divide-slate-800 dark:border-slate-800">
          {services.map((service) => (
            <ServiceRow key={service.id} service={service} onClick={onServiceClick} />
          ))}
        </div>
      )}
    </div>
  );
});
