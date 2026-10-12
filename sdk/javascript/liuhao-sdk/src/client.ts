/**
 * LiuHao AI OS SDK Client
 *
 * Core HTTP client that handles authentication, request lifecycle,
 * retry logic, and error handling. All resource classes use this
 * client to make API requests.
 */

import axios, {
  AxiosInstance,
  AxiosRequestConfig,
  AxiosResponse,
  isAxiosError,
} from 'axios';

import type {
  HttpMethod,
  LiuHaoConfig,
  RequestOptions,
} from './types';
import {
  LiuHaoError,
  TimeoutError,
  fromResponse,
} from './errors';
import {
  resolveConfig,
  validateConfig,
  mergeConfig,
} from './config';

import { AgentsResource } from './resources/agents';
import { MemoryResource } from './resources/memory';
import { KnowledgeResource } from './resources/knowledge';
import { TasksResource } from './resources/tasks';
import { HealthResource } from './resources/health';

/**
 * LiuHaoClient - Main SDK client class.
 *
 * Provides access to all API resources and handles HTTP communication,
 * authentication, retries, and error handling.
 *
 * @example
 * ```ts
 * import { LiuHaoClient } from '@liuhao/sdk';
 *
 * const client = new LiuHaoClient({
 *   baseUrl: 'https://api.liuhao.ai',
 *   apiKey: 'your-api-key',
 * });
 *
 * const agents = await client.agents.list();
 * ```
 */
export class LiuHaoClient {
  /** SDK configuration */
  private readonly config: LiuHaoConfig;

  /** Axios instance for HTTP requests */
  private readonly axios: AxiosInstance;

  // --------------------------------------------------------
  // Resource accessors
  // --------------------------------------------------------

  /** Agents API resource */
  public readonly agents: AgentsResource;

  /** Memory API resource */
  public readonly memory: MemoryResource;

  /** Knowledge base API resource */
  public readonly knowledge: KnowledgeResource;

  /** Tasks API resource */
  public readonly tasks: TasksResource;

  /** Health check API resource */
  public readonly health: HealthResource;

  // --------------------------------------------------------
  // Constructor
  // --------------------------------------------------------

  /**
   * Create a new LiuHaoClient instance.
   *
   * Configuration is resolved with the following priority:
   * 1. Explicit config parameter
   * 2. Environment variables (LIUHAO_API_KEY, LIUHAO_BASE_URL, etc.)
   * 3. Default values
   *
   * @param config - Client configuration options
   * @throws {Error} If the configuration is invalid
   */
  constructor(config?: Partial<LiuHaoConfig>) {
    this.config = resolveConfig(config);
    validateConfig(this.config);

    this.axios = this.createAxiosInstance();

    // Initialize resource classes
    this.agents = new AgentsResource(this);
    this.memory = new MemoryResource(this);
    this.knowledge = new KnowledgeResource(this);
    this.tasks = new TasksResource(this);
    this.health = new HealthResource(this);
  }

  // --------------------------------------------------------
  // Public API
  // --------------------------------------------------------

  /**
   * Get the current client configuration (read-only).
   */
  getConfig(): Readonly<LiuHaoConfig> {
    return { ...this.config };
  }

  /**
   * Make an HTTP request to the LiuHao API.
   *
   * This method handles authentication headers, error mapping,
   * and retry logic automatically.
   *
   * @template T - Expected response data type
   * @param method - HTTP method
   * @param path - API path (relative to baseUrl)
   * @param data - Request body data
   * @param params - Query parameters
   * @param options - Per-request options
   * @returns Response data
   * @throws {LiuHaoError} If the request fails
   */
  async request<T = unknown>(
    method: HttpMethod,
    path: string,
    data?: unknown,
    params?: Record<string, unknown>,
    options?: RequestOptions,
  ): Promise<T> {
    const url = this.buildUrl(path);
    const effectiveConfig = options ? mergeConfig(this.config, {
      timeout: options.timeout,
      retries: options.retries,
    }) : this.config;

    const requestConfig: AxiosRequestConfig = {
      method: method.toLowerCase() as AxiosRequestConfig['method'],
      url,
      params,
      data,
      timeout: options?.timeout ?? this.config.timeout,
      headers: {
        ...this.config.defaultHeaders,
        ...options?.headers,
      },
      validateStatus: () => true, // We handle all status codes ourselves
    };

    // Add abort signal if provided
    if (options?.signal) {
      requestConfig.signal = options.signal;
    }

    return this.executeWithRetry<T>(requestConfig, effectiveConfig.retries);
  }

