import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type {
  KnowledgeCollection,
  KnowledgeDocument,
  KnowledgeSearchResult,
} from '../types';

export interface UploadProgress {
  file: File;
  progress: number; // 0 - 100
  status: 'pending' | 'uploading' | 'done' | 'error';
  error?: string;
  documentId?: string;
}

export interface UseKnowledgeOptions {
  fetchCollections?: () => Promise<KnowledgeCollection[]>;
  fetchDocuments?: (collectionId: string) => Promise<KnowledgeDocument[]>;
  onUpload?: (
    files: File[],
    collectionId: string,
    onProgress: (file: File, percent: number) => void,
  ) => Promise<KnowledgeDocument[]>;
  onDeleteDocument?: (documentId: string) => Promise<void>;
  onSearch?: (query: string, collectionId?: string) => Promise<KnowledgeSearchResult[]>;
  autoLoad?: boolean;
}

export interface UseKnowledgeResult {
  collections: KnowledgeCollection[];
  documents: KnowledgeDocument[];
  activeCollectionId: string | null;
  loading: boolean;
  documentsLoading: boolean;
  error: Error | null;
  uploads: UploadProgress[];
  searchQuery: string;
  searchResults: KnowledgeSearchResult[];
  searching: boolean;
  setActiveCollection: (id: string | null) => void;
  upload: (files: File[]) => Promise<void>;
  removeDocument: (documentId: string) => Promise<void>;
  setSearchQuery: (query: string) => void;
  refresh: () => Promise<void>;
  activeCollection: KnowledgeCollection | null;
  totalSize: number;
}

/**
 * useKnowledge - 知识库集合、文档、上传与检索管理。
 */
export function useKnowledge(options: UseKnowledgeOptions = {}): UseKnowledgeResult {
  const {
    fetchCollections,
    fetchDocuments,
    onUpload,
    onDeleteDocument,
    onSearch,
    autoLoad = true,
  } = options;

  const [collections, setCollections] = useState<KnowledgeCollection[]>([]);
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [activeCollectionId, setActiveCollectionId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [documentsLoading, setDocumentsLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [uploads, setUploads] = useState<UploadProgress[]>([]);
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<KnowledgeSearchResult[]>([]);
  const [searching, setSearching] = useState(false);

  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const refresh = useCallback(async () => {
    if (!fetchCollections) return;
    setLoading(true);
    setError(null);
    try {
      const data = await fetchCollections();
      if (!mountedRef.current) return;
      setCollections(data);
      if (data.length > 0) {
        setActiveCollectionId((prev) => prev ?? data[0].id);
      }
    } catch (e) {
      if (mountedRef.current) setError(e instanceof Error ? e : new Error(String(e)));
    } finally {
      if (mountedRef.current) setLoading(false);
    }
  }, [fetchCollections]);

  useEffect(() => {
    if (autoLoad) void refresh();
  }, [autoLoad, refresh]);

  // 加载当前集合文档
  useEffect(() => {
    if (!activeCollectionId || !fetchDocuments) {
      setDocuments([]);
      return;
    }
    let cancelled = false;
    setDocumentsLoading(true);
    fetchDocuments(activeCollectionId)
      .then((docs) => {
        if (!cancelled && mountedRef.current) setDocuments(docs);
      })
      .catch(() => {
        if (!cancelled && mountedRef.current) setDocuments([]);
      })
      .finally(() => {
        if (!cancelled && mountedRef.current) setDocumentsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [activeCollectionId, fetchDocuments]);

  // 防抖搜索
  useEffect(() => {
    if (!searchQuery.trim() || !onSearch) {
      setSearchResults([]);
      return;
    }
    setSearching(true);
    const timer = window.setTimeout(async () => {
      try {
        const results = await onSearch(searchQuery, activeCollectionId ?? undefined);
        if (mountedRef.current) setSearchResults(results);
      } catch {
        if (mountedRef.current) setSearchResults([]);
      } finally {
        if (mountedRef.current) setSearching(false);
      }
    }, 300);
    return () => window.clearTimeout(timer);
  }, [searchQuery, onSearch, activeCollectionId]);

  const setActiveCollection = useCallback((id: string | null) => {
    setActiveCollectionId(id);
  }, []);

  const updateUpload = useCallback((file: File, patch: Partial<UploadProgress>) => {
    setUploads((prev) =>
      prev.map((u) => (u.file === file ? { ...u, ...patch } : u)),
    );
  }, []);

  const upload = useCallback(
    async (files: File[]) => {
      if (!activeCollectionId || files.length === 0) return;

      const initial: UploadProgress[] = files.map((file) => ({
        file,
        progress: 0,
        status: onUpload ? 'pending' : 'error',
        error: onUpload ? undefined : '未配置上传处理器',
      }));
      setUploads((prev) => [...prev, ...initial]);

      if (!onUpload) return;

      for (const item of initial) {
        updateUpload(item.file, { status: 'uploading' });
      }

      try {
        const docs = await onUpload(files, activeCollectionId, (file, percent) => {
          updateUpload(file, { progress: percent });
        });
        if (!mountedRef.current) return;
        for (let i = 0; i < files.length; i += 1) {
          updateUpload(files[i], {
            status: 'done',
            progress: 100,
            documentId: docs[i]?.id,
          });
        }
        setDocuments((prev) => [...docs, ...prev]);
        // 3 秒后清理已完成记录
        window.setTimeout(() => {
          if (mountedRef.current) {
            setUploads((prev) => prev.filter((u) => u.status !== 'done'));
          }
        }, 3000);
      } catch (e) {
        if (!mountedRef.current) return;
        const message = e instanceof Error ? e.message : String(e);
        for (const item of initial) {
          updateUpload(item.file, { status: 'error', error: message });
        }
      }
    },
    [activeCollectionId, onUpload, updateUpload],
  );

  const removeDocument = useCallback(
    async (documentId: string) => {
      const previous = documents;
      setDocuments((prev) => prev.filter((d) => d.id !== documentId));
      if (onDeleteDocument) {
        try {
          await onDeleteDocument(documentId);
        } catch (e) {
          if (mountedRef.current) {
            setDocuments(previous);
            setError(e instanceof Error ? e : new Error(String(e)));
          }
        }
      }
    },
    [documents, onDeleteDocument],
  );

  const activeCollection = useMemo(
    () => collections.find((c) => c.id === activeCollectionId) ?? null,
    [collections, activeCollectionId],
  );

  const totalSize = useMemo(
    () => collections.reduce((sum, c) => sum + c.totalSizeBytes, 0),
    [collections],
  );

  return {
    collections,
    documents,
    activeCollectionId,
    loading,
    documentsLoading,
    error,
    uploads,
    searchQuery,
    searchResults,
    searching,
    setActiveCollection,
    upload,
    removeDocument,
    setSearchQuery,
    refresh,
    activeCollection,
    totalSize,
  };
}
