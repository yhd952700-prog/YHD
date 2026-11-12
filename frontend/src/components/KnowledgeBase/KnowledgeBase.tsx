import React, { memo, useCallback, useMemo, useRef, useState } from 'react';
import type {
  KnowledgeCollection,
  KnowledgeDocument,
  KnowledgeDocStatus,
} from '../../types';
import type { UploadProgress } from '../../hooks/useKnowledge';
import { ProgressBar } from '../TaskCard';
import { cn, formatBytes, formatRelativeTime } from '../../utils';

export interface KnowledgeBaseProps {
  collections: KnowledgeCollection[];
  documents: KnowledgeDocument[];
  activeCollectionId?: string | null;
  uploads?: UploadProgress[];
  loading?: boolean;
  documentsLoading?: boolean;
  onSelectCollection?: (id: string) => void;
  onUploadFiles?: (files: File[]) => void;
  onDeleteDocument?: (documentId: string) => void;
  onSearch?: (query: string) => void;
  onCreateCollection?: () => void;
  /** 允许的文件类型（accept 属性） */
  accept?: string;
  className?: string;
}

const DOC_STATUS_META: Record<KnowledgeDocStatus, { label: string; className: string }> = {
  uploading: { label: '上传中', className: 'bg-blue-100 text-blue-600 dark:bg-blue-950 dark:text-blue-400' },
  processing: { label: '处理中', className: 'bg-amber-100 text-amber-600 dark:bg-amber-950 dark:text-amber-400' },
  ready: { label: '就绪', className: 'bg-emerald-100 text-emerald-600 dark:bg-emerald-950 dark:text-emerald-400' },
  failed: { label: '失败', className: 'bg-red-100 text-red-600 dark:bg-red-950 dark:text-red-400' },
};

function fileIcon(mimeType: string): string {
  if (mimeType.includes('pdf')) return '📄';
  if (mimeType.includes('word') || mimeType.includes('document')) return '📝';
  if (mimeType.includes('sheet') || mimeType.includes('excel') || mimeType.includes('csv')) return '📊';
  if (mimeType.startsWith('image/')) return '🖼️';
  if (mimeType.startsWith('text/') || mimeType.includes('markdown')) return '📃';
  if (mimeType.includes('json') || mimeType.includes('code')) return '💾';
  return '📎';
}

interface DocumentRowProps {
  doc: KnowledgeDocument;
  onDelete?: (id: string) => void;
}

const DocumentRow = memo<DocumentRowProps>(function DocumentRow({ doc, onDelete }) {
  const status = DOC_STATUS_META[doc.status];
  return (
    <div className="group flex items-center gap-3 rounded-lg border border-slate-100 px-3 py-2.5 transition-colors hover:border-slate-200 hover:bg-slate-50 dark:border-slate-800 dark:hover:border-slate-700 dark:hover:bg-slate-800/60">
      <span className="text-xl" aria-hidden>
        {fileIcon(doc.mimeType)}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="truncate text-sm font-medium text-slate-800 dark:text-slate-200">
            {doc.name}
          </span>
          <span className={cn('lh-badge shrink-0', status.className)}>{status.label}</span>
        </div>
        <div className="mt-0.5 flex items-center gap-2 text-[11px] text-slate-400">
          <span>{formatBytes(doc.sizeBytes)}</span>
          {doc.chunkCount !== undefined && <span>{doc.chunkCount} 分块</span>}
          <span>{formatRelativeTime(doc.createdAt)}</span>
        </div>
        {doc.status === 'processing' && doc.progress !== undefined && (
          <div className="mt-1.5 max-w-48">
            <ProgressBar value={doc.progress} />
          </div>
        )}
        {doc.status === 'failed' && doc.errorMessage && (
          <p className="mt-1 text-[11px] text-red-500">{doc.errorMessage}</p>
        )}
      </div>
      {onDelete && (
        <button
          type="button"
          onClick={() => onDelete(doc.id)}
          className="shrink-0 rounded-md p-1.5 text-slate-300 opacity-0 transition-all hover:bg-red-50 hover:text-red-500 group-hover:opacity-100 dark:text-slate-600 dark:hover:bg-red-950"
          aria-label={`删除 ${doc.name}`}
        >
          <svg className="h-4 w-4" viewBox="0 0 20 20" fill="currentColor">
            <path
              fillRule="evenodd"
              d="M8.75 1A2.75 2.75 0 006 3.75v.443c-.795.077-1.584.176-2.365.298a.75.75 0 10.23 1.482l.149-.022.841 10.518A2.75 2.75 0 007.596 19h4.807a2.75 2.75 0 002.742-2.53l.841-10.52.149.023a.75.75 0 00.23-1.482A41.03 41.03 0 0014 4.193V3.75A2.75 2.75 0 0011.25 1h-2.5zM10 4c.84 0 1.673.025 2.5.075V3.75c0-.69-.56-1.25-1.25-1.25h-2.5c-.69 0-1.25.56-1.25 1.25v.325C8.327 4.025 9.16 4 10 4zM8.58 7.72a.75.75 0 00-1.5.06l.3 7.5a.75.75 0 101.5-.06l-.3-7.5zm4.34.06a.75.75 0 10-1.5-.06l-.3 7.5a.75.75 0 101.5.06l.3-7.5z"
              clipRule="evenodd"
            />
          </svg>
        </button>
      )}
    </div>
  );
});

