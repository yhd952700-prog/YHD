import {
  memo,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import type { SearchResult, SearchResultType } from '../../types';
import { cn } from '../../utils';

export interface SearchBarProps {
  results?: SearchResult[];
  searching?: boolean;
  placeholder?: string;
  onSearch?: (query: string) => void;
  onSelect?: (result: SearchResult) => void;
  className?: string;
}

const TYPE_META: Record<SearchResultType, { label: string; icon: string }> = {
  agent: { label: 'Agent', icon: '🤖' },
  task: { label: '任务', icon: '📋' },
  conversation: { label: '对话', icon: '💬' },
  memory: { label: '记忆', icon: '🧠' },
  document: { label: '文档', icon: '📄' },
  plugin: { label: '插件', icon: '🧩' },
  setting: { label: '设置', icon: '⚙️' },
  action: { label: '操作', icon: '⚡' },
};

const TYPE_ORDER: SearchResultType[] = [
  'action',
  'agent',
  'task',
  'conversation',
  'memory',
  'document',
  'plugin',
  'setting',
];

interface GroupedResults {
  type: SearchResultType;
  items: SearchResult[];
}

export const SearchBar = memo(function SearchBar({
  results = [],
  searching = false,
  placeholder = '搜索 Agent、任务、文档…',
  onSearch,
  onSelect,
  className,
}: SearchBarProps) {
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  const rootRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLUListElement>(null);

  const grouped = useMemo<GroupedResults[]>(() => {
    const map = new Map<SearchResultType, SearchResult[]>();
    for (const result of results) {
      const list = map.get(result.type) ?? [];
      list.push(result);
      map.set(result.type, list);
    }
    return TYPE_ORDER.filter((type) => map.has(type)).map((type) => ({
      type,
      items: map.get(type) ?? [],
    }));
  }, [results]);

  const flatResults = useMemo(
    () => grouped.flatMap((group) => group.items),
    [grouped],
  );

  const handleInputChange = useCallback(
    (event: React.ChangeEvent<HTMLInputElement>) => {
      const next = event.target.value;
      setQuery(next);
      setActiveIndex(-1);
      setOpen(next.trim().length > 0);
      onSearch?.(next);
    },
    [onSearch],
  );

  const selectResult = useCallback(
    (result: SearchResult) => {
      onSelect?.(result);
      setOpen(false);
      setQuery('');
      setActiveIndex(-1);
      inputRef.current?.blur();
    },
    [onSelect],
  );

  const handleKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLInputElement>) => {
      if (!open) {
        if (event.key === 'ArrowDown' && flatResults.length > 0) {
          setOpen(true);
          setActiveIndex(0);
          event.preventDefault();
        }
        return;
      }
      switch (event.key) {
        case 'ArrowDown':
          event.preventDefault();
          setActiveIndex((prev) =>
            prev < flatResults.length - 1 ? prev + 1 : 0,
          );
          break;
        case 'ArrowUp':
          event.preventDefault();
          setActiveIndex((prev) =>
            prev > 0 ? prev - 1 : flatResults.length - 1,
          );
          break;
        case 'Enter':
          event.preventDefault();
          if (activeIndex >= 0 && activeIndex < flatResults.length) {
            selectResult(flatResults[activeIndex]);
          }
          break;
        case 'Escape':
          event.preventDefault();
          setOpen(false);
          setActiveIndex(-1);
          break;
        default:
          break;
      }
    },
    [open, flatResults, activeIndex, selectResult],
  );

  // 激活项滚动到可见区域
  useEffect(() => {
    if (activeIndex < 0 || !listRef.current) return;
    const activeEl = listRef.current.querySelector<HTMLElement>(
      `[data-index="${activeIndex}"]`,
    );
    activeEl?.scrollIntoView({ block: 'nearest' });
  }, [activeIndex]);

  // 点击外部关闭
  useEffect(() => {
    if (!open) return undefined;
    const handlePointerDown = (event: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', handlePointerDown);
    return () => document.removeEventListener('mousedown', handlePointerDown);
  }, [open]);

  let itemIndex = -1;

  return (
    <div ref={rootRef} className={cn('relative', className)}>
      <div className="relative">
        <svg
          aria-hidden
          className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={2}
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <circle cx="11" cy="11" r="8" />
          <path d="m21 21-4.3-4.3" />
        </svg>
        <input
          ref={inputRef}
          type="text"
          role="combobox"
          aria-expanded={open}
          aria-controls="search-results-listbox"
          aria-activedescendant={
            activeIndex >= 0 ? `search-result-${activeIndex}` : undefined
          }
          value={query}
          placeholder={placeholder}
          onChange={handleInputChange}
          onKeyDown={handleKeyDown}
          onFocus={() => {
            if (query.trim()) setOpen(true);
          }}
          className="w-full rounded-xl border border-slate-300 bg-white py-2 pl-9 pr-9 text-sm text-slate-900 placeholder:text-slate-400 focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100 dark:placeholder:text-slate-500"
        />
        {searching && (
          <span
            aria-hidden
            className="absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 animate-spin rounded-full border-2 border-slate-300 border-t-brand-600"
          />
        )}
      </div>

      {open && (
        <div className="absolute left-0 right-0 z-50 mt-2 overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-xl dark:border-slate-700 dark:bg-slate-800">
          {searching && flatResults.length === 0 ? (
            <p className="px-4 py-8 text-center text-sm text-slate-400 dark:text-slate-500">
              搜索中…
            </p>
          ) : flatResults.length === 0 ? (
            <p className="px-4 py-8 text-center text-sm text-slate-400 dark:text-slate-500">
              未找到「{query}」相关结果
            </p>
          ) : (
            <ul
              ref={listRef}
              id="search-results-listbox"
              role="listbox"
              aria-label="搜索结果"
              className="max-h-96 overflow-y-auto p-2"
            >
              {grouped.map((group) => (
                <li key={group.type} role="group" aria-label={TYPE_META[group.type].label}>
                  <p className="px-3 pb-1 pt-2 text-[11px] font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500">
                    {TYPE_META[group.type].label}
                  </p>
                  <ul>
                    {group.items.map((result) => {
                      itemIndex += 1;
                      const index = itemIndex;
                      const active = index === activeIndex;
                      return (
                        <li
                          key={result.id}
                          id={`search-result-${index}`}
                          data-index={index}
                          role="option"
                          aria-selected={active}
                        >
                          <button
                            type="button"
                            onClick={() => selectResult(result)}
                            onMouseEnter={() => setActiveIndex(index)}
                            className={cn(
                              'flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left transition-colors',
                              active
                                ? 'bg-brand-50 dark:bg-brand-500/15'
                                : 'hover:bg-slate-50 dark:hover:bg-slate-700/40',
                            )}
                          >
                            <span aria-hidden className="text-base">
                              {result.icon ?? TYPE_META[result.type].icon}
                            </span>
                            <span className="min-w-0 flex-1">
                              <span className="block truncate text-sm font-medium text-slate-800 dark:text-slate-100">
                                {result.title}
                              </span>
                              {result.subtitle && (
                                <span className="block truncate text-xs text-slate-400 dark:text-slate-500">
                                  {result.subtitle}
                                </span>
                              )}
                            </span>
                            <kbd className="hidden shrink-0 rounded border border-slate-200 px-1.5 py-0.5 text-[10px] text-slate-400 dark:border-slate-600 dark:text-slate-500 sm:inline">
                              {TYPE_META[result.type].label}
                            </kbd>
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                </li>
              ))}
            </ul>
          )}
          <footer className="flex items-center gap-4 border-t border-slate-100 px-4 py-2 text-[11px] text-slate-400 dark:border-slate-700/60 dark:text-slate-500">
            <span>
              <kbd className="rounded border border-slate-200 px-1 dark:border-slate-600">↑↓</kbd>{' '}
              导航
            </span>
            <span>
              <kbd className="rounded border border-slate-200 px-1 dark:border-slate-600">Enter</kbd>{' '}
              选择
            </span>
            <span>
              <kbd className="rounded border border-slate-200 px-1 dark:border-slate-600">Esc</kbd>{' '}
              关闭
            </span>
          </footer>
        </div>
      )}
    </div>
  );
});

export default SearchBar;
