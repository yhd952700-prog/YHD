/**
 * Knowledge Base Resource
 *
 * Provides methods for managing knowledge bases, documents,
 * and performing knowledge search in the LiuHao AI OS system.
 */

import type { LiuHaoClient } from '../client';
import type {
  Document,
  DocumentListParams,
  DocumentUploadParams,
  KnowledgeBase,
  KnowledgeBaseCreateParams,
  KnowledgeBaseListParams,
  KnowledgeCollection,
  KnowledgeCollectionCreateParams,
  KnowledgeSearchParams,
  PaginatedResponse,
  SearchResult,
} from '../types';

/**
 * Resource class for knowledge base API operations.
 *
 * @example
 * ```ts
 * const client = new LiuHaoClient({ apiKey: '...' });
 *
 * // Create a knowledge base
 * const base = await client.knowledge.createBase({
 *   name: 'Product Docs',
 *   type: KnowledgeBaseType.VECTOR,
 * });
 *
 * // Upload a document
 * const doc = await client.knowledge.uploadDocument(base.id, {
 *   title: 'API Reference',
 *   content: '... document text ...',
 * });
 * ```
 */
export class KnowledgeResource {
  private readonly client: LiuHaoClient;

  /**
   * @param client - LiuHaoClient instance
   */
  constructor(client: LiuHaoClient) {
    this.client = client;
  }

  // --------------------------------------------------------
  // Knowledge Base Operations
  // --------------------------------------------------------

  /**
   * List all knowledge bases.
   *
   * @param params - List parameters (pagination, filters)
   * @returns Paginated list of knowledge bases
   *
   * @example
   * ```ts
   * const result = await client.knowledge.listBases({
   *   page: 1,
   *   pageSize: 10,
   * });
   * console.log(result.items); // Array of KnowledgeBase objects
   * ```
   */
  async listBases(params?: KnowledgeBaseListParams): Promise<PaginatedResponse<KnowledgeBase>> {
    const queryParams = this.buildBaseListParams(params);
    return this.client.get<PaginatedResponse<KnowledgeBase>>(
      '/api/v1/knowledge/bases',
      queryParams,
    );
  }

  /**
   * Create a new knowledge base.
   *
   * @param params - Knowledge base creation parameters
   * @returns The created knowledge base
   * @throws {ValidationError} If parameters are invalid
   *
   * @example
   * ```ts
   * const base = await client.knowledge.createBase({
   *   name: 'Company Handbook',
   *   description: 'Internal policies and procedures',
   *   type: KnowledgeBaseType.HYBRID,
   *   embeddingModel: 'text-embedding-3-small',
   * });
   * ```
   */
  async createBase(params: KnowledgeBaseCreateParams): Promise<KnowledgeBase> {
    if (!params.name) {
      throw new Error('Knowledge base name is required');
    }
    if (!params.type) {
      throw new Error('Knowledge base type is required');
    }
    return this.client.post<KnowledgeBase>('/api/v1/knowledge/bases', params);
  }

  /**
   * Get a knowledge base by ID.
   *
   * @param baseId - Knowledge base ID
   * @returns Knowledge base details
   * @throws {NotFoundError} If the knowledge base does not exist
   *
   * @example
   * ```ts
   * const base = await client.knowledge.getBase('kb-123');
   * console.log(base.documentCount);
   * ```
   */
  async getBase(baseId: string): Promise<KnowledgeBase> {
    if (!baseId) {
      throw new Error('Knowledge base ID is required');
    }
    return this.client.get<KnowledgeBase>(`/api/v1/knowledge/bases/${baseId}`);
  }

  /**
   * Delete a knowledge base.
   *
   * @param baseId - Knowledge base ID
   * @throws {NotFoundError} If the knowledge base does not exist
   *
   * @example
   * ```ts
   * await client.knowledge.deleteBase('kb-123');
   * ```
   */
  async deleteBase(baseId: string): Promise<void> {
    if (!baseId) {
      throw new Error('Knowledge base ID is required');
    }
    await this.client.delete(`/api/v1/knowledge/bases/${baseId}`);
  }

