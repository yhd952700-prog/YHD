import React, { memo, useMemo, useState } from 'react';
import type { CostEntry } from '../../types';
import { cn, formatCurrency, formatNumber } from '../../utils';

export interface CostTrackerProps {
  entries: CostEntry[];
  /** 预算上限（美元），超出显示警告 */
  budgetUsd?: number;
  /** 分组维度 */
  defaultGroupBy?: 'model' | 'provider' | 'agent' | 'day';
  loading?: boolean;
  /** 图表高度 */
  height?: number;
  className?: string;
}

type GroupBy = 'model' | 'provider' | 'agent' | 'day';

const GROUP_LABELS: Record<GroupBy, string> = {
  model: '按模型',
  provider: '按提供商',
  agent: '按 Agent',
  day: '按日期',
};

const PALETTE = [
  '#5e6ef6',
  '#10b981',
  '#f59e0b',
  '#ef4444',
  '#8b5cf6',
  '#06b6d4',
  '#ec4899',
  '#84cc16',
  '#f97316',
  '#64748b',
];

interface BarDatum {
  key: string;
  value: number;
  tokens: number;
  color: string;
}

function dayKey(iso: string): string {
  const d = new Date(iso);
  return `${d.getMonth() + 1}/${d.getDate()}`;
}

/**
 * CostTracker - 成本追踪：分组柱状图 + 预算进度 + 明细统计。
 */
export const CostTracker = memo<CostTrackerProps>(function CostTracker({
  entries,
  budgetUsd,
  defaultGroupBy = 'model',
  loading = false,
  height = 180,
  className,
}) {
  const [groupBy, setGroupBy] = useState<GroupBy>(defaultGroupBy);

  const { bars, totalCost, totalTokens } = useMemo(() => {
    const map = new Map<string, { value: number; tokens: number }>();
    let cost = 0;
    let tokens = 0;

    for (const entry of entries) {
      cost += entry.costUsd;
      tokens += entry.inputTokens + entry.outputTokens;
      let key: string;
      switch (groupBy) {
        case 'model':
          key = entry.model;
          break;
        case 'provider':
          key = entry.provider;
          break;
        case 'agent':
          key = entry.agentId ?? '未分配';
          break;
        default:
          key = dayKey(entry.timestamp);
      }
      const prev = map.get(key) ?? { value: 0, tokens: 0 };
      map.set(key, {
        value: prev.value + entry.costUsd,
        tokens: prev.tokens + entry.inputTokens + entry.outputTokens,
      });
    }

    const sorted = [...map.entries()].sort((a, b) =>
      groupBy === 'day' ? 0 : b[1].value - a[1].value,
    );
    const limited = groupBy === 'day' ? sorted : sorted.slice(0, 8);

    return {
      bars: limited.map(
        ([key, v], i): BarDatum => ({
          key,
          value: v.value,
          tokens: v.tokens,
          color: PALETTE[i % PALETTE.length],
        }),
      ),
      totalCost: cost,
      totalTokens: tokens,
    };
  }, [entries, groupBy]);

  const maxValue = useMemo(() => Math.max(...bars.map((b) => b.value), 0), [bars]);
  const budgetRatio = budgetUsd ? Math.min(1, totalCost / budgetUsd) : null;
  const overBudget = budgetUsd !== undefined && totalCost > budgetUsd;

  if (loading) {
    return (
      <div className={cn('flex flex-col gap-3', className)}>
        <div className="lh-skeleton h-6 w-32" />
        <div className="lh-skeleton" style={{ height }} />
        <div className="lh-skeleton h-4 w-full" />
      </div>
    );
  }

  return (
    <div className={cn('flex flex-col gap-4', className)}>
      {/* 汇总 + 分组切换 */}
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-xs text-slate-400">总成本</p>
          <p className={cn('text-2xl font-bold tabular-nums', overBudget ? 'text-red-500' : 'text-slate-900 dark:text-slate-50')}>
            {formatCurrency(totalCost)}
          </p>
          <p className="text-xs tabular-nums text-slate-400">{formatNumber(totalTokens)} tokens</p>
        </div>

        <div className="flex rounded-lg border border-slate-200 p-0.5 dark:border-slate-700">
          {(Object.keys(GROUP_LABELS) as GroupBy[]).map((g) => (
            <button
              key={g}
              type="button"
              onClick={() => setGroupBy(g)}
              className={cn(
                'rounded-md px-2.5 py-1 text-xs font-medium transition-colors',
                groupBy === g
                  ? 'bg-brand-600 text-white'
                  : 'text-slate-500 hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-slate-800',
              )}
            >
              {GROUP_LABELS[g]}
            </button>
          ))}
        </div>
      </div>

      {/* 预算进度 */}
      {budgetUsd !== undefined && (
        <div>
          <div className="mb-1 flex items-center justify-between text-xs">
            <span className="text-slate-500 dark:text-slate-400">预算使用</span>
            <span className={cn('tabular-nums font-medium', overBudget ? 'text-red-500' : 'text-slate-600 dark:text-slate-300')}>
              {formatCurrency(totalCost)} / {formatCurrency(budgetUsd)}
              {overBudget && ' · 已超支'}
            </span>
          </div>
          <div className="h-2 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
            <div
              className={cn('h-full rounded-full transition-all', overBudget ? 'bg-red-500' : budgetRatio! > 0.8 ? 'bg-amber-500' : 'bg-emerald-500')}
              style={{ width: `${(budgetRatio ?? 0) * 100}%` }}
            />
          </div>
        </div>
      )}

      {/* 柱状图 */}
      {bars.length === 0 ? (
        <div className="flex items-center justify-center rounded-lg border border-dashed border-slate-300 text-sm text-slate-400 dark:border-slate-700" style={{ height }}>
          暂无成本数据
        </div>
      ) : (
        <div className="flex items-end gap-2" style={{ height }} role="img" aria-label="成本柱状图">
          {bars.map((bar) => (
            <div key={bar.key} className="group flex min-w-0 flex-1 flex-col items-center justify-end gap-1">
              <span className="text-[10px] tabular-nums text-slate-400 opacity-0 transition-opacity group-hover:opacity-100">
                {formatCurrency(bar.value)}
              </span>
              <div
                className="w-full max-w-14 rounded-t-md transition-all group-hover:opacity-80"
                style={{
                  height: `${maxValue > 0 ? Math.max(2, (bar.value / maxValue) * (height - 46)) : 2}px`,
                  backgroundColor: bar.color,
                }}
                title={`${bar.key}: ${formatCurrency(bar.value)} · ${formatNumber(bar.tokens)} tokens`}
              />
              <span className="w-full truncate text-center text-[10px] text-slate-400" title={bar.key}>
                {bar.key}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* 图例 / 明细 */}
      {bars.length > 0 && (
        <ul className="grid grid-cols-2 gap-x-4 gap-y-1.5 sm:grid-cols-3">
          {bars.slice(0, 6).map((bar) => (
            <li key={bar.key} className="flex items-center gap-1.5 text-xs">
              <span className="h-2 w-2 shrink-0 rounded-sm" style={{ backgroundColor: bar.color }} />
              <span className="min-w-0 flex-1 truncate text-slate-600 dark:text-slate-300">{bar.key}</span>
              <span className="shrink-0 tabular-nums text-slate-400">{formatCurrency(bar.value)}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
});
