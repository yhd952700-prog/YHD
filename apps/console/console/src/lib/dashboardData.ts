/**
 * 鎏灏 AI · CEO Command Center 业务看板数据模型
 *
 * ⚠️ 数据来源诚实声明（NO-FAKE）
 * ------------------------------------------------------------------
 * 本文件承载的是「前端业务模型」——即产品视角的经营/销售/市场看板数据，
 * 是界面复刻参考图所需的示例种子值，可交互、可持久化，**不代表后端真实遥测**。
 *
 * 系统遥测（网关就绪/会话数/运行时长/对话）不在此层，它们由 App.tsx
 * 直接通过 `/v1/ready` + `/v1/chat/sessions` 拉取真实数据。
 *
 * 若后续接入真实 CRM/ERP/MIS 数据源，只需替换本文件各 array 为 API 拉取即可。
 */

/** 今日简报任务（参考图：7 条，含优先级）。 */
export interface BriefItem {
  icon: string;
  color: 'red' | 'orange' | 'yellow' | 'cyan';
  text: string;
  priority: '最优优先' | '进行中' | '中优先级';
}

export const TODAYS_BRIEF: BriefItem[] = [
  { icon: '📦', color: 'red', text: '泰国食品包装市场机会显著，需要重点把握', priority: '最优优先' },
  { icon: '🌿', color: 'yellow', text: '该加固某一客户开发，提升跟进率', priority: '进行中' },
  { icon: '📈', color: 'yellow', text: '客户营销已建立 20 家，其中 5 家属于重点商机', priority: '进行中' },
  { icon: '↩️', color: 'orange', text: '供应链资料补充，确保信息完整', priority: '中优先级' },
  { icon: '🔍', color: 'red', text: '该加固结构与 SEO 基础短板，提升流量', priority: '最优优先' },
  { icon: '🗓️', color: 'yellow', text: '可开始策划推新品与更新，保持节奏', priority: '进行中' },
];

/** 核心业务数据（参考图：4 项，含 7/30/90 天筛选）。 */
export interface MetricItem {
  key: string;
  label: string;
  en: string;
  value: string;
  unit: string;
  delta: string;
  dir: 'up' | 'flat';
  /** 7/30/90 天分段值（演示切换）。 */
  seg: { d7: string; d30: string; d90: string };
}

export const KEY_METRICS: MetricItem[] = [
  { key: 'customer', label: '客户', en: 'Customer', value: '20', unit: '家', delta: '+8', dir: 'up', seg: { d7: '12', d30: '20', d90: '35' } },
  { key: 'opportunity', label: '销售目标', en: 'Sales Opportunity', value: '5', unit: '家', delta: '+2', dir: 'up', seg: { d7: '3', d30: '5', d90: '9' } },
  { key: 'pending', label: '待审核', en: 'Pending', value: '2', unit: '家', delta: '-0', dir: 'flat', seg: { d7: '1', d30: '2', d90: '4' } },
  { key: 'complete', label: '任务完成率', en: 'Task Completion', value: '85', unit: '%', delta: '+12%', dir: 'up', seg: { d7: '70', d30: '85', d90: '92' } },
];

/** 销售 Pipeline（参考图：9 列，前 3 列有值）。 */
export interface PipeStage {
  label: string;
  en: string;
  value: number;
  delta?: string;
}

export const PIPELINE: PipeStage[] = [
  { label: 'Prospect', en: '潜在客户', value: 20, delta: '+8' },
  { label: 'Qualified', en: '已符合', value: 5, delta: '+2' },
  { label: '待开发', en: 'To Develop', value: 3, delta: '+1' },
  { label: '已联系', en: 'Contacted', value: 0 },
  { label: '提案', en: 'Proposal', value: 0 },
  { label: '需求确认', en: 'Verified', value: 0 },
  { label: '报价', en: 'Quote', value: 0 },
  { label: '谈判', en: 'Negotiate', value: 0 },
  { label: '成交', en: 'Closed', value: 0 },
];

/** 业务数据中心（参考图：6 项，前置已完成/待补充态）。 */
export interface BizReality {
  key: string;
  label: string;
  status: 'done' | 'pending';
  pct: number;
}

