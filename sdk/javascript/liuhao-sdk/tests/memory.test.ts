/**
 * MemoryResource Unit Tests
 *
 * Tests for the MemoryResource class methods.
 */

import { MemoryResource } from '../src/resources/memory';
import { MemoryCategory } from '../src/types';
import type { LiuHaoClient } from '../src/client';

// Mock the client
const mockGet = jest.fn();
const mockPost = jest.fn();
const mockPatch = jest.fn();
const mockDelete = jest.fn();

const mockClient = {
  get: mockGet,
  post: mockPost,
  patch: mockPatch,
  delete: mockDelete,
} as unknown as LiuHaoClient;

describe('MemoryResource', () => {
  let memory: MemoryResource;

  beforeEach(() => {
    jest.clearAllMocks();
    memory = new MemoryResource(mockClient);
  });

  // --------------------------------------------------------
  // store
  // --------------------------------------------------------

  describe('store', () => {
    const storeParams = {
      content: 'User prefers dark mode',
      category: MemoryCategory.PREFERENCE,
      ownerId: 'user-123',
      importance: 0.8,
      tags: ['ui', 'preference'],
    };

    const mockMemoryItem = {
      id: 'mem-1',
      content: 'User prefers dark mode',
      category: MemoryCategory.PREFERENCE,
      importance: 0.8,
      ownerId: 'user-123',
      tags: ['ui', 'preference'],
      accessCount: 0,
      createdAt: '2024-01-01T00:00:00Z',
      updatedAt: '2024-01-01T00:00:00Z',
    };

    it('should store a memory item', async () => {
      mockPost.mockResolvedValue(mockMemoryItem);

      const result = await memory.store(storeParams);

      expect(result).toEqual(mockMemoryItem);
      expect(mockPost).toHaveBeenCalledWith('/api/v1/memory', storeParams);
    });

    it('should throw if content is missing', async () => {
      await expect(
        memory.store({
          content: '',
          category: MemoryCategory.FACT,
          ownerId: 'user-123',
        }),
      ).rejects.toThrow('Memory content is required');
      expect(mockPost).not.toHaveBeenCalled();
    });

    it('should throw if ownerId is missing', async () => {
      await expect(
        memory.store({
          content: 'Test memory',
          category: MemoryCategory.FACT,
          ownerId: '',
        }),
      ).rejects.toThrow('Owner ID is required');
      expect(mockPost).not.toHaveBeenCalled();
    });
  });

  // --------------------------------------------------------
  // retrieve
  // --------------------------------------------------------

  describe('retrieve', () => {
    const mockMemoryItem = {
      id: 'mem-1',
      content: 'Test memory content',
      category: MemoryCategory.FACT,
      importance: 0.5,
      ownerId: 'user-123',
      tags: [],
      accessCount: 5,
      createdAt: '2024-01-01T00:00:00Z',
      updatedAt: '2024-01-01T00:00:00Z',
      lastAccessedAt: '2024-01-02T00:00:00Z',
    };

    it('should retrieve a memory item by ID', async () => {
      mockGet.mockResolvedValue(mockMemoryItem);

      const result = await memory.retrieve('mem-1');

      expect(result).toEqual(mockMemoryItem);
      expect(mockGet).toHaveBeenCalledWith('/api/v1/memory/mem-1');
    });

    it('should throw if ID is empty', async () => {
      await expect(memory.retrieve('')).rejects.toThrow('Memory ID is required');
      expect(mockGet).not.toHaveBeenCalled();
    });
  });

  // --------------------------------------------------------
  // update
  // --------------------------------------------------------

  describe('update', () => {
    const updateParams = {
      content: 'Updated memory content',
      importance: 0.9,
    };

    const mockUpdatedItem = {
      id: 'mem-1',
      content: 'Updated memory content',
      category: MemoryCategory.FACT,
      importance: 0.9,
      ownerId: 'user-123',
      tags: [],
      accessCount: 0,
      createdAt: '2024-01-01T00:00:00Z',
      updatedAt: '2024-01-02T00:00:00Z',
    };

    it('should update a memory item', async () => {
      mockPatch.mockResolvedValue(mockUpdatedItem);

      const result = await memory.update('mem-1', updateParams);

      expect(result).toEqual(mockUpdatedItem);
      expect(mockPatch).toHaveBeenCalledWith(
        '/api/v1/memory/mem-1',
        updateParams,
      );
    });

    it('should throw if ID is empty', async () => {
      await expect(memory.update('', updateParams)).rejects.toThrow(
        'Memory ID is required',
      );
      expect(mockPatch).not.toHaveBeenCalled();
    });
  });

  // --------------------------------------------------------
  // delete
  // --------------------------------------------------------

  describe('delete', () => {
    it('should delete a memory item', async () => {
      mockDelete.mockResolvedValue(undefined);

      await memory.delete('mem-1');

      expect(mockDelete).toHaveBeenCalledWith('/api/v1/memory/mem-1');
    });

    it('should throw if ID is empty', async () => {
      await expect(memory.delete('')).rejects.toThrow('Memory ID is required');
      expect(mockDelete).not.toHaveBeenCalled();
    });
  });

  // --------------------------------------------------------
  // search
  // --------------------------------------------------------

  describe('search', () => {
    const mockSearchResponse = {
      items: [
        {
          id: 'mem-1',
          content: 'User prefers dark mode for the UI',
          category: MemoryCategory.PREFERENCE,
          importance: 0.7,
          ownerId: 'user-123',
          tags: ['ui', 'theme'],
          similarity: 0.92,
          accessCount: 3,
          createdAt: '2024-01-01T00:00:00Z',
          updatedAt: '2024-01-01T00:00:00Z',
        },
      ],
      total: 1,
      page: 1,
      pageSize: 10,
      totalPages: 1,
      hasNext: false,
      hasPrev: false,
    };

    it('should search memory with query', async () => {
      mockGet.mockResolvedValue(mockSearchResponse);

      const result = await memory.search({
        query: 'display preferences',
        ownerId: 'user-123',
      });

      expect(result).toEqual(mockSearchResponse);
      expect(mockGet).toHaveBeenCalledWith(
        '/api/v1/memory/search',
        expect.objectContaining({
          query: 'display preferences',
          ownerId: 'user-123',
        }),
      );
    });

    it('should search with all parameters', async () => {
      mockGet.mockResolvedValue(mockSearchResponse);

      const result = await memory.search({
        query: 'test query',
        category: MemoryCategory.PREFERENCE,
        tags: ['ui', 'theme'],
        ownerId: 'user-123',
        minSimilarity: 0.7,
        limit: 5,
        page: 1,
        pageSize: 10,
        includeEmbedding: true,
      });

      expect(result).toEqual(mockSearchResponse);
      expect(mockGet).toHaveBeenCalledWith(
        '/api/v1/memory/search',
        expect.objectContaining({
          query: 'test query',
          category: MemoryCategory.PREFERENCE,
          tags: ['ui', 'theme'],
          ownerId: 'user-123',
          minSimilarity: 0.7,
          limit: 5,
          page: 1,
          pageSize: 10,
          includeEmbedding: true,
        }),
      );
    });

    it('should throw if query is missing', async () => {
      await expect(
        memory.search({ query: '', ownerId: 'user-123' }),
      ).rejects.toThrow('Search query is required');
      expect(mockGet).not.toHaveBeenCalled();
    });
  });

  // --------------------------------------------------------
  // list
  // --------------------------------------------------------

  describe('list', () => {
    const mockListResponse = {
      items: [],
      total: 0,
      page: 1,
      pageSize: 10,
      totalPages: 0,
      hasNext: false,
      hasPrev: false,
    };

    it('should list memory items', async () => {
      mockGet.mockResolvedValue(mockListResponse);

      const result = await memory.list();

      expect(result).toEqual(mockListResponse);
      expect(mockGet).toHaveBeenCalledWith('/api/v1/memory', {});
    });

    it('should list with filters', async () => {
      mockGet.mockResolvedValue(mockListResponse);

      const result = await memory.list({
        category: MemoryCategory.TASK,
        ownerId: 'user-123',
        minImportance: 0.5,
        page: 1,
        pageSize: 20,
      });

      expect(result).toEqual(mockListResponse);
      expect(mockGet).toHaveBeenCalledWith(
        '/api/v1/memory',
        expect.objectContaining({
          category: MemoryCategory.TASK,
          ownerId: 'user-123',
          minImportance: 0.5,
          page: 1,
          pageSize: 20,
        }),
      );
    });
  });

  // --------------------------------------------------------
  // clear
  // --------------------------------------------------------

  describe('clear', () => {
    it('should clear all memory for an owner', async () => {
      mockPost.mockResolvedValue({ deleted: 42 });

      const result = await memory.clear('user-123');

      expect(result.deleted).toBe(42);
      expect(mockPost).toHaveBeenCalledWith(
        '/api/v1/memory/clear',
        expect.objectContaining({ ownerId: 'user-123' }),
      );
    });

    it('should clear memory for a specific category', async () => {
      mockPost.mockResolvedValue({ deleted: 10 });

      const result = await memory.clear('user-123', MemoryCategory.TASK);

      expect(result.deleted).toBe(10);
      expect(mockPost).toHaveBeenCalledWith(
        '/api/v1/memory/clear',
        expect.objectContaining({
          ownerId: 'user-123',
          category: MemoryCategory.TASK,
        }),
      );
    });

    it('should throw if ownerId is empty', async () => {
      await expect(memory.clear('')).rejects.toThrow('Owner ID is required');
      expect(mockPost).not.toHaveBeenCalled();
    });
  });

  // --------------------------------------------------------
  // categories
  // --------------------------------------------------------

  describe('categories', () => {
    const mockCategories = [
      { category: MemoryCategory.FACT, count: 50 },
      { category: MemoryCategory.PREFERENCE, count: 20 },
      { category: MemoryCategory.TASK, count: 30 },
    ];

    it('should get all categories', async () => {
      mockGet.mockResolvedValue(mockCategories);

      const result = await memory.categories();

      expect(result).toEqual(mockCategories);
      expect(mockGet).toHaveBeenCalledWith('/api/v1/memory/categories', {});
    });

    it('should get categories for a specific owner', async () => {
      mockGet.mockResolvedValue(mockCategories);

      const result = await memory.categories('user-123');

      expect(result).toEqual(mockCategories);
      expect(mockGet).toHaveBeenCalledWith(
        '/api/v1/memory/categories',
        { ownerId: 'user-123' },
      );
    });
  });
});
