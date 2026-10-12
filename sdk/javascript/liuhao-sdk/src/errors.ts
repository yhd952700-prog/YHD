/**
 * LiuHao AI OS SDK Error Classes
 *
 * Provides a hierarchy of custom error classes for different
 * types of API failures, plus a factory function that maps
 * HTTP response status codes to the appropriate error type.
 */

import type { ErrorResponse } from './types';

/**
 * Base error class for all LiuHao SDK errors.
 *
 * @example
 * ```ts
 * throw new LiuHaoError('Something went wrong', 'UNKNOWN_ERROR', { detail: 'info' });
 * ```
 */
export class LiuHaoError extends Error {
  /** Machine-readable error code */
  public readonly code: string;

  /** Optional structured error details */
  public readonly details?: Record<string, unknown>;

  /** Request identifier for debugging */
  public readonly requestId?: string;

  /** HTTP status code (if applicable) */
  public readonly statusCode?: number;

  /**
   * Create a new LiuHaoError.
   *
   * @param message - Human-readable error message
   * @param code - Machine-readable error code
   * @param details - Optional structured error details
   * @param requestId - Optional request identifier
   * @param statusCode - Optional HTTP status code
   */
  constructor(
    message: string,
    code: string = 'SDK_ERROR',
    details?: Record<string, unknown>,
    requestId?: string,
    statusCode?: number,
  ) {
    super(message);
    this.name = 'LiuHaoError';
    this.code = code;
    this.details = details;
    this.requestId = requestId;
    this.statusCode = statusCode;

    // Maintain proper prototype chain for instanceof checks
    Object.setPrototypeOf(this, LiuHaoError.prototype);
  }

  /**
   * Convert the error to a plain object for serialization.
   */
  toJSON(): Record<string, unknown> {
    return {
      name: this.name,
      message: this.message,
      code: this.code,
      details: this.details,
      requestId: this.requestId,
      statusCode: this.statusCode,
      stack: this.stack,
    };
  }
}

/**
 * Error thrown when authentication fails (401).
 *
 * This typically means the API key is missing, invalid, or expired.
 */
export class AuthenticationError extends LiuHaoError {
  constructor(
    message: string = 'Authentication failed',
    code: string = 'AUTHENTICATION_ERROR',
    details?: Record<string, unknown>,
    requestId?: string,
  ) {
    super(message, code, details, requestId, 401);
    this.name = 'AuthenticationError';
    Object.setPrototypeOf(this, AuthenticationError.prototype);
  }
}

/**
 * Error thrown when the requested resource was not found (404).
 */
export class NotFoundError extends LiuHaoError {
  /** The type of resource that was not found */
  public readonly resourceType?: string;

  /** The ID of the resource that was not found */
  public readonly resourceId?: string;

  constructor(
    message: string = 'Resource not found',
    code: string = 'NOT_FOUND',
    details?: Record<string, unknown>,
    requestId?: string,
    resourceType?: string,
    resourceId?: string,
  ) {
    super(message, code, details, requestId, 404);
    this.name = 'NotFoundError';
    this.resourceType = resourceType;
    this.resourceId = resourceId;
    Object.setPrototypeOf(this, NotFoundError.prototype);
  }
}

/**
 * Error thrown when request validation fails (400).
 *
 * This typically means the request parameters were invalid or
 * required fields were missing.
 */
export class ValidationError extends LiuHaoError {
  /** Field-level validation errors */
  public readonly fieldErrors?: Record<string, string[]>;

  constructor(
    message: string = 'Validation failed',
    code: string = 'VALIDATION_ERROR',
    details?: Record<string, unknown>,
    requestId?: string,
    fieldErrors?: Record<string, string[]>,
  ) {
    super(message, code, details, requestId, 400);
    this.name = 'ValidationError';
    this.fieldErrors = fieldErrors;
    Object.setPrototypeOf(this, ValidationError.prototype);
  }
}

/**
 * Error thrown when the rate limit is exceeded (429).
 *
 * Check the `retryAfterMs` property to know when the next request
 * can be made.
 */
export class RateLimitError extends LiuHaoError {
  /** Number of milliseconds to wait before retrying */
  public readonly retryAfterMs?: number;

  /** Current rate limit */
  public readonly limit?: number;

  /** Remaining requests in the current window */
  public readonly remaining?: number;

  constructor(
    message: string = 'Rate limit exceeded',
    code: string = 'RATE_LIMIT_EXCEEDED',
    details?: Record<string, unknown>,
    requestId?: string,
    retryAfterMs?: number,
    limit?: number,
    remaining?: number,
  ) {
    super(message, code, details, requestId, 429);
    this.name = 'RateLimitError';
    this.retryAfterMs = retryAfterMs;
    this.limit = limit;
    this.remaining = remaining;
    Object.setPrototypeOf(this, RateLimitError.prototype);
  }
}

