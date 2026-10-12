/**
 * LiuHao AI OS SDK Type Definitions
 *
 * This file contains all TypeScript interfaces, types, and enums
 * used throughout the LiuHao AI OS SDK.
 */

// ============================================================
// Enums
// ============================================================

/**
 * Agent lifecycle status.
 */
export enum AgentStatus {
  /** Agent is created but not started */
  CREATED = 'created',
  /** Agent is currently running */
  RUNNING = 'running',
  /** Agent is paused */
  PAUSED = 'paused',
  /** Agent has completed successfully */
  COMPLETED = 'completed',
  /** Agent has failed */
  FAILED = 'failed',
  /** Agent has been cancelled */
  CANCELLED = 'cancelled',
  /** Agent is waiting for input */
  WAITING = 'waiting',
}

/**
 * Memory item category for semantic organization.
 */
export enum MemoryCategory {
  /** General factual knowledge */
  FACT = 'fact',
  /** User preferences and settings */
  PREFERENCE = 'preference',
  /** Conversation or interaction history */
  CONVERSATION = 'conversation',
  /** Task or goal related memory */
  TASK = 'task',
  /** User profile information */
  PROFILE = 'profile',
  /** System configuration */
  CONFIG = 'config',
  /** Custom user-defined category */
  CUSTOM = 'custom',
}

/**
 * Task execution status.
 */
export enum TaskStatus {
  /** Task is queued for execution */
  PENDING = 'pending',
  /** Task is currently running */
  RUNNING = 'running',
  /** Task has completed successfully */
  COMPLETED = 'completed',
  /** Task has failed */
  FAILED = 'failed',
  /** Task has been cancelled */
  CANCELLED = 'cancelled',
  /** Task is waiting for retry */
  RETRYING = 'retrying',
  /** Task is on hold */
  ON_HOLD = 'on_hold',
}

/**
 * Overall system health status.
 */
export enum HealthStatus {
  /** All systems operational */
  HEALTHY = 'healthy',
  /** Some degraded components */
  DEGRADED = 'degraded',
  /** System is unhealthy */
  UNHEALTHY = 'unhealthy',
  /** System status unknown */
  UNKNOWN = 'unknown',
}

/**
 * HTTP method types.
 */
export enum HttpMethod {
  GET = 'GET',
  POST = 'POST',
  PUT = 'PUT',
  PATCH = 'PATCH',
  DELETE = 'DELETE',
}

/**
 * Sort direction for list operations.
 */
export enum SortDirection {
  ASC = 'asc',
  DESC = 'desc',
}

/**
 * Knowledge base type.
 */
export enum KnowledgeBaseType {
  /** Vector-based semantic search */
  VECTOR = 'vector',
  /** Full-text keyword search */
  FULLTEXT = 'fulltext',
  /** Hybrid vector + fulltext search */
  HYBRID = 'hybrid',
  /** Graph-based knowledge */
  GRAPH = 'graph',
}

// ============================================================
// Core Interfaces
// ============================================================

/**
 * Standard paginated response wrapper.
 */
export interface PaginatedResponse<T> {
  /** Array of result items */
  items: T[];
  /** Total number of items available */
  total: number;
  /** Current page number (1-indexed) */
  page: number;
  /** Number of items per page */
  pageSize: number;
  /** Total number of pages */
  totalPages: number;
  /** Whether there is a next page */
  hasNext: boolean;
  /** Whether there is a previous page */
  hasPrev: boolean;
}

/**
 * Pagination parameters for list operations.
 */
export interface PaginationParams {
  /** Page number (1-indexed) */
  page?: number;
  /** Number of items per page */
  pageSize?: number;
}

/**
 * Sort parameters for list operations.
 */
export interface SortParams {
  /** Field to sort by */
  sortBy?: string;
  /** Sort direction */
  sortDir?: SortDirection;
}

/**
 * Standard error response from the API.
 */
export interface ErrorResponse {
  /** Machine-readable error code */
  code: string;
  /** Human-readable error message */
  message: string;
  /** Optional detailed error information */
  details?: Record<string, unknown>;
  /** Request identifier for debugging */
  requestId?: string;
  /** HTTP status code */
  status?: number;
}

