/**
 * LiuHao AI OS - 全局类型定义
 *
 * 覆盖 Agent / Task / Chat / Memory / Knowledge / Workflow / Plugin /
 * Notification / Metrics 等核心领域模型。
 */

// ---------------------------------------------------------------------------
// 通用
// ---------------------------------------------------------------------------

export type ID = string;

export type Timestamp = string; // ISO-8601

export interface EntityBase {
  id: ID;
  createdAt: Timestamp;
  updatedAt?: Timestamp;
}

export type AsyncState<T> =
  | { status: 'idle' | 'loading' }
  | { status: 'success'; data: T }
  | { status: 'error'; error: Error };

export interface Paginated<T> {
  items: T[];
  total: number;
  page: number;
  pageSize: number;
}

export type SortDirection = 'asc' | 'desc';

// ---------------------------------------------------------------------------
// Agent
// ---------------------------------------------------------------------------

export type AgentType = 'planner' | 'executor' | 'reviewer' | 'researcher' | 'custom';

export type AgentStatus = 'online' | 'offline' | 'busy' | 'error' | 'idle';

export interface AgentCapability {
  name: string;
  description?: string;
  version?: string;
}

export interface AgentMetrics {
  tasksCompleted: number;
  tasksFailed: number;
  avgLatencyMs: number;
  totalTokens: number;
  totalCostUsd: number;
  successRate: number; // 0 - 1
}

export interface Agent extends EntityBase {
  name: string;
  description?: string;
  type: AgentType;
  status: AgentStatus;
  avatarUrl?: string;
  model?: string;
  provider?: string;
  capabilities: AgentCapability[];
  tags?: string[];
  currentTaskId?: ID;
  metrics?: AgentMetrics;
  lastActiveAt?: Timestamp;
}

export interface AgentFilter {
  search?: string;
  types?: AgentType[];
  statuses?: AgentStatus[];
  tags?: string[];
}

// ---------------------------------------------------------------------------
// Task
// ---------------------------------------------------------------------------

export type TaskStatus = 'backlog' | 'todo' | 'in_progress' | 'review' | 'done' | 'blocked';

export type TaskPriority = 'critical' | 'high' | 'medium' | 'low';

export interface TaskStep {
  id: ID;
  title: string;
  completed: boolean;
}

export interface Task extends EntityBase {
  title: string;
  description?: string;
  status: TaskStatus;
  priority: TaskPriority;
  progress: number; // 0 - 100
  assigneeId?: ID;
  assigneeName?: string;
  agentId?: ID;
  steps?: TaskStep[];
  dueDate?: Timestamp;
  startedAt?: Timestamp;
  completedAt?: Timestamp;
  estimatedTokens?: number;
  actualTokens?: number;
  labels?: string[];
  parentId?: ID;
}

export interface TaskColumn {
  id: TaskStatus;
  title: string;
  tasks: Task[];
  wipLimit?: number;
}

export interface TaskFilter {
  search?: string;
  statuses?: TaskStatus[];
  priorities?: TaskPriority[];
  assigneeId?: ID;
  agentId?: ID;
  labels?: string[];
}

// ---------------------------------------------------------------------------
// Chat
// ---------------------------------------------------------------------------

export type MessageRole = 'user' | 'assistant' | 'system' | 'tool';

export type MessageStatus = 'sending' | 'sent' | 'streaming' | 'error';

export interface MessageAttachment {
  id: ID;
  name: string;
  mimeType: string;
  sizeBytes: number;
  url?: string;
}

export interface ToolCall {
  id: ID;
  name: string;
  arguments: Record<string, unknown>;
  result?: unknown;
  status: 'pending' | 'running' | 'success' | 'error';
  durationMs?: number;
}

export interface ChatMessage {
  id: ID;
  conversationId: ID;
  role: MessageRole;
  content: string;
  status?: MessageStatus;
  timestamp: Timestamp;
  agentId?: ID;
  agentName?: string;
  agentAvatarUrl?: string;
  attachments?: MessageAttachment[];
  toolCalls?: ToolCall[];
  tokenCount?: number;
  error?: string;
}

export interface Conversation extends EntityBase {
  title: string;
  agentId?: ID;
  messageCount: number;
  lastMessageAt?: Timestamp;
  pinned?: boolean;
  archived?: boolean;
}

// ---------------------------------------------------------------------------
// Memory
// ---------------------------------------------------------------------------

export type MemoryType = 'episodic' | 'semantic' | 'procedural' | 'working';

export interface MemoryNode {
  id: ID;
  label: string;
  type: MemoryType | 'category';
  content?: string;
  importance?: number; // 0 - 1
  accessCount?: number;
  lastAccessedAt?: Timestamp;
  children?: MemoryNode[];
  metadata?: Record<string, unknown>;
}

export interface MemoryEntry extends EntityBase {
  type: MemoryType;
  content: string;
  embedding?: number[];
  importance: number;
  source?: string;
  agentId?: ID;
  tags?: string[];
  expiresAt?: Timestamp;
}

export interface MemoryStats {
  totalEntries: number;
  byType: Record<MemoryType, number>;
  storageBytes: number;
  avgImportance: number;
}

// ---------------------------------------------------------------------------
// Knowledge
// ---------------------------------------------------------------------------

export type KnowledgeDocStatus = 'uploading' | 'processing' | 'ready' | 'failed';

export interface KnowledgeDocument extends EntityBase {
  name: string;
  mimeType: string;
  sizeBytes: number;
  status: KnowledgeDocStatus;
  chunkCount?: number;
  collectionId: ID;
  progress?: number; // 0 - 100, 处理进度
  errorMessage?: string;
  checksum?: string;
}