  /**
   * Convenience method for GET requests.
   */
  async get<T = unknown>(
    path: string,
    params?: Record<string, unknown>,
    options?: RequestOptions,
  ): Promise<T> {
    return this.request<T>('GET', path, undefined, params, options);
  }

  /**
   * Convenience method for POST requests.
   */
  async post<T = unknown>(
    path: string,
    data?: unknown,
    options?: RequestOptions,
  ): Promise<T> {
    return this.request<T>('POST', path, data, undefined, options);
  }

  /**
   * Convenience method for PUT requests.
   */
  async put<T = unknown>(
    path: string,
    data?: unknown,
    options?: RequestOptions,
  ): Promise<T> {
    return this.request<T>('PUT', path, data, undefined, options);
  }

  /**
   * Convenience method for PATCH requests.
   */
  async patch<T = unknown>(
    path: string,
    data?: unknown,
    options?: RequestOptions,
  ): Promise<T> {
    return this.request<T>('PATCH', path, data, undefined, options);
  }

  /**
   * Convenience method for DELETE requests.
   */
  async delete<T = unknown>(
    path: string,
    params?: Record<string, unknown>,
    options?: RequestOptions,
  ): Promise<T> {
    return this.request<T>('DELETE', path, undefined, params, options);
  }

  // --------------------------------------------------------
  // Internal methods
  // --------------------------------------------------------

  /**
   * Creates the axios instance with default configuration.
   */
  private createAxiosInstance(): AxiosInstance {
    const instance = axios.create({
      baseURL: this.config.baseUrl,
      timeout: this.config.timeout,
      headers: {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'User-Agent': this.config.userAgent,
      },
      withCredentials: false,
    });

    // Request interceptor: add auth header
    instance.interceptors.request.use((requestConfig) => {
      if (this.config.apiKey) {
        requestConfig.headers = requestConfig.headers ?? {};
        requestConfig.headers['Authorization'] = `Bearer ${this.config.apiKey}`;
      }
      return requestConfig;
    });

    return instance;
  }

  /**
   * Builds the full URL from a relative path.
   */
  private buildUrl(path: string): string {
    if (path.startsWith('http://') || path.startsWith('https://')) {
      return path;
    }
    const normalizedPath = path.startsWith('/') ? path : `/${path}`;
    return `${this.config.baseUrl}${normalizedPath}`;
  }