// ============================================================
// Agent Types
// ============================================================

/**
 * Agent resource representation.
 */
export interface Agent {
  /** Unique agent identifier */
  id: string;
  /** Agent name */
  name: string;
  /** Agent description */
  description?: string;
  /** Current agent status */
  status: AgentStatus;
  /** Agent model/type identifier */
  model: string;
  /** Agent capabilities list */
  capabilities: string[];
  /** Agent configuration */
  config: Record<string, unknown>;
  /** Agent metadata */
  metadata?: Record<string, string>;
  /** Associated user/owner ID */
  ownerId?: string;
  /** Creation timestamp (ISO 8601) */
  createdAt: string;
  /** Last update timestamp (ISO 8601) */
  updatedAt: string;
  /** Last started timestamp (ISO 8601) */
  startedAt?: string;
  /** Completion timestamp (ISO 8601) */
  completedAt?: string;
}

/**
 * Parameters for creating a new agent.
 */
export interface AgentCreateParams {
  /** Agent name */
  name: string;
  /** Agent description */
  description?: string;
  /** Agent model/type identifier */
  model: string;
  /** Agent capabilities */
  capabilities?: string[];
  /** Agent configuration */
  config?: Record<string, unknown>;
  /** Agent metadata */
  metadata?: Record<string, string>;
  /** Initial system prompt */
  systemPrompt?: string;
  /** Associated knowledge base IDs */
  knowledgeBaseIds?: string[];
}

/**
 * Parameters for updating an existing agent.
 */
export interface AgentUpdateParams {
  /** Agent name */
  name?: string;
  /** Agent description */
  description?: string;
  /** Agent model/type identifier */
  model?: string;
  /** Agent capabilities */
  capabilities?: string[];
  /** Agent configuration */
  config?: Record<string, unknown>;
  /** Agent metadata */
  metadata?: Record<string, string>;
  /** System prompt */
  systemPrompt?: string;
}

/**
 * Parameters for agent execution.
 */
export interface AgentExecuteParams {
  /** Input message/prompt for the agent */
  input: string;
  /** Conversation context */
  context?: Record<string, unknown>;
  /** Additional variables to inject */
  variables?: Record<string, string>;
  /** Whether to stream the response */
  stream?: boolean;
  /** Maximum tokens to generate */
  maxTokens?: number;
  /** Temperature setting */
  temperature?: number;
}

/**
 * Agent execution result.
 */
export interface AgentExecuteResult {
  /** Execution ID */
  executionId: string;
  /** Agent response output */
  output: string;
  /** Execution status */
  status: AgentStatus;
  /** Token usage statistics */
  usage?: TokenUsage;
  /** Execution metadata */
  metadata?: Record<string, unknown>;
}

/**
 * Agent metrics.
 */
export interface AgentMetrics {
  /** Total number of executions */
  totalExecutions: number;
  /** Number of successful executions */
  successfulExecutions: number;
  /** Number of failed executions */
  failedExecutions: number;
  /** Average execution time in milliseconds */
  averageExecutionTimeMs: number;
  /** Total tokens consumed */
  totalTokensConsumed: number;
  /** Current active tasks count */
  activeTasks: number;
  /** Time window for these metrics */
  timeWindow: {
    start: string;
    end: string;
  };
}

/**
 * Token usage statistics.
 */
export interface TokenUsage {
  /** Prompt tokens used */
  promptTokens: number;
  /** Completion tokens used */
  completionTokens: number;
  /** Total tokens used */
  totalTokens: number;
}

/**
 * Agent list filter parameters.
 */
export interface AgentListParams extends PaginationParams, SortParams {
  /** Filter by status */
  status?: AgentStatus;
  /** Filter by model */
  model?: string;
  /** Search by name/description */
  search?: string;
  /** Filter by owner ID */
  ownerId?: string;
}

// ============================================================
// Memory Types
// ============================================================

/**
 * Memory item representation.
 */