export interface KnowledgeCollection extends EntityBase {
  name: string;
  description?: string;
  documentCount: number;
  totalSizeBytes: number;
  embeddingModel?: string;
}

export interface KnowledgeSearchResult {
  documentId: ID;
  documentName: string;
  chunk: string;
  score: number;
  position?: number;
}

// ---------------------------------------------------------------------------
// Workflow
// ---------------------------------------------------------------------------

export type WorkflowNodeType =
  | 'trigger'
  | 'agent'
  | 'condition'
  | 'transform'
  | 'tool'
  | 'output';

export interface WorkflowNode {
  id: ID;
  type: WorkflowNodeType;
  label: string;
  position: { x: number; y: number };
  config?: Record<string, unknown>;
  agentId?: ID;
}

export interface WorkflowEdge {
  id: ID;
  source: ID;
  target: ID;
  label?: string;
  condition?: string;
}

export interface Workflow extends EntityBase {
  name: string;
  description?: string;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  enabled: boolean;
  version: number;
  runCount?: number;
  lastRunAt?: Timestamp;
}

export interface WorkflowRun {
  id: ID;
  workflowId: ID;
  status: 'running' | 'success' | 'failed' | 'cancelled';
  startedAt: Timestamp;
  finishedAt?: Timestamp;
  currentNodeId?: ID;
  error?: string;
}

// ---------------------------------------------------------------------------
// Plugin
// ---------------------------------------------------------------------------

export type PluginStatus = 'installed' | 'enabled' | 'disabled' | 'error' | 'update_available';

export interface PluginManifest {
  name: string;
  version: string;
  author?: string;
  description?: string;
  homepage?: string;
  permissions?: string[];
}

export interface Plugin {
  id: ID;
  manifest: PluginManifest;
  status: PluginStatus;
  installedAt: Timestamp;
  updatedAt?: Timestamp;
  config?: Record<string, unknown>;
  errorMessage?: string;
  rating?: number;
  downloads?: number;
}

// ---------------------------------------------------------------------------
// Notification
// ---------------------------------------------------------------------------

export type NotificationLevel = 'info' | 'success' | 'warning' | 'error';

export interface AppNotification {
  id: ID;
  level: NotificationLevel;
  title: string;
  message?: string;
  timestamp: Timestamp;
  read: boolean;
  source?: string;
  actionUrl?: string;
  actionLabel?: string;
}

// ---------------------------------------------------------------------------
// Metrics / Cost / Health
// ---------------------------------------------------------------------------

export interface MetricTrendPoint {
  timestamp: Timestamp;
  value: number;
}

export interface Metric {
  id: string;
  label: string;
  value: number;
  unit?: string;
  format?: 'number' | 'currency' | 'percent' | 'duration' | 'bytes';
  trend?: MetricTrendPoint[];
  deltaPercent?: number; // 相对上一周期的变化
  target?: number;
}

export interface CostEntry {
  timestamp: Timestamp;
  provider: string;
  model: string;
  inputTokens: number;
  outputTokens: number;
  costUsd: number;
  agentId?: ID;
}

export interface CostBreakdown {
  period: string;
  totalUsd: number;
  byModel: Record<string, number>;
  byProvider: Record<string, number>;
  byAgent: Record<string, number>;
  entries: CostEntry[];
}

export type HealthLevel = 'healthy' | 'degraded' | 'unhealthy' | 'unknown';

export interface ServiceHealth {
  id: string;
  name: string;
  level: HealthLevel;
  latencyMs?: number;
  uptime?: number; // 0 - 1
  message?: string;
  lastCheckedAt: Timestamp;
}

export interface SystemHealth {
  overall: HealthLevel;
  services: ServiceHealth[];
  checkedAt: Timestamp;
}

// ---------------------------------------------------------------------------
// Settings
// ---------------------------------------------------------------------------

export type SettingValueType = 'string' | 'number' | 'boolean' | 'select' | 'secret';

export interface SettingField {
  key: string;
  label: string;
  description?: string;
  type: SettingValueType;
  value: unknown;
  defaultValue?: unknown;
  options?: { label: string; value: string }[];
  min?: number;
  max?: number;
  step?: number;
  placeholder?: string;
  required?: boolean;
  sensitive?: boolean;
}

export interface SettingSection {
  id: string;
  title: string;
  description?: string;
  icon?: string;
  fields: SettingField[];
}

// ---------------------------------------------------------------------------
// WebSocket
// ---------------------------------------------------------------------------

export type WsConnectionState = 'connecting' | 'open' | 'closing' | 'closed' | 'error';

export interface WsMessage<T = unknown> {
  type: string;
  payload: T;
  timestamp: Timestamp;
  id?: string;
}

export interface WsSendOptions {
  retry?: boolean;
  maxRetries?: number;
}

// ---------------------------------------------------------------------------
// Search
// ---------------------------------------------------------------------------

export type SearchResultType =
  | 'agent'
  | 'task'
  | 'conversation'
  | 'memory'
  | 'document'
  | 'plugin'
  | 'setting'
  | 'action';

export interface SearchResult {
  id: ID;
  type: SearchResultType;
  title: string;
  subtitle?: string;
  icon?: string;
  score?: number;
  payload?: unknown;
}

// ---------------------------------------------------------------------------
// Navigation
// ---------------------------------------------------------------------------

export interface NavItem {
  id: string;
  label: string;
  icon?: string;
  path?: string;
  badge?: string | number;
  children?: NavItem[];
  disabled?: boolean;
}
