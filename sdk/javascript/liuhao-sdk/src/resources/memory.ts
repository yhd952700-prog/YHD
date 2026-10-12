/**
 * Memory Resource
 *
 * Provides methods for storing, retrieving, and searching
 * memory items in the LiuHao AI OS system.
 */

import type { LiuHaoClient } from '../client';
import type {
  MemoryCategory,
  MemoryItem,
  MemoryListParams,
  MemorySearchParams,
  MemoryStoreParams,
  MemoryUpdateParams,
  PaginatedResponse,
} from '../types';

/**
 * Resource class for memory-related API operations.
 *
 * @example
 * ```ts
 * const client = new LiuHaoClient({ apiKey: '...' });
 *
 * // Store a memory
 * await client.memory.store({
 *   content: 'User prefers dark mode',
 *   category: MemoryCategory.PREFERENCE,
 *   ownerId: 'user-123',
 * });
 *
 * // Search memories
 * const results = await client.memory.search({
 *   query: 'display settings',
 *   ownerId: 'user-123',
 * });
 * ```
 */
export class MemoryResource {
  private readonly client: LiuHaoClient;

  /**
   * @param client - LiuHaoClient instance
   */
  constructor(client: LiuHaoClient) {
    this.client = client;
  }

  /**
   * Store a new memory item.
   *
   * @param params - Memory storage parameters
   * @returns The created memory item
   * @throws {ValidationError} If required fields are missing
   *
   * @example
   * ```ts
   * const memory = await client.memory.store({
   *   content: 'The user lives in Beijing',
   *   category: MemoryCategory.PROFILE,
   *   ownerId: 'user-123',
   *   importance: 0.8,
   *   tags: ['location', 'personal'],
   * });
   * ```
   */
  async store(params: MemoryStoreParams): Promise<MemoryItem> {
    if (!params.content) {
      throw new Error('Memory content is required');
    }
    if (!params.ownerId) {
      throw new Error('Owner ID is required');
    }
    return this.client.post<MemoryItem>('/api/v1/memory', params);
  }

  /**
   * Retrieve a single memory item by ID.
   *
   * @param id - Memory item ID
   * @returns Memory item details
   * @throws {NotFoundError} If the memory item does not exist
   *
   * @example
   * ```ts
   * const memory = await client.memory.retrieve('mem-123');
   * console.log(memory.content);
   * ```
   */
  async retrieve(id: string): Promise<MemoryItem> {
    if (!id) {
      throw new Error('Memory ID is required');
    }
    return this.client.get<MemoryItem>(`/api/v1/memory/${id}`);
  }

  /**
   * Update an existing memory item.
   *
   * @param id - Memory item ID
   * @param params - Fields to update
   * @returns The updated memory item
   * @throws {NotFoundError} If the memory item does not exist
   *
   * @example
   * ```ts
   * const memory = await client.memory.update('mem-123', {
   *   content: 'Updated content',
   *   importance: 0.9,
   * });
   * ```
   */
  async update(id: string, params: MemoryUpdateParams): Promise<MemoryItem> {
    if (!id) {
      throw new Error('Memory ID is required');
    }
    return this.client.patch<MemoryItem>(`/api/v1/memory/${id}`, params);
  }

  /**
   * Delete a memory item.
   *
   * @param id - Memory item ID
   * @throws {NotFoundError} If the memory item does not exist
   *
   * @example
   * ```ts
   * await client.memory.delete('mem-123');
   * ```
   */
  async delete(id: string): Promise<void> {
    if (!id) {
      throw new Error('Memory ID is required');
    }
    await this.client.delete(`/api/v1/memory/${id}`);
  }