export const BIZ_REALITY: BizReality[] = [
  { key: 'company', label: 'Company', status: 'done', pct: 100 },
  { key: 'product', label: 'Product', status: 'done', pct: 100 },
  { key: 'supplier', label: 'Supplier', status: 'pending', pct: 60 },
  { key: 'market', label: 'Market', status: 'pending', pct: 50 },
  { key: 'customer', label: 'Customer', status: 'pending', pct: 40 },
  { key: 'pipeline', label: 'Pipeline', status: 'pending', pct: 50 },
];

/** 全球市场聚焦（参考图：5 国 + 评分）。 */
export interface MarketItem {
  name: string;
  score: number;
  hot?: boolean;
}

export const MARKET: MarketItem[] = [
  { name: 'Thailand', score: 92, hot: true },
  { name: 'Vietnam', score: 85 },
  { name: 'Malaysia', score: 82 },
  { name: 'Indonesia', score: 80 },
  { name: 'Philippines', score: 78 },
];

/** AI 任务中心（参考图：5 条 + 状态筛选）。 */
export interface TaskItem {
  text: string;
  priority: '最优优先' | '中优先级' | '进行中';
  state: 'all' | 'running' | 'done' | 'archived';
}

export const AI_TASKS: TaskItem[] = [
  { text: '泰国食品包装市场机会分析报告', priority: '最优优先', state: 'running' },
  { text: '产品定位与差异化策略', priority: '中优先级', state: 'running' },
  { text: '全球市场分析报告', priority: '中优先级', state: 'done' },
  { text: '供应链补充资料', priority: '中优先级', state: 'running' },
  { text: '独立站内容规划', priority: '进行中', state: 'archived' },
];

/** 最近动态（参考图：5 条时间线）。 */
export interface ActItem {
  time: string;
  text: string;
  tag: string;
}

export const ACTIVITY: ActItem[] = [
  { time: '10:30', text: 'AI 完成泰国市场分析报告', tag: '市场研究' },
  { time: '15:25', text: '新增 5 条泰国业务信息，客户开发', tag: '客户开发' },
  { time: '15:40', text: '产品定位与差异化方案，市场研究', tag: '市场研究' },
  { time: '14:20', text: '供应链补充资料，市场研究', tag: '市场研究' },
  { time: '10:00', text: 'CEO 制定本周业务目标', tag: 'CEO' },
];

/** 系统健康 / 运行模式（真实遥测由 App 层覆盖 value）。 */
export interface HealthItem {
  label: string;
  state: 'ok' | 'warn';
  liveKey?: 'provider' | 'ready';
}

export const SYSTEM_HEALTH: HealthItem[] = [
  { label: 'Governance', state: 'ok' },
  { label: 'Audit', state: 'ok' },
  { label: 'Business Reality', state: 'ok' },
  { label: 'Agent Runtime', state: 'ok' },
  { label: 'Provider', state: 'ok', liveKey: 'provider' },
  { label: 'Execution Lock', state: 'ok' },
  { label: 'Browser Guard', state: 'ok' },
  { label: 'API 服务', state: 'ok', liveKey: 'ready' },
];

/** 侧边栏导航。 */
export interface NavItem {
  key: string;
  label: string;
  en: string;
  icon: string;
  badge?: string;
}

export const NAV_MAIN: NavItem[] = [
  { key: 'dashboard', label: '首页 · 仪表板', en: 'Dashboard', icon: '🏠' },
  { key: 'jarvis', label: 'Jarvis 智能助手', en: 'JARVIS', icon: '🤖', badge: '?' },
  { key: 'brief', label: 'CEO 简报', en: 'CEO Brief', icon: '📋', badge: 'AI' },
  { key: 'tasks', label: '任务中心', en: 'Mission & Tasks', icon: '🎯' },
  { key: 'sales', label: '客户与销售', en: 'Sales & Pipeline', icon: '📊' },
  { key: 'data', label: '业务数据中心', en: 'Business Reality', icon: '💼' },
  { key: 'market', label: '市场研究', en: 'Market Research', icon: '🌏' },
  { key: 'aihub', label: 'AI 决策中枢', en: 'AI Council', icon: '🧠', badge: 'AIR' },
  { key: 'approval', label: '审批中心', en: 'Approval Gateway', icon: '✅', badge: '1' },
  { key: 'knowledge', label: '知识中心', en: 'Knowledge Center', icon: '📚' },
  { key: 'system', label: '系统状态', en: 'System Health', icon: '🛡️' },
  { key: 'settings', label: '设置', en: 'Settings', icon: '⚙️' },
];