  // --------------------------------------------------------
  // Document Operations
  // --------------------------------------------------------

  /**
   * Upload a document to a knowledge base.
   *
   * @param baseId - Knowledge base ID
   * @param params - Document upload parameters
   * @returns The created document
   * @throws {NotFoundError} If the knowledge base does not exist
   *
   * @example
   * ```ts
   * const doc = await client.knowledge.uploadDocument('kb-123', {
   *   title: 'Getting Started Guide',
   *   content: 'Welcome to our platform...',
   *   tags: ['guide', 'beginner'],
   *   chunk: true,
   *   chunkSize: 1000,
   * });
   * ```
   */
  async uploadDocument(baseId: string, params: DocumentUploadParams): Promise<Document> {
    if (!baseId) {
      throw new Error('Knowledge base ID is required');
    }
    if (!params.title) {
      throw new Error('Document title is required');
    }
    if (!params.content && !params.source) {
      throw new Error('Either content or source must be provided');
    }
    return this.client.post<Document>(
      `/api/v1/knowledge/bases/${baseId}/documents`,
      params,
    );
  }

  /**
   * List documents in a knowledge base.
   *
   * @param baseId - Knowledge base ID
   * @param params - List parameters
   * @returns Paginated list of documents
   *
   * @example
   * ```ts
   * const result = await client.knowledge.listDocuments('kb-123', {
   *   page: 1,
   *   pageSize: 20,
   *   tags: ['guide'],
   * });
   * ```
   */
  async listDocuments(
    baseId: string,
    params?: DocumentListParams,
  ): Promise<PaginatedResponse<Document>> {
    if (!baseId) {
      throw new Error('Knowledge base ID is required');
    }

    const queryParams: Record<string, unknown> = {};

    if (params) {
      if (params.page !== undefined) {
        queryParams.page = params.page;
      }
      if (params.pageSize !== undefined) {
        queryParams.pageSize = params.pageSize;
      }
      if (params.tags !== undefined) {
        queryParams.tags = params.tags;
      }
      if (params.indexingStatus !== undefined) {
        queryParams.indexingStatus = params.indexingStatus;
      }
      if (params.search !== undefined) {
        queryParams.search = params.search;
      }
      if (params.sortBy !== undefined) {
        queryParams.sortBy = params.sortBy;
      }
      if (params.sortDir !== undefined) {
        queryParams.sortDir = params.sortDir;
      }
    }

    return this.client.get<PaginatedResponse<Document>>(
      `/api/v1/knowledge/bases/${baseId}/documents`,
      queryParams,
    );
  }

  /**
   * Delete a document from a knowledge base.
   *
   * @param baseId - Knowledge base ID
   * @param documentId - Document ID to delete
   * @throws {NotFoundError} If the document does not exist
   *
   * @example
   * ```ts
   * await client.knowledge.deleteDocument('kb-123', 'doc-456');
   * ```
   */
  async deleteDocument(baseId: string, documentId: string): Promise<void> {
    if (!baseId) {
      throw new Error('Knowledge base ID is required');
    }
    if (!documentId) {
      throw new Error('Document ID is required');
    }
    await this.client.delete(
      `/api/v1/knowledge/bases/${baseId}/documents/${documentId}`,
    );
  }

  // --------------------------------------------------------
  // Search Operations
  // --------------------------------------------------------

