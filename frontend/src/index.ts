/**
 * LiuHao AI OS - 前端组件库主导出
 *
 * 统一导出全部组件、hooks、类型与工具函数。
 */

// ---------------------------------------------------------------------------
// 类型
// ---------------------------------------------------------------------------
export * from './types';

// ---------------------------------------------------------------------------
// 工具函数
// ---------------------------------------------------------------------------
export * from './utils';

// ---------------------------------------------------------------------------
// Hooks
// ---------------------------------------------------------------------------
export * from './hooks';

// ---------------------------------------------------------------------------
// 组件
// ---------------------------------------------------------------------------

// Agent
export {
  AgentCard,
  AgentAvatar,
  AGENT_TYPE_LABELS,
  AGENT_TYPE_COLORS,
  AGENT_STATUS_META,
} from './components/AgentCard';
export type { AgentCardProps } from './components/AgentCard';
export { AgentList } from './components/AgentList';
export type { AgentListProps } from './components/AgentList';

// Task
export { TaskCard, ProgressBar, PRIORITY_META } from './components/TaskCard';
export type { TaskCardProps } from './components/TaskCard';
export { TaskBoard } from './components/TaskBoard';
export type { TaskBoardProps } from './components/TaskBoard';

// Chat
export { ChatWindow } from './components/ChatWindow';
export type {
  ChatWindowProps,
  ChatWindowHandle,
} from './components/ChatWindow';
export { ChatMessage, MarkdownContent } from './components/ChatMessage';
export type { ChatMessageProps } from './components/ChatMessage';

// Memory / Knowledge
export { MemoryPanel, MEMORY_TYPE_META } from './components/MemoryPanel';
export type { MemoryPanelProps } from './components/MemoryPanel';
export { KnowledgeBase } from './components/KnowledgeBase';
export type { KnowledgeBaseProps } from './components/KnowledgeBase';

// Dashboard / Metrics
export { Dashboard } from './components/Dashboard';
export type {
  DashboardProps,
  DashboardWidget,
} from './components/Dashboard';
export { MetricCard, Sparkline } from './components/MetricCard';
export type { MetricCardProps } from './components/MetricCard';
export { CostTracker } from './components/CostTracker';
export type { CostTrackerProps } from './components/CostTracker';
export { HealthStatus, HEALTH_META, computeOverall } from './components/HealthStatus';
export type { HealthStatusProps } from './components/HealthStatus';

// Workflow / Plugin / Settings / Notification
export { WorkflowBuilder, NODE_TYPE_META } from './components/WorkflowBuilder';
export type { WorkflowBuilderProps } from './components/WorkflowBuilder';
export { PluginManager } from './components/PluginManager';
export type { PluginManagerProps } from './components/PluginManager';
export { SettingsPanel } from './components/SettingsPanel';
export type { SettingsPanelProps } from './components/SettingsPanel';
export { NotificationCenter } from './components/NotificationCenter';
export type { NotificationCenterProps } from './components/NotificationCenter';

// Layout
export { SearchBar } from './components/SearchBar';
export type { SearchBarProps } from './components/SearchBar';
export { Sidebar } from './components/Sidebar';
export type { SidebarProps } from './components/Sidebar';
export { Header } from './components/Header';
export type { HeaderProps, HeaderUser } from './components/Header';
export { CodeEditor } from './components/CodeEditor';
export type { CodeEditorProps, CodeLanguage } from './components/CodeEditor';