  /**
   * Search memory using semantic search.
   *
   * Performs a similarity-based search across memory items.
   *
   * @param params - Search parameters
   * @returns Paginated search results with similarity scores
   *
   * @example
   * ```ts
   * const results = await client.memory.search({
   *   query: 'What does the user like?',
   *   ownerId: 'user-123',
   *   category: MemoryCategory.PREFERENCE,
   *   minSimilarity: 0.7,
   *   limit: 5,
   * });
   *
   * results.items.forEach((item) => {
   *   console.log(`${item.similarity}: ${item.content}`);
   * });
   * ```
   */
  async search(params: MemorySearchParams): Promise<PaginatedResponse<MemoryItem>> {
    if (!params.query) {
      throw new Error('Search query is required');
    }

    const queryParams: Record<string, unknown> = {
      query: params.query,
    };

    if (params.category !== undefined) {
      queryParams.category = params.category;
    }
    if (params.tags !== undefined) {
      queryParams.tags = params.tags;
    }
    if (params.ownerId !== undefined) {
      queryParams.ownerId = params.ownerId;
    }
    if (params.minSimilarity !== undefined) {
      queryParams.minSimilarity = params.minSimilarity;
    }
    if (params.limit !== undefined) {
      queryParams.limit = params.limit;
    }
    if (params.page !== undefined) {
      queryParams.page = params.page;
    }
    if (params.pageSize !== undefined) {
      queryParams.pageSize = params.pageSize;
    }
    if (params.includeEmbedding !== undefined) {
      queryParams.includeEmbedding = params.includeEmbedding;
    }

    return this.client.get<PaginatedResponse<MemoryItem>>(
      '/api/v1/memory/search',
      queryParams,
    );
  }

  /**
   * List memory items with pagination and filtering.
   *
   * @param params - List parameters
   * @returns Paginated list of memory items
   *
   * @example
   * ```ts
   * const result = await client.memory.list({
   *   ownerId: 'user-123',
   *   category: MemoryCategory.TASK,
   *   page: 1,
   *   pageSize: 20,
   * });
   * ```
   */
  async list(params?: MemoryListParams): Promise<PaginatedResponse<MemoryItem>> {
    const queryParams: Record<string, unknown> = {};

    if (params) {
      if (params.category !== undefined) {
        queryParams.category = params.category;
      }
      if (params.tags !== undefined) {
        queryParams.tags = params.tags;
      }
      if (params.ownerId !== undefined) {
        queryParams.ownerId = params.ownerId;
      }
      if (params.minImportance !== undefined) {
        queryParams.minImportance = params.minImportance;
      }
      if (params.page !== undefined) {
        queryParams.page = params.page;
      }
      if (params.pageSize !== undefined) {
        queryParams.pageSize = params.pageSize;
      }
      if (params.sortBy !== undefined) {
        queryParams.sortBy = params.sortBy;
      }
      if (params.sortDir !== undefined) {
        queryParams.sortDir = params.sortDir;
      }
    }

    return this.client.get<PaginatedResponse<MemoryItem>>(
      '/api/v1/memory',
      queryParams,
    );
  }

  /**
   * Clear all memory items for an owner.
   *
   * @param ownerId - Owner ID whose memory should be cleared
   * @param category - Optional category to clear (all categories if not specified)
   * @returns Number of items deleted
   *
   * @example
   * ```ts
   * const result = await client.memory.clear('user-123');
   * console.log(`Deleted ${result.deleted} items`);
   * ```
   */
  async clear(
    ownerId: string,
    category?: MemoryCategory,
  ): Promise<{ deleted: number }> {
    if (!ownerId) {
      throw new Error('Owner ID is required');
    }

    const params: Record<string, unknown> = { ownerId };
    if (category !== undefined) {
      params.category = category;
    }

    return this.client.post<{ deleted: number }>('/api/v1/memory/clear', params);
  }

  /**
   * Get all available memory categories.
   *
   * Returns the list of categories that exist in memory for
   * a given owner, along with counts.
   *
   * @param ownerId - Optional owner ID to filter by
   * @returns List of categories with counts
   *
   * @example
   * ```ts
   * const categories = await client.memory.categories('user-123');
   * categories.forEach((cat) => {
   *   console.log(`${cat.category}: ${cat.count} items`);
   * });
   * ```
   */
  async categories(ownerId?: string): Promise<Array<{ category: MemoryCategory; count: number }>> {
    const params: Record<string, unknown> = {};
    if (ownerId !== undefined) {
      params.ownerId = ownerId;
    }

    return this.client.get<Array<{ category: MemoryCategory; count: number }>>(
      '/api/v1/memory/categories',
      params,
    );
  }
}