  /**
   * Search a knowledge base for relevant documents.
   *
   * Performs semantic search across documents in the knowledge base.
   *
   * @param baseId - Knowledge base ID
   * @param params - Search parameters
   * @returns Paginated search results
   *
   * @example
   * ```ts
   * const results = await client.knowledge.search('kb-123', {
   *   query: 'How do I reset my password?',
   *   limit: 5,
   *   minScore: 0.7,
   *   rerank: true,
   * });
   *
   * results.items.forEach((result) => {
   *   console.log(`${result.score}: ${result.documentTitle}`);
   *   console.log(result.text);
   * });
   * ```
   */
  async search(
    baseId: string,
    params: KnowledgeSearchParams,
  ): Promise<PaginatedResponse<SearchResult>> {
    if (!baseId) {
      throw new Error('Knowledge base ID is required');
    }
    if (!params.query) {
      throw new Error('Search query is required');
    }

    const queryParams: Record<string, unknown> = {
      query: params.query,
    };

    if (params.limit !== undefined) {
      queryParams.limit = params.limit;
    }
    if (params.minScore !== undefined) {
      queryParams.minScore = params.minScore;
    }
    if (params.documentIds !== undefined) {
      queryParams.documentIds = params.documentIds;
    }
    if (params.tags !== undefined) {
      queryParams.tags = params.tags;
    }
    if (params.rerank !== undefined) {
      queryParams.rerank = params.rerank;
    }
    if (params.page !== undefined) {
      queryParams.page = params.page;
    }
    if (params.pageSize !== undefined) {
      queryParams.pageSize = params.pageSize;
    }

    return this.client.get<PaginatedResponse<SearchResult>>(
      `/api/v1/knowledge/bases/${baseId}/search`,
      queryParams,
    );
  }

  // --------------------------------------------------------
  // Collection Operations
  // --------------------------------------------------------

  /**
   * Create a knowledge collection.
   *
   * A collection groups multiple knowledge bases together for
   * unified search across multiple bases.
   *
   * @param params - Collection creation parameters
   * @returns The created collection
   *
   * @example
   * ```ts
   * const collection = await client.knowledge.createCollection({
   *   name: 'All Product Docs',
   *   description: 'Combined knowledge bases for all products',
   *   knowledgeBaseIds: ['kb-123', 'kb-456'],
   * });
   * ```
   */
  async createCollection(params: KnowledgeCollectionCreateParams): Promise<KnowledgeCollection> {
    if (!params.name) {
      throw new Error('Collection name is required');
    }
    return this.client.post<KnowledgeCollection>(
      '/api/v1/knowledge/collections',
      params,
    );
  }

  /**
   * List all knowledge collections.
   *
   * @param params - Pagination parameters
   * @returns Paginated list of collections
   *
   * @example
   * ```ts
   * const result = await client.knowledge.listCollections({
   *   page: 1,
   *   pageSize: 10,
   * });
   * ```
   */
  async listCollections(params?: {
    page?: number;
    pageSize?: number;
  }): Promise<PaginatedResponse<KnowledgeCollection>> {
    const queryParams: Record<string, unknown> = {};

    if (params?.page !== undefined) {
      queryParams.page = params.page;
    }
    if (params?.pageSize !== undefined) {
      queryParams.pageSize = params.pageSize;
    }

    return this.client.get<PaginatedResponse<KnowledgeCollection>>(
      '/api/v1/knowledge/collections',
      queryParams,
    );
  }

  // --------------------------------------------------------
  // Private helpers
  // --------------------------------------------------------

  /**
   * Build query parameters from knowledge base list params.
   */
  private buildBaseListParams(params?: KnowledgeBaseListParams): Record<string, unknown> {
    const queryParams: Record<string, unknown> = {};

    if (!params) {
      return queryParams;
    }

    if (params.page !== undefined) {
      queryParams.page = params.page;
    }
    if (params.pageSize !== undefined) {
      queryParams.pageSize = params.pageSize;
    }
    if (params.type !== undefined) {
      queryParams.type = params.type;
    }
    if (params.search !== undefined) {
      queryParams.search = params.search;
    }
    if (params.sortBy !== undefined) {
      queryParams.sortBy = params.sortBy;
    }
    if (params.sortDir !== undefined) {
      queryParams.sortDir = params.sortDir;
    }

    return queryParams;
  }
}