/**
 * Error thrown for server-side errors (5xx).
 *
 * This indicates a problem on the API server side, not with the
 * request itself.
 */
export class APIError extends LiuHaoError {
  constructor(
    message: string = 'API error occurred',
    code: string = 'API_ERROR',
    details?: Record<string, unknown>,
    requestId?: string,
    statusCode?: number,
  ) {
    super(message, code, details, requestId, statusCode ?? 500);
    this.name = 'APIError';
    Object.setPrototypeOf(this, APIError.prototype);
  }
}

/**
 * Error thrown when a request times out.
 */
export class TimeoutError extends LiuHaoError {
  /** The timeout duration in milliseconds that was exceeded */
  public readonly timeoutMs: number;

  constructor(
    message: string = 'Request timed out',
    timeoutMs: number = 0,
    requestId?: string,
  ) {
    super(message, 'TIMEOUT_ERROR', { timeoutMs }, requestId);
    this.name = 'TimeoutError';
    this.timeoutMs = timeoutMs;
    Object.setPrototypeOf(this, TimeoutError.prototype);
  }
}

/**
 * Error thrown when the request is forbidden (403).
 *
 * The API key is valid but does not have permission to access
 * the requested resource.
 */
export class PermissionDeniedError extends LiuHaoError {
  constructor(
    message: string = 'Permission denied',
    code: string = 'PERMISSION_DENIED',
    details?: Record<string, unknown>,
    requestId?: string,
  ) {
    super(message, code, details, requestId, 403);
    this.name = 'PermissionDeniedError';
    Object.setPrototypeOf(this, PermissionDeniedError.prototype);
  }
}

/**
 * Error thrown when there is a conflict (409).
 *
 * Typically means the resource already exists or the operation
 * conflicts with the current state.
 */
export class ConflictError extends LiuHaoError {
  constructor(
    message: string = 'Resource conflict',
    code: string = 'CONFLICT',
    details?: Record<string, unknown>,
    requestId?: string,
  ) {
    super(message, code, details, requestId, 409);
    this.name = 'ConflictError';
    Object.setPrototypeOf(this, ConflictError.prototype);
  }
}

/**
 * Error thrown when the service is unavailable (503).
 */
export class ServiceUnavailableError extends LiuHaoError {
  /** Number of milliseconds to wait before retrying */
  public readonly retryAfterMs?: number;

  constructor(
    message: string = 'Service unavailable',
    code: string = 'SERVICE_UNAVAILABLE',
    details?: Record<string, unknown>,
    requestId?: string,
    retryAfterMs?: number,
  ) {
    super(message, code, details, requestId, 503);
    this.name = 'ServiceUnavailableError';
    this.retryAfterMs = retryAfterMs;
    Object.setPrototypeOf(this, ServiceUnavailableError.prototype);
  }
}

/**
 * Creates the appropriate error type from an HTTP response status code.
 *
 * This factory function inspects the HTTP status code and response body
 * to construct the most specific error class possible.
 *
 * @param status - HTTP status code
 * @param response - Parsed error response body
 * @returns A LiuHaoError subclass instance
 *
 * @example
 * ```ts
 * catch (error) {
 *   if (axios.isAxiosError(error) && error.response) {
 *     throw fromResponse(error.response.status, error.response.data);
 *   }
 * }
 * ```
 */
export function fromResponse(
  status: number,
  response?: ErrorResponse,
): LiuHaoError {
  const message = response?.message ?? 'An unknown error occurred';
  const code = response?.code ?? 'UNKNOWN_ERROR';
  const details = response?.details as Record<string, unknown> | undefined;
  const requestId = response?.requestId;

  switch (status) {
    case 400:
      return new ValidationError(
        message,
        code,
        details,
        requestId,
        details?.fieldErrors as Record<string, string[]> | undefined,
      );

    case 401:
      return new AuthenticationError(message, code, details, requestId);

    case 403:
      return new PermissionDeniedError(message, code, details, requestId);

    case 404:
      return new NotFoundError(
        message,
        code,
        details,
        requestId,
        details?.resourceType as string | undefined,
        details?.resourceId as string | undefined,
      );

    case 409:
      return new ConflictError(message, code, details, requestId);

    case 429: {
      const retryAfter = details?.retryAfterMs as number | undefined;
      const limit = details?.limit as number | undefined;
      const remaining = details?.remaining as number | undefined;
      return new RateLimitError(
        message,
        code,
        details,
        requestId,
        retryAfter,
        limit,
        remaining,
      );
    }

    case 500:
    case 501:
    case 502:
    case 504:
    case 505:
      return new APIError(message, code, details, requestId, status);

    case 503: {
      const retryAfter = details?.retryAfterMs as number | undefined;
      return new ServiceUnavailableError(
        message,
        code,
        details,
        requestId,
        retryAfter,
      );
    }

    default:
      return new LiuHaoError(message, code, details, requestId, status);
  }
}
