import React, { memo, useMemo } from 'react';
import type { MetricTrendPoint } from '../../types';
import { cn, formatBytes, formatCurrency, formatDuration, formatNumber } from '../../utils';

export interface MetricCardProps {
  label: string;
  value: number;
  /** 数字格式 */
  format?: 'number' | 'currency' | 'percent' | 'duration' | 'bytes';
  unit?: string;
  /** 环比变化（小数，如 0.12 表示 +12%） */
  deltaPercent?: number;
  /** 趋势是否取反（值下降为好，如成本） */
  invertTrend?: boolean;
  /** 迷你趋势数据 */
  trend?: MetricTrendPoint[];
  /** 目标值（显示进度） */
  target?: number;
  icon?: React.ReactNode;
  loading?: boolean;
  onClick?: () => void;
  className?: string;
}

function formatValue(value: number, format: MetricCardProps['format'], unit?: string): string {
  switch (format) {
    case 'currency':
      return formatCurrency(value);
    case 'percent':
      return `${(value * 100).toFixed(1)}%`;
    case 'duration':
      return formatDuration(value);
    case 'bytes':
      return formatBytes(value);
    default:
      return `${formatNumber(value)}${unit ? ` ${unit}` : ''}`;
  }
}

interface SparklineProps {
  data: MetricTrendPoint[];
  positive: boolean;
  width?: number;
  height?: number;
}

/** 纯 SVG 迷你趋势图 */
export const Sparkline: React.FC<SparklineProps> = ({
  data,
  positive,
  width = 96,
  height = 32,
}) => {
  const { path, area } = useMemo(() => {
    if (data.length < 2) return { path: '', area: '' };
    const values = data.map((d) => d.value);
    const min = Math.min(...values);
    const max = Math.max(...values);
    const range = max - min || 1;
    const stepX = width / (data.length - 1);
    const pad = 2;

    const points = data.map((d, i) => {
      const x = i * stepX;
      const y = pad + (1 - (d.value - min) / range) * (height - pad * 2);
      return [x, y] as const;
    });

    const linePath = points
      .map(([x, y], i) => `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`)
      .join(' ');
    const areaPath = `${linePath} L${width},${height} L0,${height} Z`;
    return { path: linePath, area: areaPath };
  }, [data, width, height]);

  if (!path) return null;

  const stroke = positive ? '#10b981' : '#ef4444';

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className="overflow-visible"
      aria-hidden
    >
      <path d={area} fill={stroke} opacity={0.12} />
      <path d={path} fill="none" stroke={stroke} strokeWidth={1.5} strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
};

/**
 * MetricCard - 指标卡片：数值 + 环比变化 + 迷你趋势图。
 */
export const MetricCard = memo<MetricCardProps>(function MetricCard({
  label,
  value,
  format = 'number',
  unit,
  deltaPercent,
  invertTrend = false,
  trend,
  target,
  icon,
  loading = false,
  onClick,
  className,
}) {
  const trendPositive = useMemo(() => {
    if (deltaPercent === undefined) return true;
    const good = invertTrend ? deltaPercent <= 0 : deltaPercent >= 0;
    return good;
  }, [deltaPercent, invertTrend]);

  if (loading) {
    return (
      <div className={cn('lh-card p-4', className)}>
        <div className="lh-skeleton h-3 w-16" />
        <div className="lh-skeleton mt-2 h-7 w-24" />
        <div className="lh-skeleton mt-2 h-8 w-full" />
      </div>
    );
  }

  return (
    <div
      role={onClick ? 'button' : undefined}
      tabIndex={onClick ? 0 : undefined}
      onClick={onClick}
      onKeyDown={(e) => {
        if (onClick && (e.key === 'Enter' || e.key === ' ')) {
          e.preventDefault();
          onClick();
        }
      }}
      className={cn('lh-card p-4', onClick && 'lh-card-hover cursor-pointer', className)}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-xs font-medium text-slate-500 dark:text-slate-400">{label}</p>
          <p className="mt-1 text-2xl font-bold tabular-nums text-slate-900 dark:text-slate-50">
            {formatValue(value, format, unit)}
          </p>
        </div>
        {icon && (
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-brand-50 text-brand-500 dark:bg-brand-950">
            {icon}
          </div>
        )}
      </div>

      <div className="mt-2 flex items-end justify-between gap-2">
        <div className="flex flex-col gap-1">
          {deltaPercent !== undefined && (
            <span
              className={cn(
                'inline-flex items-center gap-0.5 text-xs font-medium',
                trendPositive ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-500',
              )}
            >
              <svg
                className={cn('h-3 w-3', deltaPercent < 0 && 'rotate-180')}
                viewBox="0 0 20 20"
                fill="currentColor"
              >
                <path
                  fillRule="evenodd"
                  d="M10 17a.75.75 0 01-.75-.75V5.612L5.29 9.77a.75.75 0 01-1.08-1.04l5.25-5.5a.75.75 0 011.08 0l5.25 5.5a.75.75 0 11-1.08 1.04l-3.96-4.158V16.25A.75.75 0 0110 17z"
                  clipRule="evenodd"
                />
              </svg>
              {Math.abs(deltaPercent * 100).toFixed(1)}%
              <span className="font-normal text-slate-400">环比</span>
            </span>
          )}

          {target !== undefined && target > 0 && (
            <div className="flex items-center gap-1.5">
              <div className="h-1 w-16 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
                <div
                  className={cn(
                    'h-full rounded-full',
                    value / target >= 1 ? 'bg-emerald-500' : 'bg-brand-500',
                  )}
                  style={{ width: `${Math.min(100, (value / target) * 100)}%` }}
                />
              </div>
              <span className="text-[10px] text-slate-400">
                目标 {formatValue(target, format, unit)}
              </span>
            </div>
          )}
        </div>

        {trend && trend.length > 1 && (
          <Sparkline
            data={trend}
            positive={
              invertTrend
                ? trend[trend.length - 1].value <= trend[0].value
                : trend[trend.length - 1].value >= trend[0].value
            }
          />
        )}
      </div>
    </div>
  );
});
