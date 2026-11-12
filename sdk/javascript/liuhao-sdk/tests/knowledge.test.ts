/**
 * KnowledgeResource Unit Tests
 *
 * Tests for the KnowledgeResource class methods.
 */

import { KnowledgeResource } from '../src/resources/knowledge';
import { KnowledgeBaseType } from '../src/types';
import type { LiuHaoClient } from '../src/client';

// Mock the client
const mockGet = jest.fn();
const mockPost = jest.fn();
const mockDelete = jest.fn();

const mockClient = {
  get: mockGet,
  post: mockPost,
  delete: mockDelete,
} as unknown as LiuHaoClient;

describe('KnowledgeResource', () => {
  let knowledge: KnowledgeResource;

  beforeEach(() => {
    jest.clearAllMocks();
    knowledge = new KnowledgeResource(mockClient);
  });

  // --------------------------------------------------------
  // listBases
  // --------------------------------------------------------

  describe('listBases', () => {
    const mockResponse = {
      items: [
        {
          id: 'kb-1',
          name: 'Product Docs',
          type: KnowledgeBaseType.VECTOR,
          documentCount: 50,
          sizeBytes: 1024000,
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

    it('should list knowledge bases', async () => {
      mockGet.mockResolvedValue(mockResponse);

      const result = await knowledge.listBases();

      expect(result).toEqual(mockResponse);
      expect(mockGet).toHaveBeenCalledWith('/api/v1/knowledge/bases', {});
    });

    it('should list with pagination and filters', async () => {
      mockGet.mockResolvedValue(mockResponse);

      const result = await knowledge.listBases({
        page: 2,
        pageSize: 20,
        type: KnowledgeBaseType.HYBRID,
        search: 'docs',
      });

      expect(result).toEqual(mockResponse);
      expect(mockGet).toHaveBeenCalledWith(
        '/api/v1/knowledge/bases',
        expect.objectContaining({
          page: 2,
          pageSize: 20,
          type: KnowledgeBaseType.HYBRID,
          search: 'docs',
        }),
      );
    });
  });

  // --------------------------------------------------------
  // createBase
  // --------------------------------------------------------

  describe('createBase', () => {
    const createParams = {
      name: 'Product Handbook',
      description: 'Company product documentation',
      type: KnowledgeBaseType.VECTOR,
      embeddingModel: 'text-embedding-3-small',
    };

    const mockBase = {
      id: 'kb-new',
      ...createParams,
      documentCount: 0,
      sizeBytes: 0,
      createdAt: '2024-01-01T00:00:00Z',
      updatedAt: '2024-01-01T00:00:00Z',
    };

    it('should create a knowledge base', async () => {
      mockPost.mockResolvedValue(mockBase);

      const result = await knowledge.createBase(createParams);

      expect(result).toEqual(mockBase);
      expect(mockPost).toHaveBeenCalledWith(
        '/api/v1/knowledge/bases',
        createParams,
      );
    });

    it('should throw if name is missing', async () => {
      await expect(
        knowledge.createBase({
          name: '',
          type: KnowledgeBaseType.VECTOR,
        }),
      ).rejects.toThrow('Knowledge base name is required');
      expect(mockPost).not.toHaveBeenCalled();
    });

    it('should throw if type is missing', async () => {
      await expect(
        knowledge.createBase({
          name: 'Test',
          type: '' as KnowledgeBaseType,
        }),
      ).rejects.toThrow('Knowledge base type is required');
      expect(mockPost).not.toHaveBeenCalled();
    });
  });

  // --------------------------------------------------------
  // getBase
  // --------------------------------------------------------

  describe('getBase', () => {
    const mockBase = {
      id: 'kb-1',
      name: 'Product Docs',
      description: 'Product documentation',
      type: KnowledgeBaseType.VECTOR,
      documentCount: 50,
      sizeBytes: 1024000,
      embeddingModel: 'text-embedding-3-small',
      createdAt: '2024-01-01T00:00:00Z',
      updatedAt: '2024-01-01T00:00:00Z',
    };

    it('should get a knowledge base by ID', async () => {
      mockGet.mockResolvedValue(mockBase);

      const result = await knowledge.getBase('kb-1');

      expect(result).toEqual(mockBase);
      expect(mockGet).toHaveBeenCalledWith('/api/v1/knowledge/bases/kb-1');
    });

    it('should throw if baseId is empty', async () => {
      await expect(knowledge.getBase('')).rejects.toThrow(
        'Knowledge base ID is required',
      );
      expect(mockGet).not.toHaveBeenCalled();
    });
  });

  // --------------------------------------------------------
  // deleteBase
  // --------------------------------------------------------

  describe('deleteBase', () => {
    it('should delete a knowledge base', async () => {
      mockDelete.mockResolvedValue(undefined);

      await knowledge.deleteBase('kb-1');

      expect(mockDelete).toHaveBeenCalledWith('/api/v1/knowledge/bases/kb-1');
    });

    it('should throw if baseId is empty', async () => {
      await expect(knowledge.deleteBase('')).rejects.toThrow(
        'Knowledge base ID is required',
      );
      expect(mockDelete).not.toHaveBeenCalled();
    });
  });

  // --------------------------------------------------------
  // uploadDocument
  // --------------------------------------------------------

  describe('uploadDocument', () => {
    const uploadParams = {
      title: 'Getting Started',
      content: 'Welcome to the platform...',
      tags: ['guide', 'beginner'],
      chunk: true,
      chunkSize: 1000,
    };

    const mockDocument = {
      id: 'doc-1',
      knowledgeBaseId: 'kb-1',
      title: 'Getting Started',
      tags: ['guide', 'beginner'],
      indexingStatus: 'processing' as const,
      chunkCount: 5,
      createdAt: '2024-01-01T00:00:00Z',
      updatedAt: '2024-01-01T00:00:00Z',
    };

    it('should upload a document', async () => {
      mockPost.mockResolvedValue(mockDocument);

      const result = await knowledge.uploadDocument('kb-1', uploadParams);

      expect(result).toEqual(mockDocument);
      expect(mockPost).toHaveBeenCalledWith(
        '/api/v1/knowledge/bases/kb-1/documents',
        uploadParams,
      );
    });

    it('should throw if baseId is empty', async () => {
      await expect(
        knowledge.uploadDocument('', uploadParams),
      ).rejects.toThrow('Knowledge base ID is required');
      expect(mockPost).not.toHaveBeenCalled();
    });

    it('should throw if title is missing', async () => {
      await expect(
        knowledge.uploadDocument('kb-1', { ...uploadParams, title: '' }),
      ).rejects.toThrow('Document title is required');
      expect(mockPost).not.toHaveBeenCalled();
    });

    it('should throw if both content and source are missing', async () => {
      await expect(
        knowledge.uploadDocument('kb-1', { title: 'Test' }),
      ).rejects.toThrow('Either content or source must be provided');
      expect(mockPost).not.toHaveBeenCalled();
    });
  });

  // --------------------------------------------------------
  // listDocuments
  // --------------------------------------------------------

  describe('listDocuments', () => {
    const mockResponse = {
      items: [],
      total: 0,
      page: 1,
      pageSize: 10,
      totalPages: 0,
      hasNext: false,
      hasPrev: false,
    };

    it('should list documents in a knowledge base', async () => {
      mockGet.mockResolvedValue(mockResponse);

      const result = await knowledge.listDocuments('kb-1');

      expect(result).toEqual(mockResponse);
      expect(mockGet).toHaveBeenCalledWith(
        '/api/v1/knowledge/bases/kb-1/documents',
        {},
      );
    });

    it('should list documents with filters', async () => {
      mockGet.mockResolvedValue(mockResponse);

      const result = await knowledge.listDocuments('kb-1', {
        page: 1,
        pageSize: 20,
        tags: ['guide'],
        indexingStatus: 'completed',
        search: 'getting started',
      });

      expect(result).toEqual(mockResponse);
      expect(mockGet).toHaveBeenCalledWith(
        '/api/v1/knowledge/bases/kb-1/documents',
        expect.objectContaining({
          page: 1,
          pageSize: 20,
          tags: ['guide'],
          indexingStatus: 'completed',
          search: 'getting started',
        }),
      );
    });

    it('should throw if baseId is empty', async () => {
      await expect(knowledge.listDocuments('')).rejects.toThrow(
        'Knowledge base ID is required',
      );
      expect(mockGet).not.toHaveBeenCalled();
    });
  });

  // --------------------------------------------------------
  // deleteDocument
  // --------------------------------------------------------

  describe('deleteDocument', () => {
    it('should delete a document', async () => {
      mockDelete.mockResolvedValue(undefined);

      await knowledge.deleteDocument('kb-1', 'doc-1');

      expect(mockDelete).toHaveBeenCalledWith(
        '/api/v1/knowledge/bases/kb-1/documents/doc-1',
      );
    });

    it('should throw if baseId is empty', async () => {
      await expect(knowledge.deleteDocument('', 'doc-1')).rejects.toThrow(
        'Knowledge base ID is required',
      );
      expect(mockDelete).not.toHaveBeenCalled();
    });

    it('should throw if documentId is empty', async () => {
      await expect(knowledge.deleteDocument('kb-1', '')).rejects.toThrow(
        'Document ID is required',
      );
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
          text: 'To reset your password, click on...',
          documentId: 'doc-1',
          documentTitle: 'User Guide',
          score: 0.95,
          chunkIndex: 3,
        },
      ],
      total: 1,
      page: 1,
      pageSize: 10,
      totalPages: 1,
      hasNext: false,
      hasPrev: false,
    };

    it('should search a knowledge base', async () => {
      mockGet.mockResolvedValue(mockSearchResponse);

      const result = await knowledge.search('kb-1', {
        query: 'How to reset password',
      });

      expect(result).toEqual(mockSearchResponse);
      expect(mockGet).toHaveBeenCalledWith(
        '/api/v1/knowledge/bases/kb-1/search',
        expect.objectContaining({
          query: 'How to reset password',
        }),
      );
    });

    it('should search with all parameters', async () => {
      mockGet.mockResolvedValue(mockSearchResponse);

      const result = await knowledge.search('kb-1', {
        query: 'test query',
        limit: 5,
        minScore: 0.7,
        documentIds: ['doc-1', 'doc-2'],
        tags: ['guide'],
        rerank: true,
        page: 1,
        pageSize: 10,
      });

      expect(result).toEqual(mockSearchResponse);
      expect(mockGet).toHaveBeenCalledWith(
        '/api/v1/knowledge/bases/kb-1/search',
        expect.objectContaining({
          query: 'test query',
          limit: 5,
          minScore: 0.7,
          documentIds: ['doc-1', 'doc-2'],
          tags: ['guide'],
          rerank: true,
          page: 1,
          pageSize: 10,
        }),
      );
    });

    it('should throw if baseId is empty', async () => {
      await expect(
        knowledge.search('', { query: 'test' }),
      ).rejects.toThrow('Knowledge base ID is required');
      expect(mockGet).not.toHaveBeenCalled();
    });

    it('should throw if query is missing', async () => {
      await expect(
        knowledge.search('kb-1', { query: '' }),
      ).rejects.toThrow('Search query is required');
      expect(mockGet).not.toHaveBeenCalled();
    });
  });

  // --------------------------------------------------------
  // createCollection
  // --------------------------------------------------------

  describe('createCollection', () => {
    const createParams = {
      name: 'All Docs',
      description: 'All knowledge bases combined',
      knowledgeBaseIds: ['kb-1', 'kb-2'],
    };

    const mockCollection = {
      id: 'coll-1',
      ...createParams,
      createdAt: '2024-01-01T00:00:00Z',
      updatedAt: '2024-01-01T00:00:00Z',
    };

    it('should create a collection', async () => {
      mockPost.mockResolvedValue(mockCollection);

      const result = await knowledge.createCollection(createParams);

      expect(result).toEqual(mockCollection);
      expect(mockPost).toHaveBeenCalledWith(
        '/api/v1/knowledge/collections',
        createParams,
      );
    });

    it('should throw if name is missing', async () => {
      await expect(
        knowledge.createCollection({ name: '' }),
      ).rejects.toThrow('Collection name is required');
      expect(mockPost).not.toHaveBeenCalled();
    });
  });

  // --------------------------------------------------------
  // listCollections
  // --------------------------------------------------------

  describe('listCollections', () => {
    const mockResponse = {
      items: [],
      total: 0,
      page: 1,
      pageSize: 10,
      totalPages: 0,
      hasNext: false,
      hasPrev: false,
    };

    it('should list collections', async () => {
      mockGet.mockResolvedValue(mockResponse);

      const result = await knowledge.listCollections();

      expect(result).toEqual(mockResponse);
      expect(mockGet).toHaveBeenCalledWith(
        '/api/v1/knowledge/collections',
        {},
      );
    });

    it('should list collections with pagination', async () => {
      mockGet.mockResolvedValue(mockResponse);

      const result = await knowledge.listCollections({
        page: 2,
        pageSize: 20,
      });

      expect(result).toEqual(mockResponse);
      expect(mockGet).toHaveBeenCalledWith(
        '/api/v1/knowledge/collections',
        expect.objectContaining({
          page: 2,
          pageSize: 20,
        }),
      );
    });
  });
});
