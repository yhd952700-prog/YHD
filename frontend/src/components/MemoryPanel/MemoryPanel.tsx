import React, { memo, useCallback, useMemo, useState } from 'react';
import type { MemoryNode, MemoryStats, MemoryType } from '../../types';
import { cn, formatBytes, formatNumber, formatRelativeTime } from '../../utils';

export interface MemoryPanelProps {
  tree: MemoryNode[];
  stats?: MemoryStats | null;
  loading?: boolean;
  /** 受控展开集合 */
  expandedIds?: Set<string>;
  onToggleExpanded?: (id: string) => void;
  selectedNodeId?: string;
  onSelectNode?: (node: MemoryNode) => void;
  onSearch?: (query: string) => void;
  /** 显示顶部统计条 */
  showStats?: boolean;
  className?: string;
}

export const MEMORY_TYPE_META: Record<MemoryType | 'category', { label: string; icon: string; color: string }> = {
  episodic: { label: '情景记忆', icon: '🕘', color: 'text-violet-500' },
  semantic: { label: '语义记忆', icon: '📚', color: 'text-blue-500' },
  procedural: { label: '程序记忆', icon: '⚙️', color: 'text-emerald-500' },
  working: { label: '工作记忆', icon: '⚡', color: 'text-amber-500' },
  category: { label: '分类', icon: '📁', color: 'text-slate-400' },
};

interface TreeNodeProps {
  node: MemoryNode;
  depth: number;
  expandedIds: Set<string>;
  selectedNodeId?: string;
  onToggle: (id: string) => void;
  onSelect?: (node: MemoryNode) => void;
}

const TreeNode = memo<TreeNodeProps>(function TreeNode({
  node,
  depth,
  expandedIds,
  selectedNodeId,
  onToggle,
  onSelect,
}) {
  const hasChildren = Boolean(node.children?.length);
  const expanded = expandedIds.has(node.id);
  const selected = node.id === selectedNodeId;
  const meta = MEMORY_TYPE_META[node.type] ?? MEMORY_TYPE_META.category;

  return (
    <div role="treeitem" aria-expanded={hasChildren ? expanded : undefined} aria-selected={selected}>
      <div
        onClick={() => {
          if (hasChildren) onToggle(node.id);
          onSelect?.(node);
        }}
        className={cn(
          'group flex cursor-pointer items-center gap-1.5 rounded-lg px-2 py-1.5 text-sm transition-colors',
          selected
            ? 'bg-brand-50 text-brand-700 dark:bg-brand-950 dark:text-brand-300'
            : 'text-slate-700 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800',
        )}
        style={{ paddingLeft: `${depth * 16 + 8}px` }}
      >
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onToggle(node.id);
          }}
          className={cn(
            'flex h-4 w-4 shrink-0 items-center justify-center rounded text-slate-400 transition-transform',
            !hasChildren && 'invisible',
          )}
          aria-label={expanded ? '收起' : '展开'}
        >
          <svg
            className={cn('h-3 w-3 transition-transform', expanded && 'rotate-90')}
            viewBox="0 0 20 20"
            fill="currentColor"
          >
            <path
              fillRule="evenodd"
              d="M7.21 14.77a.75.75 0 01.02-1.06L11.168 10 7.23 6.29a.75.75 0 111.04-1.08l4.5 4.25a.75.75 0 010 1.08l-4.5 4.25a.75.75 0 01-1.06-.02z"
              clipRule="evenodd"
            />
          </svg>
        </button>

        <span className="shrink-0 text-sm leading-none" aria-hidden>
          {meta.icon}
        </span>

        <span className="min-w-0 flex-1 truncate">{node.label}</span>

        {node.importance !== undefined && (
          <span
            className="flex shrink-0 items-center gap-0.5"
            title={`重要性 ${(node.importance * 100).toFixed(0)}%`}
          >
            <span className="h-1 w-8 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-700">
              <span
                className={cn(
                  'block h-full rounded-full',
                  node.importance > 0.7
                    ? 'bg-red-400'
                    : node.importance > 0.4
                      ? 'bg-amber-400'
                      : 'bg-slate-400',
                )}
                style={{ width: `${node.importance * 100}%` }}
              />
            </span>
          </span>
        )}

        {node.accessCount !== undefined && (
          <span className="shrink-0 text-[10px] tabular-nums text-slate-400">
            ×{node.accessCount}
          </span>
        )}
      </div>

      {hasChildren && expanded && (
        <div role="group">
          {node.children!.map((child) => (
            <TreeNode
              key={child.id}
              node={child}
              depth={depth + 1}
              expandedIds={expandedIds}
              selectedNodeId={selectedNodeId}
              onToggle={onToggle}
              onSelect={onSelect}
            />
          ))}
        </div>
      )}
    </div>
  );
});