export interface MemoryItem {
  /** Unique memory identifier */
  id: string;
  /** Memory content/text */
  content: string;
  /** Memory category */
  category: MemoryCategory;
  /** Memory importance score (0-1) */
  importance: number;
  /** Associated agent/user ID */
  ownerId: string;
  /** Memory tags for filtering */
  tags: string[];
  /** Embedding vector (when returned by search) */
  embedding?: number[];
  /** Similarity score (when returned by search) */
  similarity?: number;
  /** Memory metadata */
  metadata?: Record<string, unknown>;
  /** Creation timestamp (ISO 8601) */
  createdAt: string;
  /** Last update timestamp (ISO 8601) */
  updatedAt: string;
  /** Last accessed timestamp (ISO 8601) */
  lastAccessedAt?: string;
  /** Access count */
  accessCount: number;
}

/**
 * Parameters for storing a memory item.
 */
export interface MemoryStoreParams {
  /** Memory content/text */
  content: string;
  /** Memory category */
  category: MemoryCategory;
  /** Memory importance score (0-1) */
  importance?: number;
  /** Associated agent/user ID */
  ownerId: string;
  /** Memory tags */
  tags?: string[];
  /** Memory metadata */
  metadata?: Record<string, unknown>;
}

/**
 * Parameters for updating a memory item.
 */
export interface MemoryUpdateParams {
  /** Memory content/text */
  content?: string;
  /** Memory category */
  category?: MemoryCategory;
  /** Memory importance score (0-1) */
  importance?: number;
  /** Memory tags */
  tags?: string[];
  /** Memory metadata */
  metadata?: Record<string, unknown>;
}

/**
 * Parameters for searching memory.
 */
export interface MemorySearchParams extends PaginationParams {
  /** Search query text */
  query: string;
  /** Filter by category */
  category?: MemoryCategory;
  /** Filter by tags */
  tags?: string[];
  /** Filter by owner ID */
  ownerId?: string;
  /** Minimum similarity threshold (0-1) */
  minSimilarity?: number;
  /** Maximum number of results */
  limit?: number;
  /** Whether to include embedding in results */
  includeEmbedding?: boolean;
}

/**
 * Parameters for listing memory items.
 */
export interface MemoryListParams extends PaginationParams, SortParams {
  /** Filter by category */
  category?: MemoryCategory;
  /** Filter by tags */
  tags?: string[];
  /** Filter by owner ID */
  ownerId?: string;
  /** Minimum importance score */
  minImportance?: number;
}

// ============================================================
// Knowledge Base Types
// ============================================================

/**
 * Knowledge base representation.
 */
export interface KnowledgeBase {
  /** Unique knowledge base identifier */
  id: string;
  /** Knowledge base name */
  name: string;
  /** Knowledge base description */
  description?: string;
  /** Knowledge base type */
  type: KnowledgeBaseType;
  /** Number of documents in the base */
  documentCount: number;
  /** Total size in bytes */
  sizeBytes: number;
  /** Embedding model used */
  embeddingModel?: string;
  /** Knowledge base configuration */
  config?: Record<string, unknown>;
  /** Knowledge base metadata */
  metadata?: Record<string, string>;
  /** Creation timestamp (ISO 8601) */
  createdAt: string;
  /** Last update timestamp (ISO 8601) */
  updatedAt: string;
}

/**
 * Parameters for creating a knowledge base.
 */
export interface KnowledgeBaseCreateParams {
  /** Knowledge base name */
  name: string;
  /** Knowledge base description */
  description?: string;
  /** Knowledge base type */
  type: KnowledgeBaseType;
  /** Embedding model */
  embeddingModel?: string;
  /** Knowledge base configuration */
  config?: Record<string, unknown>;
  /** Knowledge base metadata */
  metadata?: Record<string, string>;
}

/**
 * Document representation in a knowledge base.
 */
