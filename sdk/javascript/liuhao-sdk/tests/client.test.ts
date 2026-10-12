/**
 * LiuHaoClient Unit Tests
 *
 * Tests for the LiuHaoClient class including configuration,
 * request methods, error handling, and retry logic.
 */

import { LiuHaoClient } from '../src/client';
import {
  LiuHaoError,
  AuthenticationError,
  NotFoundError,
  ValidationError,
  RateLimitError,
  APIError,
  TimeoutError,
} from '../src/errors';
import { DEFAULT_CONFIG } from '../src/config';

// Mock axios
jest.mock('axios', () => {
  const mockAxiosInstance = {
    request: jest.fn(),
    interceptors: {
      request: { use: jest.fn() },
      response: { use: jest.fn() },
    },
  };

  return {
    default: jest.fn(() => mockAxiosInstance),
    create: jest.fn(() => mockAxiosInstance),
    isAxiosError: jest.fn((error) => error?.isAxiosError === true),
  };
});

import axios from 'axios';

const mockAxios = axios as jest.Mocked<typeof axios>;
const mockRequest = jest.fn();

describe('LiuHaoClient', () => {
  let client: LiuHaoClient;

  beforeEach(() => {
    jest.clearAllMocks();

    // Setup mock axios create to return our mock instance
    (mockAxios.create as jest.Mock).mockReturnValue({
      request: mockRequest,
      interceptors: {
        request: { use: jest.fn() },
        response: { use: jest.fn() },
      },
    });

    client = new LiuHaoClient({
      baseUrl: 'https://api.liuhao.test',
      apiKey: 'test-api-key',
      retries: 0, // Disable retries for basic tests
    });
  });

  // --------------------------------------------------------
  // Constructor & Configuration
  // --------------------------------------------------------

  describe('constructor', () => {
    it('should create a client with default config', () => {
      const defaultClient = new LiuHaoClient({
        baseUrl: 'https://api.liuhao.test',
      });
      const config = defaultClient.getConfig();

      expect(config.baseUrl).toBe('https://api.liuhao.test');
      expect(config.timeout).toBe(DEFAULT_CONFIG.timeout);
      expect(config.retries).toBe(DEFAULT_CONFIG.retries);
    });

    it('should use provided configuration values', () => {
      const customClient = new LiuHaoClient({
        baseUrl: 'https://custom.liuhao.test',
        apiKey: 'custom-key',
        timeout: 10000,
        retries: 5,
      });
      const config = customClient.getConfig();

      expect(config.baseUrl).toBe('https://custom.liuhao.test');
      expect(config.apiKey).toBe('custom-key');
      expect(config.timeout).toBe(10000);
      expect(config.retries).toBe(5);
    });

    it('should remove trailing slash from baseUrl', () => {
      const customClient = new LiuHaoClient({
        baseUrl: 'https://api.liuhao.test/',
      });
      const config = customClient.getConfig();

      expect(config.baseUrl).toBe('https://api.liuhao.test');
    });

    it('should throw for invalid baseUrl', () => {
      expect(() => {
        new LiuHaoClient({ baseUrl: 'not-a-url' });
      }).toThrow('not a valid URL');
    });

    it('should throw if baseUrl is empty', () => {
      expect(() => {
        new LiuHaoClient({ baseUrl: '' });
      }).toThrow('baseUrl is required');
    });
  });

  // --------------------------------------------------------
  // Request Methods
  // --------------------------------------------------------

  describe('request methods', () => {
    beforeEach(() => {
      mockRequest.mockResolvedValue({
        status: 200,
        data: { success: true },
      });
    });

    it('should make a GET request', async () => {
      const result = await client.get('/test');

      expect(result).toEqual({ success: true });
      expect(mockRequest).toHaveBeenCalledWith(
        expect.objectContaining({
          method: 'get',
          url: 'https://api.liuhao.test/test',
        }),
      );
    });

    it('should make a POST request with data', async () => {
      const data = { name: 'test' };
      const result = await client.post('/test', data);

      expect(result).toEqual({ success: true });
      expect(mockRequest).toHaveBeenCalledWith(
        expect.objectContaining({
          method: 'post',
          data,
        }),
      );
    });

    it('should make a PUT request', async () => {
      const data = { name: 'updated' };
      await client.put('/test/1', data);

      expect(mockRequest).toHaveBeenCalledWith(
        expect.objectContaining({
          method: 'put',
          data,
        }),
      );
    });

    it('should make a PATCH request', async () => {
      const data = { name: 'patched' };
      await client.patch('/test/1', data);

      expect(mockRequest).toHaveBeenCalledWith(
        expect.objectContaining({
          method: 'patch',
          data,
        }),
      );
    });

    it('should make a DELETE request', async () => {
      await client.delete('/test/1');

      expect(mockRequest).toHaveBeenCalledWith(
        expect.objectContaining({
          method: 'delete',
        }),
      );
    });

    it('should pass query parameters', async () => {
      const params = { page: 1, pageSize: 10 };
      await client.get('/test', params);

      expect(mockRequest).toHaveBeenCalledWith(
        expect.objectContaining({
          params,
        }),
      );
    });
  });

  // --------------------------------------------------------
  // Error Handling
  // --------------------------------------------------------

  describe('error handling', () => {
    it('should throw AuthenticationError for 401 responses', async () => {
      mockRequest.mockResolvedValue({
        status: 401,
        data: {
          code: 'UNAUTHORIZED',
          message: 'Invalid API key',
        },
      });

      await expect(client.get('/test')).rejects.toThrow(AuthenticationError);
      await expect(client.get('/test')).rejects.toMatchObject({
        code: 'UNAUTHORIZED',
        statusCode: 401,
      });
    });

    it('should throw NotFoundError for 404 responses', async () => {
      mockRequest.mockResolvedValue({
        status: 404,
        data: {
          code: 'NOT_FOUND',
          message: 'Agent not found',
          details: { resourceType: 'agent', resourceId: '123' },
        },
      });

      await expect(client.get('/test')).rejects.toThrow(NotFoundError);
    });

    it('should throw ValidationError for 400 responses', async () => {
      mockRequest.mockResolvedValue({
        status: 400,
        data: {
          code: 'VALIDATION_ERROR',
          message: 'Invalid input',
          details: { fieldErrors: { name: ['Required'] } },
        },
      });

      await expect(client.get('/test')).rejects.toThrow(ValidationError);
    });

    it('should throw RateLimitError for 429 responses', async () => {
      mockRequest.mockResolvedValue({
        status: 429,
        data: {
          code: 'RATE_LIMIT_EXCEEDED',
          message: 'Too many requests',
          details: { retryAfterMs: 5000 },
        },
      });

      await expect(client.get('/test')).rejects.toThrow(RateLimitError);
    });

    it('should throw APIError for 500 responses', async () => {
      mockRequest.mockResolvedValue({
        status: 500,
        data: {
          code: 'INTERNAL_ERROR',
          message: 'Internal server error',
        },
      });

      await expect(client.get('/test')).rejects.toThrow(APIError);
    });

    it('should throw TimeoutError for timeout errors', async () => {
      const timeoutClient = new LiuHaoClient({
        baseUrl: 'https://api.liuhao.test',
        retries: 0,
      });

      mockRequest.mockRejectedValue({
        isAxiosError: true,
        code: 'ECONNABORTED',
        message: 'timeout of 30000ms exceeded',
      });

      await expect(timeoutClient.get('/test')).rejects.toThrow(TimeoutError);
    });

    it('should throw LiuHaoError for network errors', async () => {
      const networkClient = new LiuHaoClient({
        baseUrl: 'https://api.liuhao.test',
        retries: 0,
      });

      mockRequest.mockRejectedValue({
        isAxiosError: true,
        code: 'ECONNREFUSED',
        message: 'Connection refused',
      });

      await expect(networkClient.get('/test')).rejects.toThrow(LiuHaoError);
    });

    it('should include requestId in errors when provided', async () => {
      mockRequest.mockResolvedValue({
        status: 400,
        data: {
          code: 'VALIDATION_ERROR',
          message: 'Invalid input',
          requestId: 'req-123',
        },
      });

      try {
        await client.get('/test');
        fail('Expected error');
      } catch (error) {
        expect(error).toBeInstanceOf(LiuHaoError);
        expect((error as LiuHaoError).requestId).toBe('req-123');
      }
    });
  });

  // --------------------------------------------------------
  // Retry Logic
  // --------------------------------------------------------

  describe('retry logic', () => {
    it('should retry on 500 errors', async () => {
      const retryClient = new LiuHaoClient({
        baseUrl: 'https://api.liuhao.test',
        apiKey: 'test-key',
        retries: 2,
      });

      // Fail twice, succeed on third
      mockRequest
        .mockRejectedValueOnce({
          isAxiosError: true,
          response: { status: 500, data: { message: 'Server error' } },
        })
        .mockRejectedValueOnce({
          isAxiosError: true,
          response: { status: 500, data: { message: 'Server error' } },
        })
        .mockResolvedValue({
          status: 200,
          data: { success: true },
        });

      const result = await retryClient.get('/test');

      expect(result).toEqual({ success: true });
      expect(mockRequest).toHaveBeenCalledTimes(3);
    });

    it('should not retry on 400 errors', async () => {
      const retryClient = new LiuHaoClient({
        baseUrl: 'https://api.liuhao.test',
        apiKey: 'test-key',
        retries: 3,
      });

      mockRequest.mockResolvedValue({
        status: 400,
        data: { code: 'BAD_REQUEST', message: 'Bad request' },
      });

      await expect(retryClient.get('/test')).rejects.toThrow(ValidationError);
      expect(mockRequest).toHaveBeenCalledTimes(1);
    });

    it('should retry on 429 rate limit errors', async () => {
      const retryClient = new LiuHaoClient({
        baseUrl: 'https://api.liuhao.test',
        apiKey: 'test-key',
        retries: 1,
      });

      mockRequest
        .mockResolvedValueOnce({
          status: 429,
          data: {
            code: 'RATE_LIMIT',
            message: 'Rate limited',
            details: { retryAfterMs: 10 },
          },
        })
        .mockResolvedValueOnce({
          status: 200,
          data: { success: true },
        });

      const result = await retryClient.get('/test');

      expect(result).toEqual({ success: true });
      expect(mockRequest).toHaveBeenCalledTimes(2);
    });
  });

  // --------------------------------------------------------
  // Resource Initialization
  // --------------------------------------------------------

  describe('resource initialization', () => {
    it('should initialize all resource classes', () => {
      expect(client.agents).toBeDefined();
      expect(client.memory).toBeDefined();
      expect(client.knowledge).toBeDefined();
      expect(client.tasks).toBeDefined();
      expect(client.health).toBeDefined();
    });
  });
});