function countNodes(nodes: MemoryNode[]): number {
  return nodes.reduce((sum, n) => sum + 1 + (n.children ? countNodes(n.children) : 0), 0);
}

/**
 * MemoryPanel - 记忆树形面板：层级浏览、重要性展示、搜索过滤与统计。
 */
export const MemoryPanel = memo<MemoryPanelProps>(function MemoryPanel({
  tree,
  stats,
  loading = false,
  expandedIds: controlledExpanded,
  onToggleExpanded,
  selectedNodeId,
  onSelectNode,
  onSearch,
  showStats = true,
  className,
}) {
  const [innerExpanded, setInnerExpanded] = useState<Set<string>>(new Set());
  const [query, setQuery] = useState('');

  const expandedIds = controlledExpanded ?? innerExpanded;

  const handleToggle = useCallback(
    (id: string) => {
      if (onToggleExpanded) {
        onToggleExpanded(id);
      } else {
        setInnerExpanded((prev) => {
          const next = new Set(prev);
          if (next.has(id)) next.delete(id);
          else next.add(id);
          return next;
        });
      }
    },
    [onToggleExpanded],
  );

  const filteredTree = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return tree;
    const walk = (node: MemoryNode): MemoryNode | null => {
      const self = node.label.toLowerCase().includes(q) || node.content?.toLowerCase().includes(q);
      const children = (node.children ?? []).map(walk).filter((n): n is MemoryNode => n !== null);
      if (self || children.length > 0) return { ...node, children };
      return null;
    };
    return tree.map(walk).filter((n): n is MemoryNode => n !== null);
  }, [tree, query]);

  const totalNodes = useMemo(() => countNodes(tree), [tree]);

  return (
    <div className={cn('flex h-full flex-col overflow-hidden rounded-xl border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900', className)}>
      <header className="border-b border-slate-200 p-3 dark:border-slate-800">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-slate-800 dark:text-slate-100">记忆系统</h3>
          <span className="text-xs tabular-nums text-slate-400">{formatNumber(totalNodes)} 条</span>
        </div>

        <div className="relative mt-2">
          <input
            type="search"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              onSearch?.(e.target.value);
            }}
            placeholder="搜索记忆…"
            className="lh-input py-1.5 pl-8 text-xs"
          />
          <svg
            className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400"
            viewBox="0 0 20 20"
            fill="currentColor"
          >
            <path
              fillRule="evenodd"
              d="M9 3.5a5.5 5.5 0 100 11 5.5 5.5 0 000-11zM2 9a7 7 0 1112.452 4.391l3.328 3.329a.75.75 0 11-1.06 1.06l-3.329-3.328A7 7 0 012 9z"
              clipRule="evenodd"
            />
          </svg>
        </div>
      </header>

      {showStats && stats && (
        <div className="grid grid-cols-4 gap-px border-b border-slate-200 bg-slate-100 dark:border-slate-800 dark:bg-slate-800">
          {(Object.keys(MEMORY_TYPE_META) as (MemoryType | 'category')[])
            .filter((t) => t !== 'category')
            .map((type) => (
              <div key={type} className="bg-white px-2 py-2 text-center dark:bg-slate-900">
                <div className={cn('text-sm font-semibold tabular-nums', MEMORY_TYPE_META[type].color)}>
                  {formatNumber(stats.byType[type as MemoryType] ?? 0)}
                </div>
                <div className="text-[10px] text-slate-400">{MEMORY_TYPE_META[type].label}</div>
              </div>
            ))}
        </div>
      )}

      <div className="flex-1 overflow-y-auto p-2" role="tree" aria-label="记忆树">
        {loading ? (
          <div className="space-y-2 p-2">
            {Array.from({ length: 8 }).map((_, i) => (
              <div key={i} className="lh-skeleton h-7" style={{ width: `${85 - (i % 4) * 12}%` }} />
            ))}
          </div>
        ) : filteredTree.length === 0 ? (
          <div className="flex h-full items-center justify-center text-xs text-slate-400">
            {query ? '没有匹配的记忆' : '暂无记忆数据'}
          </div>
        ) : (
          filteredTree.map((node) => (
            <TreeNode
              key={node.id}
              node={node}
              depth={0}
              expandedIds={expandedIds}
              selectedNodeId={selectedNodeId}
              onToggle={handleToggle}
              onSelect={onSelectNode}
            />
          ))
        )}
      </div>

      {stats && (
        <footer className="flex items-center justify-between border-t border-slate-200 px-3 py-2 text-[10px] text-slate-400 dark:border-slate-800">
          <span>存储 {formatBytes(stats.storageBytes)}</span>
          <span>平均重要性 {(stats.avgImportance * 100).toFixed(0)}%</span>
          <span title="最后更新">{formatRelativeTime(new Date().toISOString())}</span>
        </footer>
      )}
    </div>
  );
});