  /**
   * Executes a request with retry logic.
   *
   * Retries on network errors, 5xx status codes, and 429 rate limit errors.
   * Uses exponential backoff with jitter.
   */
  private async executeWithRetry<T>(
    requestConfig: AxiosRequestConfig,
    maxRetries: number,
  ): Promise<T> {
    let lastError: Error | undefined;

    for (let attempt = 0; attempt <= maxRetries; attempt++) {
      try {
        const response: AxiosResponse = await this.axios.request(requestConfig);

        // Check if response is successful
        if (response.status >= 200 && response.status < 300) {
          return response.data as T;
        }

        // Non-2xx response - throw appropriate error
        const error = fromResponse(response.status, response.data);

        // Only retry on certain error types
        if (this.shouldRetry(error, attempt, maxRetries)) {
          lastError = error;
          await this.delay(this.getRetryDelay(attempt, error));
          continue;
        }

        throw error;
      } catch (error) {
        if (error instanceof LiuHaoError) {
          throw error;
        }

        if (isAxiosError(error)) {
          // Handle timeout
          if (error.code === 'ECONNABORTED' || error.message.includes('timeout')) {
            const timeoutError = new TimeoutError(
              'Request timed out',
              this.config.timeout,
            );

            if (attempt < maxRetries) {
              lastError = timeoutError;
              await this.delay(this.getRetryDelay(attempt));
              continue;
            }
            throw timeoutError;
          }

          // Handle network errors
          if (
            error.code === 'ECONNREFUSED' ||
            error.code === 'ENETUNREACH' ||
            error.code === 'ETIMEDOUT' ||
            error.code === 'ERR_NETWORK' ||
            !error.response
          ) {
            const networkError = new LiuHaoError(
              `Network error: ${error.message}`,
              'NETWORK_ERROR',
              { code: error.code },
            );

            if (attempt < maxRetries) {
              lastError = networkError;
              await this.delay(this.getRetryDelay(attempt));
              continue;
            }
            throw networkError;
          }

          // Response received but with error status
          if (error.response) {
            const apiError = fromResponse(error.response.status, error.response.data);

            if (this.shouldRetry(apiError, attempt, maxRetries)) {
              lastError = apiError;
              await this.delay(this.getRetryDelay(attempt, apiError));
              continue;
            }

            throw apiError;
          }
        }

        // Unknown error type
        if (error instanceof Error) {
          if (attempt < maxRetries && this.isRetryableError(error)) {
            lastError = error;
            await this.delay(this.getRetryDelay(attempt));
            continue;
          }
          throw new LiuHaoError(error.message, 'UNKNOWN_ERROR');
        }

        throw new LiuHaoError('Unknown error occurred', 'UNKNOWN_ERROR');
      }
    }

    // All retries exhausted
    throw lastError ?? new LiuHaoError('Request failed after retries', 'RETRY_EXHAUSTED');
  }

  /**
   * Determines if an error should trigger a retry.
   */
  private shouldRetry(error: LiuHaoError, attempt: number, maxRetries: number): boolean {
    if (attempt >= maxRetries) {
      return false;
    }

    // Retry on rate limit errors (429)
    if (error.statusCode === 429) {
      return true;
    }

    // Retry on server errors (5xx)
    if (error.statusCode !== undefined && error.statusCode >= 500) {
      return true;
    }

    return false;
  }

  /**
   * Checks if a generic error is retryable.
   */
  private isRetryableError(error: Error): boolean {
    // Network-related errors are typically retryable
    const retryableMessages = [
      'network',
      'connection',
      'timeout',
      'econnreset',
      'etimedout',
      'esockettimedout',
    ];

    const message = error.message.toLowerCase();
    return retryableMessages.some((m) => message.includes(m));
  }

  /**
   * Calculates the retry delay with exponential backoff and jitter.
   *
   * @param attempt - Current attempt number (0-indexed)
   * @param error - The error that caused the retry (for rate limit info)
   * @returns Delay in milliseconds
   */
  private getRetryDelay(attempt: number, error?: LiuHaoError): number {
    // Use retry-after header if available (for rate limits)
    if (error && 'retryAfterMs' in error && typeof (error as { retryAfterMs?: number }).retryAfterMs === 'number') {
      const retryAfter = (error as { retryAfterMs: number }).retryAfterMs;
      if (retryAfter > 0) {
        return retryAfter;
      }
    }

    // Exponential backoff: 1s, 2s, 4s, 8s, ...
    const baseDelay = 1000 * Math.pow(2, attempt);

    // Add jitter (±25%) to prevent thundering herd
    const jitter = baseDelay * 0.25;
    const delay = baseDelay + (Math.random() * jitter * 2 - jitter);

    // Cap at 30 seconds
    return Math.min(delay, 30000);
  }

  /**
   * Simple delay helper.
   */
  private delay(ms: number): Promise<void> {
    return new Promise((resolve) => {
      setTimeout(resolve, ms);
    });
  }
}