/**
 * KnowledgeBase - 知识库管理：集合切换、拖拽上传、处理进度与文档列表。
 */
export const KnowledgeBase = memo<KnowledgeBaseProps>(function KnowledgeBase({
  collections,
  documents,
  activeCollectionId,
  uploads = [],
  loading = false,
  documentsLoading = false,
  onSelectCollection,
  onUploadFiles,
  onDeleteDocument,
  onSearch,
  onCreateCollection,
  accept = '.pdf,.md,.txt,.docx,.csv,.json',
  className,
}) {
  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const activeCollection = useMemo(
    () => collections.find((c) => c.id === activeCollectionId) ?? null,
    [collections, activeCollectionId],
  );

  const handleFiles = useCallback(
    (files: FileList | null) => {
      if (!files || files.length === 0) return;
      onUploadFiles?.(Array.from(files));
    },
    [onUploadFiles],
  );

  return (
    <div className={cn('flex h-full overflow-hidden rounded-xl border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900', className)}>
      {/* 集合侧栏 */}
      <aside className="flex w-52 shrink-0 flex-col border-r border-slate-200 dark:border-slate-800">
        <div className="flex items-center justify-between px-3 py-2.5">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-400">知识库</h3>
          {onCreateCollection && (
            <button
              type="button"
              onClick={onCreateCollection}
              className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600 dark:hover:bg-slate-800"
              aria-label="新建知识库"
            >
              <svg className="h-4 w-4" viewBox="0 0 20 20" fill="currentColor">
                <path d="M10.75 4.75a.75.75 0 00-1.5 0v4.5h-4.5a.75.75 0 000 1.5h4.5v4.5a.75.75 0 001.5 0v-4.5h4.5a.75.75 0 000-1.5h-4.5v-4.5z" />
              </svg>
            </button>
          )}
        </div>
        <div className="flex-1 overflow-y-auto px-2 pb-2">
          {loading ? (
            <div className="space-y-2 px-1 pt-1">
              {Array.from({ length: 4 }).map((_, i) => (
                <div key={i} className="lh-skeleton h-9" />
              ))}
            </div>
          ) : (
            collections.map((col) => (
              <button
                key={col.id}
                type="button"
                onClick={() => onSelectCollection?.(col.id)}
                className={cn(
                  'mb-1 flex w-full flex-col rounded-lg px-2.5 py-2 text-left transition-colors',
                  col.id === activeCollectionId
                    ? 'bg-brand-50 text-brand-700 dark:bg-brand-950 dark:text-brand-300'
                    : 'text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800',
                )}
              >
                <span className="truncate text-sm font-medium">{col.name}</span>
                <span className="text-[11px] opacity-70">
                  {col.documentCount} 文档 · {formatBytes(col.totalSizeBytes)}
                </span>
              </button>
            ))
          )}
        </div>
      </aside>

      {/* 文档区 */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center gap-3 border-b border-slate-200 px-4 py-3 dark:border-slate-800">
          <div className="min-w-0 flex-1">
            <h2 className="truncate text-sm font-semibold text-slate-900 dark:text-slate-100">
              {activeCollection?.name ?? '选择知识库'}
            </h2>
            {activeCollection?.description && (
              <p className="truncate text-xs text-slate-400">{activeCollection.description}</p>
            )}
          </div>
          {onSearch && (
            <input
              type="search"
              placeholder="语义检索…"
              onChange={(e) => onSearch(e.target.value)}
              className="lh-input w-44 py-1.5 text-xs"
            />
          )}
          {onUploadFiles && (
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              disabled={!activeCollectionId}
              className="lh-btn-primary shrink-0 py-1.5 text-xs"
            >
              上传文档
            </button>
          )}
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept={accept}
            className="hidden"
            onChange={(e) => {
              handleFiles(e.target.files);
              e.target.value = '';
            }}
          />
        </header>

        {/* 上传进行区 */}
        {uploads.length > 0 && (
          <div className="space-y-2 border-b border-slate-100 bg-slate-50/50 px-4 py-2 dark:border-slate-800 dark:bg-slate-800/30">
            {uploads.map((u, idx) => (
              <div key={`${u.file.name}-${idx}`} className="flex items-center gap-2 text-xs">
                <span className="min-w-0 flex-1 truncate text-slate-600 dark:text-slate-300">
                  {u.file.name}
                </span>
                {u.status === 'error' ? (
                  <span className="shrink-0 text-red-500">{u.error ?? '上传失败'}</span>
                ) : (
                  <>
                    <div className="w-32">
                      <ProgressBar value={u.progress} />
                    </div>
                    <span className="w-9 shrink-0 text-right tabular-nums text-slate-400">
                      {Math.round(u.progress)}%
                    </span>
                  </>
                )}
              </div>
            ))}
          </div>
        )}

        {/* 文档列表（支持拖拽上传） */}
        <div
          className="relative flex-1 overflow-y-auto p-3"
          onDragOver={(e) => {
            if (!onUploadFiles) return;
            e.preventDefault();
            setDragOver(true);
          }}
          onDragLeave={(e) => {
            if (!e.currentTarget.contains(e.relatedTarget as Node)) setDragOver(false);
          }}
          onDrop={(e) => {
            e.preventDefault();
            setDragOver(false);
            handleFiles(e.dataTransfer.files);
          }}
        >
          {dragOver && (
            <div className="pointer-events-none absolute inset-2 z-10 flex items-center justify-center rounded-xl border-2 border-dashed border-brand-400 bg-brand-50/80 text-sm font-medium text-brand-600 dark:bg-brand-950/80 dark:text-brand-300">
              释放以上传到「{activeCollection?.name}」
            </div>
          )}

          {documentsLoading ? (
            <div className="space-y-2">
              {Array.from({ length: 5 }).map((_, i) => (
                <div key={i} className="lh-skeleton h-14" />
              ))}
            </div>
          ) : documents.length === 0 ? (
            <div className="flex h-full flex-col items-center justify-center gap-2 text-slate-400">
              <svg className="h-10 w-10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.2">
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M12 10.5v6m3-3H9m4.06-7.19l-2.12-2.12a1.5 1.5 0 00-1.061-.44H4.5A2.25 2.25 0 002.25 6v12a2.25 2.25 0 002.25 2.25h15A2.25 2.25 0 0021.75 18V9a2.25 2.25 0 00-2.25-2.25h-5.379a1.5 1.5 0 01-1.06-.44z"
                />
              </svg>
              <p className="text-sm">
                {activeCollection ? '暂无文档，拖拽文件到此处上传' : '请先选择左侧知识库'}
              </p>
            </div>
          ) : (
            <div className="flex flex-col gap-2">
              {documents.map((doc) => (
                <DocumentRow key={doc.id} doc={doc} onDelete={onDeleteDocument} />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
});
