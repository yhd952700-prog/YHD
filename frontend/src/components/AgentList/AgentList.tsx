import React, { memo, useMemo, useState } from 'react';
import type { Agent, AgentFilter, AgentStatus, AgentType } from '../../types';
import { AgentCard, AGENT_STATUS_META, AGENT_TYPE_LABELS } from '../AgentCard';
import { cn } from '../../utils';

export interface AgentListProps {
  agents: Agent[];
  loading?: boolean;
  /** 空列表提示 */
  emptyText?: string;
  /** 布局：网格 / 列表 */
  layout?: 'grid' | 'list';
  /** 是否显示内置过滤工具栏 */
  showToolbar?: boolean;
  /** 受控过滤条件 */
  filter?: AgentFilter;
  onFilterChange?: (filter: AgentFilter) => void;
  selectedAgentId?: string;
  onAgentClick?: (agent: Agent) => void;
  onAgentChat?: (agent: Agent) => void;
  onAgentConfigure?: (agent: Agent) => void;
  className?: string;
}

const ALL_TYPES: AgentType[] = ['planner', 'executor', 'reviewer', 'researcher', 'custom'];
const ALL_STATUSES: AgentStatus[] = ['online', 'busy', 'idle', 'offline', 'error'];

function applyFilter(agents: Agent[], filter: AgentFilter): Agent[] {
  const search = filter.search?.trim().toLowerCase();
  return agents.filter((agent) => {
    if (search) {
      const haystack = [agent.name, agent.description, agent.model, ...(agent.tags ?? [])]
        .filter(Boolean)
        .join(' ')
        .toLowerCase();
      if (!haystack.includes(search)) return false;
    }
    if (filter.types?.length && !filter.types.includes(agent.type)) return false;
    if (filter.statuses?.length && !filter.statuses.includes(agent.status)) return false;
    return true;
  });
}

function SkeletonCard() {
  return (
    <div className="lh-card p-4">
      <div className="flex items-start gap-3">
        <div className="lh-skeleton h-11 w-11 rounded-full" />
        <div className="flex-1 space-y-2">
          <div className="lh-skeleton h-4 w-1/3" />
          <div className="lh-skeleton h-3 w-2/3" />
          <div className="lh-skeleton h-3 w-1/2" />
        </div>
      </div>
    </div>
  );
}

/**
 * AgentList - Agent 网格/列表，内置搜索与类型、状态过滤。
 */
export const AgentList = memo<AgentListProps>(function AgentList({
  agents,
  loading = false,
  emptyText = '暂无 Agent',
  layout = 'grid',
  showToolbar = true,
  filter: controlledFilter,
  onFilterChange,
  selectedAgentId,
  onAgentClick,
  onAgentChat,
  onAgentConfigure,
  className,
}) {
  const [innerFilter, setInnerFilter] = useState<AgentFilter>({});
  const filter = controlledFilter ?? innerFilter;

  const updateFilter = (patch: Partial<AgentFilter>) => {
    const next = { ...filter, ...patch };
    if (onFilterChange) onFilterChange(next);
    else setInnerFilter(next);
  };

  const filtered = useMemo(() => applyFilter(agents, filter), [agents, filter]);

  const toggleIn = <T extends string>(list: T[] | undefined, value: T): T[] =>
    list?.includes(value) ? list.filter((v) => v !== value) : [...(list ?? []), value];

  return (
    <div className={cn('flex flex-col gap-4', className)}>
      {showToolbar && (
        <div className="flex flex-col gap-3">
          <div className="relative">
            <svg
              className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400"
              viewBox="0 0 20 20"
              fill="currentColor"
            >
              <path
                fillRule="evenodd"
                d="M9 3.5a5.5 5.5 0 100 11 5.5 5.5 0 000-11zM2 9a7 7 0 1112.452 4.391l3.328 3.329a.75.75 0 11-1.06 1.06l-3.329-3.328A7 7 0 012 9z"
                clipRule="evenodd"
              />
            </svg>
            <input
              type="search"
              value={filter.search ?? ''}
              onChange={(e) => updateFilter({ search: e.target.value })}
              placeholder="搜索 Agent 名称、模型或标签…"
              className="lh-input pl-9"
            />
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs text-slate-400">类型</span>
            {ALL_TYPES.map((type) => (
              <button
                key={type}
                type="button"
                onClick={() => updateFilter({ types: toggleIn(filter.types, type) })}
                className={cn('lh-chip', filter.types?.includes(type) && 'lh-chip-active')}
              >
                {AGENT_TYPE_LABELS[type]}
              </button>
            ))}
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs text-slate-400">状态</span>
            {ALL_STATUSES.map((status) => (
              <button
                key={status}
                type="button"
                onClick={() => updateFilter({ statuses: toggleIn(filter.statuses, status) })}
                className={cn('lh-chip', filter.statuses?.includes(status) && 'lh-chip-active')}
              >
                <span className={cn('h-1.5 w-1.5 rounded-full', AGENT_STATUS_META[status].dot)} />
                {AGENT_STATUS_META[status].label}
              </button>
            ))}
          </div>
        </div>
      )}

      {loading ? (
        <div
          className={cn(
            layout === 'grid'
              ? 'grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3'
              : 'flex flex-col gap-3',
          )}
        >
          {Array.from({ length: 6 }).map((_, i) => (
            <SkeletonCard key={i} />
          ))}
        </div>
      ) : filtered.length === 0 ? (
        <div className="flex flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-slate-300 py-16 text-slate-400 dark:border-slate-700">
          <svg className="h-10 w-10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M15.182 16.318A4.486 4.486 0 0012.016 15a4.486 4.486 0 00-3.198 1.318M21 12a9 9 0 11-18 0 9 9 0 0118 0zM9.75 9.75c0 .414-.168.75-.375.75S9 10.164 9 9.75 9.168 9 9.375 9s.375.336.375.75zm5.25 0c0 .414-.168.75-.375.75s-.375-.336-.375-.75.168-.75.375-.75.375.336.375.75z"
            />
          </svg>
          <p className="text-sm">{emptyText}</p>
        </div>
      ) : (
        <div
          className={cn(
            'animate-fade-in',
            layout === 'grid'
              ? 'grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3'
              : 'flex flex-col gap-3',
          )}
        >
          {filtered.map((agent) => (
            <AgentCard
              key={agent.id}
              agent={agent}
              compact={layout === 'list'}
              selected={agent.id === selectedAgentId}
              onClick={onAgentClick}
              onChat={onAgentChat}
              onConfigure={onAgentConfigure}
            />
          ))}
        </div>
      )}

      {!loading && filtered.length > 0 && (
        <div className="text-right text-xs text-slate-400">
          共 {filtered.length} / {agents.length} 个 Agent
        </div>
      )}
    </div>
  );
});