export interface Document {
  /** Unique document identifier */
  id: string;
  /** Knowledge base ID */
  knowledgeBaseId: string;
  /** Document title */
  title: string;
  /** Document content */
  content?: string;
  /** Document source URL/path */
  source?: string;
  /** Document MIME type */
  mimeType?: string;
  /** Document size in bytes */
  sizeBytes?: number;
  /** Number of chunks/segments */
  chunkCount?: number;
  /** Document tags */
  tags: string[];
  /** Document metadata */
  metadata?: Record<string, unknown>;
  /** Creation timestamp (ISO 8601) */
  createdAt: string;
  /** Last update timestamp (ISO 8601) */
  updatedAt: string;
  /** Indexing status */
  indexingStatus: 'pending' | 'processing' | 'completed' | 'failed';
}

/**
 * Parameters for uploading a document.
 */
export interface DocumentUploadParams {
  /** Document title */
  title: string;
  /** Document content (text documents) */
  content?: string;
  /** Document source URL */
  source?: string;
  /** Document tags */
  tags?: string[];
  /** Document metadata */
  metadata?: Record<string, unknown>;
  /** Whether to chunk the document */
  chunk?: boolean;
  /** Chunk size in characters */
  chunkSize?: number;
  /** Chunk overlap in characters */
  chunkOverlap?: number;
}

/**
 * Knowledge search result.
 */
export interface SearchResult {
  /** Matching document chunk/text */
  text: string;
  /** Source document ID */
  documentId: string;
  /** Source document title */
  documentTitle: string;
  /** Similarity/relevance score (0-1) */
  score: number;
  /** Chunk index within the document */
  chunkIndex?: number;
  /** Page number (for PDF documents) */
  pageNumber?: number;
  /** Metadata from the source document */
  metadata?: Record<string, unknown>;
}

/**
 * Parameters for searching a knowledge base.
 */
export interface KnowledgeSearchParams extends PaginationParams {
  /** Search query */
  query: string;
  /** Maximum number of results */
  limit?: number;
  /** Minimum relevance score */
  minScore?: number;
  /** Filter by document IDs */
  documentIds?: string[];
  /** Filter by tags */
  tags?: string[];
  /** Rerank results */
  rerank?: boolean;
}

/**
 * Knowledge collection representation.
 */
export interface KnowledgeCollection {
  /** Unique collection identifier */
  id: string;
  /** Collection name */
  name: string;
  /** Collection description */
  description?: string;
  /** Knowledge base IDs in this collection */
  knowledgeBaseIds: string[];
  /** Creation timestamp (ISO 8601) */
  createdAt: string;
  /** Last update timestamp (ISO 8601) */
  updatedAt: string;
}

/**
 * Parameters for creating a knowledge collection.
 */
export interface KnowledgeCollectionCreateParams {
  /** Collection name */
  name: string;
  /** Collection description */
  description?: string;
  /** Initial knowledge base IDs */
  knowledgeBaseIds?: string[];
}

/**
 * Parameters for listing knowledge bases.
 */
export interface KnowledgeBaseListParams extends PaginationParams, SortParams {
  /** Filter by type */
  type?: KnowledgeBaseType;
  /** Search by name/description */
  search?: string;
}

/**
 * Parameters for listing documents.
 */
export interface DocumentListParams extends PaginationParams, SortParams {
  /** Filter by tags */
  tags?: string[];
  /** Filter by indexing status */
  indexingStatus?: Document['indexingStatus'];
  /** Search by title */
  search?: string;
}

// ============================================================
// Task Types
// ============================================================

/**
 * Task representation.
 */
export interface Task {
  /** Unique task identifier */
  id: string;
  /** Task name/title */
  name: string;
  /** Task description */
  description?: string;
  /** Current task status */
  status: TaskStatus;
  /** Task type */
  type: string;
  /** Associated agent ID */
  agentId?: string;
  /** Task input parameters */
  input: Record<string, unknown>;
  /** Task output/result */
  output?: Record<string, unknown>;
  /** Task priority */
  priority: number;
  /** Number of retries attempted */
  retryCount: number;
  /** Maximum number of retries */
  maxRetries: number;
  /** Error message if failed */
  error?: string;
  /** Task progress percentage (0-100) */
  progress?: number;
  /** Task metadata */
  metadata?: Record<string, string>;
  /** Creation timestamp (ISO 8601) */
  createdAt: string;
  /** Last update timestamp (ISO 8601) */
  updatedAt: string;
  /** Start timestamp (ISO 8601) */
  startedAt?: string;
  /** Completion timestamp (ISO 8601) */
  completedAt?: string;
}

