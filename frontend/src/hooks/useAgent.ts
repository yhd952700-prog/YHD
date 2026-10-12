import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { Agent, AgentFilter, AgentStatus, AgentType } from '../types';

export interface UseAgentOptions {
  /** 数据获取函数，默认返回空列表（便于纯 UI 场景使用） */
  fetcher?: (filter?: AgentFilter) => Promise<Agent[]>;
  /** 初始过滤条件 */
  initialFilter?: AgentFilter;
  /** 轮询间隔（毫秒），0 表示不轮询 */
  pollIntervalMs?: number;
  /** 是否在挂载时自动加载 */
  autoLoad?: boolean;
}

export interface UseAgentResult {
  agents: Agent[];
  filteredAgents: Agent[];
  filter: AgentFilter;
  loading: boolean;
  error: Error | null;
  selectedAgent: Agent | null;
  setFilter: (filter: AgentFilter) => void;
  setSearch: (search: string) => void;
  toggleType: (type: AgentType) => void;
  toggleStatus: (status: AgentStatus) => void;
  selectAgent: (id: string | null) => void;
  refresh: () => Promise<void>;
  stats: {
    total: number;
    online: number;
    busy: number;
    error: number;
  };
}

/**
 * useAgent - 管理 Agent 列表的加载、过滤、搜索与选择。
 */
export function useAgent(options: UseAgentOptions = {}): UseAgentResult {
  const { fetcher, initialFilter = {}, pollIntervalMs = 0, autoLoad = true } = options;

  const [agents, setAgents] = useState<Agent[]>([]);
  const [filter, setFilter] = useState<AgentFilter>(initialFilter);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const mountedRef = useRef(true);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const refresh = useCallback(async () => {
    if (!fetcherRef.current) return;
    setLoading(true);
    setError(null);
    try {
      const data = await fetcherRef.current();
      if (mountedRef.current) setAgents(data);
    } catch (e) {
      if (mountedRef.current) setError(e instanceof Error ? e : new Error(String(e)));
    } finally {
      if (mountedRef.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (autoLoad) void refresh();
  }, [autoLoad, refresh]);

  useEffect(() => {
    if (pollIntervalMs <= 0) return;
    const timer = window.setInterval(() => void refresh(), pollIntervalMs);
    return () => window.clearInterval(timer);
  }, [pollIntervalMs, refresh]);

  const filteredAgents = useMemo(() => {
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
      if (filter.tags?.length && !filter.tags.some((t) => agent.tags?.includes(t))) return false;
      return true;
    });
  }, [agents, filter]);

  const stats = useMemo(
    () => ({
      total: agents.length,
      online: agents.filter((a) => a.status === 'online').length,
      busy: agents.filter((a) => a.status === 'busy').length,
      error: agents.filter((a) => a.status === 'error').length,
    }),
    [agents],
  );

  const setSearch = useCallback((search: string) => {
    setFilter((prev) => ({ ...prev, search }));
  }, []);

  const toggleType = useCallback((type: AgentType) => {
    setFilter((prev) => {
      const types = prev.types?.includes(type)
        ? prev.types.filter((t) => t !== type)
        : [...(prev.types ?? []), type];
      return { ...prev, types };
    });
  }, []);

  const toggleStatus = useCallback((status: AgentStatus) => {
    setFilter((prev) => {
      const statuses = prev.statuses?.includes(status)
        ? prev.statuses.filter((s) => s !== status)
        : [...(prev.statuses ?? []), status];
      return { ...prev, statuses };
    });
  }, []);

  const selectAgent = useCallback((id: string | null) => {
    setSelectedId(id);
  }, []);

  const selectedAgent = useMemo(
    () => agents.find((a) => a.id === selectedId) ?? null,
    [agents, selectedId],
  );

  return {
    agents,
    filteredAgents,
    filter,
    loading,
    error,
    selectedAgent,
    setFilter,
    setSearch,
    toggleType,
    toggleStatus,
    selectAgent,
    refresh,
    stats,
  };
}
