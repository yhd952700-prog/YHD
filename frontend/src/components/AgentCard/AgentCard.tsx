import React, { memo } from 'react';
import type { Agent, AgentStatus, AgentType } from '../../types';
import { avatarColor, cn, formatNumber, formatRelativeTime, initials } from '../../utils';

export interface AgentCardProps {
  agent: Agent;
  /** 紧凑模式：隐藏指标与能力标签 */
  compact?: boolean;
  /** 是否处于选中状态 */
  selected?: boolean;
  /** 显示状态徽标 */
  showStatus?: boolean;
  /** 显示指标数据 */
  showMetrics?: boolean;
  onClick?: (agent: Agent) => void;
  onChat?: (agent: Agent) => void;
  onConfigure?: (agent: Agent) => void;
  className?: string;
}

export const AGENT_TYPE_LABELS: Record<AgentType, string> = {
  planner: '规划者',
  executor: '执行者',
  reviewer: '评审者',
  researcher: '研究者',
  custom: '自定义',
};

export const AGENT_TYPE_COLORS: Record<AgentType, string> = {
  planner: 'bg-violet-100 text-violet-700 dark:bg-violet-950 dark:text-violet-300',
  executor: 'bg-blue-100 text-blue-700 dark:bg-blue-950 dark:text-blue-300',
  reviewer: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300',
  researcher: 'bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300',
  custom: 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300',
};

export const AGENT_STATUS_META: Record<AgentStatus, { label: string; dot: string; pulse: boolean }> = {
  online: { label: '在线', dot: 'bg-green-500', pulse: true },
  offline: { label: '离线', dot: 'bg-slate-400', pulse: false },
  busy: { label: '忙碌', dot: 'bg-amber-500', pulse: true },
  error: { label: '异常', dot: 'bg-red-500', pulse: false },
  idle: { label: '空闲', dot: 'bg-sky-400', pulse: true },
};

interface AgentAvatarProps {
  agent: Agent;
  size?: 'sm' | 'md' | 'lg';
  showStatus?: boolean;
}

export const AgentAvatar: React.FC<AgentAvatarProps> = ({ agent, size = 'md', showStatus = true }) => {
  const sizeClass = {
    sm: 'h-8 w-8 text-xs',
    md: 'h-11 w-11 text-sm',
    lg: 'h-16 w-16 text-lg',
  }[size];

  const dotSize = {
    sm: 'h-2 w-2 ring-1',
    md: 'h-3 w-3 ring-2',
    lg: 'h-4 w-4 ring-2',
  }[size];

  const meta = AGENT_STATUS_META[agent.status];

  return (
    <div className="relative shrink-0">
      {agent.avatarUrl ? (
        <img
          src={agent.avatarUrl}
          alt={agent.name}
          className={cn('rounded-full object-cover', sizeClass)}
        />
      ) : (
        <div
          className={cn(
            'flex items-center justify-center rounded-full font-semibold text-white',
            avatarColor(agent.id + agent.name),
            sizeClass,
          )}
        >
          {initials(agent.name)}
        </div>
      )}
      {showStatus && (
        <span
          className={cn(
            'absolute -bottom-0.5 -right-0.5 rounded-full ring-white dark:ring-slate-900',
            meta.dot,
            meta.pulse && 'animate-pulse-soft',
            dotSize,
          )}
          title={meta.label}
        />
      )}
    </div>
  );
};

/**
 * AgentCard - 展示单个 Agent 的信息、状态与关键指标。
 */
export const AgentCard = memo<AgentCardProps>(function AgentCard({
  agent,
  compact = false,
  selected = false,
  showStatus = true,
  showMetrics = true,
  onClick,
  onChat,
  onConfigure,
  className,
}) {
  const statusMeta = AGENT_STATUS_META[agent.status];
  const clickable = Boolean(onClick);

  return (
    <div
      role={clickable ? 'button' : undefined}
      tabIndex={clickable ? 0 : undefined}
      onClick={() => onClick?.(agent)}
      onKeyDown={(e) => {
        if (clickable && (e.key === 'Enter' || e.key === ' ')) {
          e.preventDefault();
          onClick?.(agent);
        }
      }}
      className={cn(
        'lh-card p-4',
        clickable && 'lh-card-hover cursor-pointer',
        selected && 'border-brand-500 ring-2 ring-brand-500/20',
        className,
      )}
    >
      <div className="flex items-start gap-3">
        <AgentAvatar agent={agent} showStatus={showStatus} />

        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h3 className="truncate text-sm font-semibold text-slate-900 dark:text-slate-100">
              {agent.name}
            </h3>
            <span className={cn('lh-badge shrink-0', AGENT_TYPE_COLORS[agent.type])}>
              {AGENT_TYPE_LABELS[agent.type]}
            </span>
          </div>

          {agent.description && !compact && (
            <p className="mt-1 line-clamp-2 text-xs text-slate-500 dark:text-slate-400">
              {agent.description}
            </p>
          )}

          <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-400 dark:text-slate-500">
            {agent.model && <span className="font-mono">{agent.model}</span>}
            <span className="inline-flex items-center gap-1">
              <span className={cn('h-1.5 w-1.5 rounded-full', statusMeta.dot)} />
              {statusMeta.label}
            </span>
            {agent.lastActiveAt && <span>活跃于 {formatRelativeTime(agent.lastActiveAt)}</span>}
          </div>
        </div>
      </div>

      {showMetrics && agent.metrics && !compact && (
        <div className="mt-3 grid grid-cols-3 gap-2 border-t border-slate-100 pt-3 dark:border-slate-800">
          <div>
            <div className="text-xs text-slate-400">完成任务</div>
            <div className="text-sm font-semibold text-slate-800 dark:text-slate-200">
              {formatNumber(agent.metrics.tasksCompleted)}
            </div>
          </div>
          <div>
            <div className="text-xs text-slate-400">成功率</div>
            <div className="text-sm font-semibold text-slate-800 dark:text-slate-200">
              {(agent.metrics.successRate * 100).toFixed(1)}%
            </div>
          </div>
          <div>
            <div className="text-xs text-slate-400">Tokens</div>
            <div className="text-sm font-semibold text-slate-800 dark:text-slate-200">
              {formatNumber(agent.metrics.totalTokens)}
            </div>
          </div>
        </div>
      )}

      {!compact && agent.capabilities.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {agent.capabilities.slice(0, 4).map((cap) => (
            <span
              key={cap.name}
              className="rounded-md bg-slate-100 px-1.5 py-0.5 text-[11px] text-slate-600 dark:bg-slate-800 dark:text-slate-300"
              title={cap.description}
            >
              {cap.name}
            </span>
          ))}
          {agent.capabilities.length > 4 && (
            <span className="rounded-md bg-slate-100 px-1.5 py-0.5 text-[11px] text-slate-400 dark:bg-slate-800">
              +{agent.capabilities.length - 4}
            </span>
          )}
        </div>
      )}

      {(onChat || onConfigure) && !compact && (
        <div className="mt-3 flex gap-2">
          {onChat && (
            <button
              type="button"
              className="lh-btn-primary flex-1 text-xs"
              onClick={(e) => {
                e.stopPropagation();
                onChat(agent);
              }}
            >
              发起对话
            </button>
          )}
          {onConfigure && (
            <button
              type="button"
              className="lh-btn-secondary flex-1 text-xs"
              onClick={(e) => {
                e.stopPropagation();
                onConfigure(agent);
              }}
            >
              配置
            </button>
          )}
        </div>
      )}
    </div>
  );
});