/**
 * Parameters for creating a task.
 */
export interface TaskCreateParams {
  /** Task name/title */
  name: string;
  /** Task description */
  description?: string;
  /** Task type */
  type: string;
  /** Associated agent ID */
  agentId?: string;
  /** Task input parameters */
  input: Record<string, unknown>;
  /** Task priority */
  priority?: number;
  /** Maximum number of retries */
  maxRetries?: number;
  /** Task metadata */
  metadata?: Record<string, string>;
}

/**
 * Parameters for listing tasks.
 */
export interface TaskListParams extends PaginationParams, SortParams {
  /** Filter by status */
  status?: TaskStatus;
  /** Filter by type */
  type?: string;
  /** Filter by agent ID */
  agentId?: string;
  /** Filter by priority */
  priority?: number;
}

/**
 * Task retry parameters.
 */
export interface TaskRetryParams {
  /** Updated input parameters */
  input?: Record<string, unknown>;
  /** Additional retries to add */
  additionalRetries?: number;
}

// ============================================================
// Health Types
// ============================================================

/**
 * Basic health check result.
 */
export interface HealthCheckResult {
  /** Overall health status */
  status: HealthStatus;
  /** Status message */
  message?: string;
  /** Current timestamp (ISO 8601) */
  timestamp: string;
  /** Service version */
  version?: string;
}

/**
 * Detailed health check with component breakdown.
 */
export interface DetailedHealthResult extends HealthCheckResult {
  /** Uptime in seconds */
  uptimeSeconds: number;
  /** Component health statuses */
  components: Record<string, ComponentHealth>;
  /** System resource usage */
  system?: SystemHealth;
}

/**
 * Individual component health status.
 */
export interface ComponentHealth {
  /** Component health status */
  status: HealthStatus;
  /** Component description */
  description?: string;
  /** Status message or error detail */
  message?: string;
  /** Response time in milliseconds */
  responseTimeMs?: number;
  /** Last checked timestamp (ISO 8601) */
  lastChecked: string;
}

/**
 * System resource health information.
 */
export interface SystemHealth {
  /** CPU usage percentage */
  cpuUsagePercent: number;
  /** Memory usage in bytes */
  memoryUsageBytes: number;
  /** Total memory in bytes */
  memoryTotalBytes: number;
  /** Disk usage in bytes */
  diskUsageBytes: number;
  /** Total disk in bytes */
  diskTotalBytes: number;
  /** Active connections count */
  activeConnections: number;
  /** Requests per second */
  requestsPerSecond: number;
}

// ============================================================
// Utility Types
// ============================================================

/**
 * SDK version information.
 */
export interface SDKInfo {
  /** SDK version string */
  version: string;
  /** SDK package name */
  name: string;
}

/**
 * Request options that can override client-level settings.
 */
export interface RequestOptions {
  /** Request timeout in milliseconds */
  timeout?: number;
  /** Number of retry attempts */
  retries?: number;
  /** Additional headers */
  headers?: Record<string, string>;
  /** Abort signal for cancelling requests */
  signal?: AbortSignal;
  /** Whether to validate response data */
  validateResponse?: boolean;
}

/**
 * SDK configuration interface.
 */
export interface LiuHaoConfig {
  /** Base URL for the API */
  baseUrl: string;
  /** API key for authentication */
  apiKey?: string;
  /** Request timeout in milliseconds */
  timeout: number;
  /** Number of retry attempts for failed requests */
  retries: number;
  /** User agent string */
  userAgent: string;
  /** Whether to validate SSL certificates */
  validateSSL: boolean;
  /** Custom headers to include in all requests */
  defaultHeaders?: Record<string, string>;
}
