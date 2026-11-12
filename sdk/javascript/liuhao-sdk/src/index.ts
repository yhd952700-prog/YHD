/**
 * LiuHao AI OS JavaScript SDK
 *
 * Main entry point for the LiuHao AI OS SDK. Exports the
 * LiuHaoClient class, all resource classes, types, enums,
 * and error classes.
 *
 * @example
 * ```ts
 * import { LiuHaoClient, AgentStatus, MemoryCategory } from '@liuhao/sdk';
 *
 * const client = new LiuHaoClient({
 *   baseUrl: 'https://api.liuhao.ai',
 *   apiKey: 'your-api-key',
 * });
 *
 * const agents = await client.agents.list();
 * ```
 *
 * @module @liuhao/sdk
 */

// --------------------------------------------------------
// SDK Version
// --------------------------------------------------------

/**
 * Current SDK version.
 */
export const SDK_VERSION = '1.0.0';

/**
 * SDK package name.
 */
export const SDK_NAME = '@liuhao/sdk';

// --------------------------------------------------------
// Main Client
// --------------------------------------------------------

export { LiuHaoClient } from './client';

// --------------------------------------------------------
// Resources
// --------------------------------------------------------

export {
  AgentsResource,
  MemoryResource,
  KnowledgeResource,
  TasksResource,
  HealthResource,
} from './resources';

// --------------------------------------------------------
// Types & Enums
// --------------------------------------------------------

export {
  // Enums
  AgentStatus,
  MemoryCategory,
  TaskStatus,
  HealthStatus,
  HttpMethod,
  SortDirection,
  KnowledgeBaseType,

  // Core interfaces
  type PaginatedResponse,
  type PaginationParams,
  type SortParams,
  type ErrorResponse,
  type RequestOptions,
  type LiuHaoConfig,
  type SDKInfo,

  // Agent types
  type Agent,
  type AgentCreateParams,
  type AgentUpdateParams,
  type AgentExecuteParams,
  type AgentExecuteResult,
  type AgentMetrics,
  type AgentListParams,
  type TokenUsage,

  // Memory types
  type MemoryItem,
  type MemoryStoreParams,
  type MemoryUpdateParams,
  type MemorySearchParams,
  type MemoryListParams,

  // Knowledge types
  type KnowledgeBase,
  type KnowledgeBaseCreateParams,
  type KnowledgeBaseListParams,
  type Document,
  type DocumentUploadParams,
  type DocumentListParams,
  type SearchResult,
  type KnowledgeSearchParams,
  type KnowledgeCollection,
  type KnowledgeCollectionCreateParams,

  // Task types
  type Task,
  type TaskCreateParams,
  type TaskListParams,
  type TaskRetryParams,

  // Health types
  type HealthCheckResult,
  type DetailedHealthResult,
  type ComponentHealth,
  type SystemHealth,
} from './types';

// --------------------------------------------------------
// Errors
// --------------------------------------------------------

export {
  LiuHaoError,
  AuthenticationError,
  NotFoundError,
  ValidationError,
  RateLimitError,
  APIError,
  TimeoutError,
  PermissionDeniedError,
  ConflictError,
  ServiceUnavailableError,
  fromResponse,
} from './errors';

// --------------------------------------------------------
// Config
// --------------------------------------------------------

export {
  DEFAULT_CONFIG,
  ENV_VARS,
  resolveConfig,
  validateConfig,
  mergeConfig,
  getEnvConfig,
} from './config';

export type { LiuHaoConfigInput } from './config';
