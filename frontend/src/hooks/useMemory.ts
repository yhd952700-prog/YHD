import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { MemoryEntry, MemoryNode, MemoryStats, MemoryType } from '../types';

export interface UseMemoryOptions {
  fetchTree?: () => Promise<MemoryNode[]>;
  fetchStats?: () => Promise<MemoryStats>;
  onSearch?: (query: string) => Promise<MemoryEntry[]>;
  autoLoad?: boolean;
}

export interface UseMemoryResult {
  tree: MemoryNode[];
  stats: MemoryStats | null;
  loading: boolean;
  error: Error | null;
  expandedIds: Set<string>;
  selectedNode: MemoryNode | null;
  searchQuery: string;
  searchResults: MemoryEntry[];
  searching: boolean;
  toggleExpanded: (id: string) => void;
  expandAll: () => void;
  collapseAll: () => void;
  selectNode: (node: MemoryNode | null) => void;
  setSearchQuery: (query: string) => void;
  refresh: () => Promise<void>;
}

function collectIds(nodes: MemoryNode[], acc: string[] = []): string[] {
  for (const node of nodes) {
    acc.push(node.id);
    if (node.children) collectIds(node.children, acc);
  }
  return acc;
}

function filterTree(nodes: MemoryNode[], query: string): MemoryNode[] {
  const lower = query.toLowerCase();
  const walk = (node: MemoryNode): MemoryNode | null => {
    const selfMatch =
      node.label.toLowerCase().includes(lower) || node.content?.toLowerCase().includes(lower);
    const children = (node.children ?? []).map(walk).filter((n): n is MemoryNode => n !== null);
    if (selfMatch || children.length > 0) {
      return { ...node, children };
    }
    return null;
  };
  return nodes.map(walk).filter((n): n is MemoryNode => n !== null);
}

/**
 * useMemory - 管理记忆树、展开状态、统计与搜索。
 */
export function useMemory(options: UseMemoryOptions = {}): UseMemoryResult {
  const { fetchTree, fetchStats, onSearch, autoLoad = true } = options;

  const [rawTree, setRawTree] = useState<MemoryNode[]>([]);
  const [stats, setStats] = useState<MemoryStats | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());
  const [selectedNode, setSelectedNode] = useState<MemoryNode | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<MemoryEntry[]>([]);
  const [searching, setSearching] = useState(false);

  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [treeData, statsData] = await Promise.all([
        fetchTree ? fetchTree() : Promise.resolve([]),
        fetchStats ? fetchStats() : Promise.resolve(null),
      ]);
      if (!mountedRef.current) return;
      setRawTree(treeData);
      setStats(statsData);
      // 默认展开第一层
      setExpandedIds(new Set(treeData.map((n) => n.id)));
    } catch (e) {
      if (mountedRef.current) setError(e instanceof Error ? e : new Error(String(e)));
    } finally {
      if (mountedRef.current) setLoading(false);
    }
  }, [fetchTree, fetchStats]);

  useEffect(() => {
    if (autoLoad) void refresh();
  }, [autoLoad, refresh]);

  // 防抖搜索
  useEffect(() => {
    if (!searchQuery.trim() || !onSearch) {
      setSearchResults([]);
      return;
    }
    setSearching(true);
    const timer = window.setTimeout(async () => {
      try {
        const results = await onSearch(searchQuery);
        if (mountedRef.current) setSearchResults(results);
      } catch {
        if (mountedRef.current) setSearchResults([]);
      } finally {
        if (mountedRef.current) setSearching(false);
      }
    }, 300);
    return () => window.clearTimeout(timer);
  }, [searchQuery, onSearch]);

  const tree = useMemo(
    () => (searchQuery.trim() ? filterTree(rawTree, searchQuery) : rawTree),
    [rawTree, searchQuery],
  );

  const toggleExpanded = useCallback((id: string) => {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const expandAll = useCallback(() => {
    setExpandedIds(new Set(collectIds(rawTree)));
  }, [rawTree]);

  const collapseAll = useCallback(() => {
    setExpandedIds(new Set());
  }, []);

  const selectNode = useCallback((node: MemoryNode | null) => {
    setSelectedNode(node);
  }, []);

  return {
    tree,
    stats,
    loading,
    error,
    expandedIds,
    selectedNode,
    searchQuery,
    searchResults,
    searching,
    toggleExpanded,
    expandAll,
    collapseAll,
    selectNode,
    setSearchQuery,
    refresh,
  };
}
