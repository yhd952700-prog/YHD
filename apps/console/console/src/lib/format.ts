/**
 * 展示层格式化与时间工具。
 *
 * 为什么单独成文件：这些是**非组件**的纯函数/钩子。把它们和组件放在同一个模块里，
 * 会让 Fast Refresh 失效（改了工具函数整个组件文件都要重新挂载，本地状态丢失），
 * 也会让"这个文件到底导出什么"变得含混。组件归 `components/`，工具归这里。
 */

import { useEffect, useState } from 'react'

/** 语义色调。图表、状态点、徽标共用同一套取值，避免每个组件各造一套字符串。 */
export type Tone = 'ok' | 'warn' | 'danger' | 'accent' | 'muted'

/** 时间戳 → 相对时间。超过 7 天显示日期。任何输入都不抛。 */
export function relativeTime(timestamp: number): string {
  if (!Number.isFinite(timestamp) || timestamp <= 0) return '未知时间'
  // `Math.max(0, …)` 让未来时间戳（时钟漂移 / 写入端时钟超前）显示「刚刚」，
  // 而不是「-3 分钟前」—— 后者是一个会被当成数据错误去追查的假信号。
  const seconds = Math.max(0, Date.now() / 1000 - timestamp)
  if (seconds < 60) return '刚刚'
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前`
  if (seconds < 7 * 86400) return `${Math.floor(seconds / 86400)} 天前`
  try {
    return new Date(timestamp * 1000).toLocaleDateString('zh-CN')
  } catch {
    // 极端时间戳下 toLocaleDateString 可能抛（无效 Date）。
    // 展示层抛异常 = 整页白屏，所以这里必须兜住。
    return '未知时间'
  }
}

/**
 * 秒 → 人类可读时长。任何输入都不抛。
 *
 * 缺失与零是两件事：`undefined` 显示「—」（后端没发这个字段），
 * 而真实的 `0` 仍然显示「0 秒」。把它们混成同一个值就是一句静默的谎。
 */
export function humanDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return '—'
  if (seconds < 60) return `${Math.round(seconds)} 秒`
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分 ${Math.round(seconds % 60)} 秒`
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  if (hours < 24) return `${hours} 小时 ${minutes} 分`
  return `${Math.floor(hours / 24)} 天 ${hours % 24} 小时`
}

/**
 * 走动的当前时刻（毫秒）。
 *
 * 为什么需要它：在渲染期间直接调 `Date.now()` 是不纯的 —— 同一个 props 会渲染出
 * 不同结果，React 的纯度检查会拦下来。正确做法是让**时间成为状态**，由定时器从
 * 外部世界推进（这也是 effect 的正当用途：同步一个外部系统）。
 */
export function useNow(intervalMs = 30_000): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), intervalMs)
    return () => window.clearInterval(timer)
  }, [intervalMs])
  return now
}

/** 语义色调 → CSS 变量后缀。`muted` 落在 `--os-text-faint` 上。 */
export function toneVar(tone: Tone): string {
  return tone === 'muted' ? 'text-faint' : tone
}

/**
 * 审计事件类型 → 中文标签。
 *
 * 未知类型原样显示，**绝不返回 undefined**：React 渲染 `undefined` 会静默变成
 * 一个空白单元格 —— 那是一个没人看得出异常的字段缺失，等于把后端的缺口藏起来。
 * 字段真的缺失时显示「未知」，让人知道这里本来应该有东西。
 */
const EVENT_LABELS: Record<string, string> = {
  access_allowed: '访问放行',
  access_denied: '访问拒绝',
  access_check: '访问校验',
  policy_eval: '策略评估',
  human_sovereignty_override: '人工确认',
  kernel_status: '系统状态',
  kernel_implementation: '系统实现',
  phase_gate: '阶段门',
  state_change: '状态变更',
  role_grant: '角色授予',
  role_revoke: '角色撤销',
  permission_check: '权限校验',
  conditional_access: '条件放行',
  deferred_access: '延后处理',
}

export function eventLabel(type: string): string {
  const key = typeof type === 'string' ? type : ''
  if (!key) return '未知'
  const label = EVENT_LABELS[key]
  if (label) return label
  // 后端偶尔会把内部事件名（含命名空间点号）原样透传；展示层不暴露工程命名。
  if (key.includes('.') || key.includes('_internal')) return '系统事件'
  return key
}

/** 审计 outcome → 中文标签。未知原样显示；字段缺失显示「未知」。 */
const OUTCOME_LABELS: Record<string, string> = {
  allow: '通过',
  allowed: '通过',
  deny: '拒绝',
  denied: '拒绝',
  conditional: '有条件',
  defer: '延后',
  granted: '已授权',
  revoked: '已撤销',
  login_success: '登录成功',
  login_denied: '登录失败',
  logout: '登出',
  // 执行结果：后端会直接把 `success` / `error` 这类机器值透传，界面必须翻译，
  // 否则用户会在卡片里看到一行英文内部状态。
  success: '成功',
  ok: '成功',
  completed: '完成',
  error: '失败',
  failure: '失败',
  failed: '失败',
  interrupted: '已中断',
  pending: '等待中',
  running: '进行中',
}

export function outcomeLabel(outcome: string): string {
  const key = typeof outcome === 'string' ? outcome : ''
  if (!key) return '未知'
  return OUTCOME_LABELS[key] ?? key
}

/**
 * 主体标识 → 人能看懂的名字。
 *
 * 审计事件里的 `principal` 可能是 `service.liuhao-internal-service` 这类内部服务
 * 标识。把原字符串直接铺到卡片上，等于把工程命名暴露给用户。这里只做**展示层**
 * 翻译：内部服务统一显示「系统服务」，其余主体原样显示（不能替用户改名）。
 */
export function principalLabel(principal: unknown): string {
  const text = safeText(principal).trim()
  if (!text) return '未知'
  const lower = text.toLowerCase()
  if (
    lower.startsWith('service.') ||
    lower.startsWith('kernel.') ||
    lower.startsWith('system.') ||
    lower.includes('internal-service') ||
    lower.startsWith('mock.')
  ) {
    return '系统服务'
  }
  return text
}

/** 审计动作名（点号命名空间）→ 中文短语。未知片段返回「系统操作」，不露工程名。 */
const ACTION_SEGMENTS: Record<string, string> = {
  account: '账号',
  member: '账号',
  root: '根',
  register: '注册',
  activated: '激活',
  activated_by: '激活',
  created: '新增',
  deleted: '删除',
  updated: '修改',
  state_change: '状态变更',
  access: '访问',
  login: '登录',
  logout: '登出',
  success: '成功',
  denied: '拒绝',
  allowed: '允许',
  read: '读取',
  write: '写入',
  grant: '授权',
  revoke: '撤销',
  policy: '策略',
  eval: '评估',
  human: '人工',
  sovereignty: '确认',
  model: '模型',
  key: '密钥',
}

export function actionLabel(action: unknown): string {
  const text = safeText(action).trim()
  if (!text) return ''
  const direct = EVENT_LABELS[text]
  if (direct) return direct
  if (!text.includes('.')) return text
  const pieces = text
    .split('.')
    .map((part) => ACTION_SEGMENTS[part] ?? '')
    .filter(Boolean)
  // `account.member.created` 逐段翻译会得到「账号 账号 新增」；相邻重复只留一个。
  const deduped = pieces.filter((part, index) => index === 0 || part !== pieces[index - 1])
  return deduped.length > 0 ? deduped.join('') : '系统操作'
}

/**
 * 系统组件的内部名 → 用户能读懂的名字。
 *
 * 就绪探针与运维页会返回 `ai_provider`、`kernel_policy_enforcement` 这类实现名。
 * 页面上原样铺开等于让用户读代码。未知组件统一显示「系统组件」，不猜语义。
 */
const COMPONENT_LABELS: Record<string, string> = {
  api_key_manager: '密钥管理',
  jwt_handler: '登录令牌',
  rbac_manager: '权限校验',
  encryption_manager: '数据加密',
  rate_limiter: '请求限流',
  audit_store: '审计存储',
  audit_checkpoint: '审计检查点',
  audit_checkpointer: '审计检查点',
  executor: '任务执行器',
  executor_fence: '执行围栏',
  ai_provider: '模型服务',
  kernel_policy_enforcement: '策略执行',
  schema_registry: '结构注册表',
  plugin_registry: '插件注册表',
  scheduler: '任务调度',
  knowledge_store: '知识库存储',
  memory_store: '记忆存储',
}

export function componentLabel(name: unknown): string {
  const text = safeText(name).trim()
  if (!text) return '系统组件'
  return COMPONENT_LABELS[text] ?? '系统组件'
}

/** 后端透传的插件错误（英文实现细节）→ 中性可读原因。 */
export function friendlyPluginError(message: unknown): string {
  const text = safeText(message).trim()
  if (!text) return ''
  if (text.includes('module_path') || /^No module/i.test(text)) {
    return '该插件缺少可加载的模块入口，暂不可用'
  }
  if (/[A-Za-z]{6,}/.test(text) && !/[\u4e00-\u9fa5]/.test(text)) {
    return '插件加载失败，暂不可用'
  }
  return text
}

/** 插件能力标识（后端 snake_case）→ 人话。未知英文标识不硬译，归入「其他能力」。 */
const CAPABILITY_LABELS: Record<string, string> = {
  create_identity: '创建身份',
  grant_permission: '授予权限',
  audit_trail: '审计记录',
  store: '存储',
  recall: '召回',
  scope_filter: '范围过滤',
  'multi-tier': '多层记忆',
  ttl: '过期清理',
  planner: '规划',
  executor: '执行',
  researcher: '调研',
  research: '调研',
  writer: '写作',
  doc: '文档写作',
  python_compute: '本地计算',
  file_write: '写本地文件',
  network_bus: '联网调研',
  execution_pipeline: '综合整理',
  reviewer: '审阅',
  analyst: '分析',
  coder: '编码',
}

export function capabilityLabel(name: unknown): string {
  const text = safeText(name).trim()
  if (!text) return ''
  const known = CAPABILITY_LABELS[text]
  if (known) return known
  return /[\u4e00-\u9fa5]/.test(text) ? text : '其他能力'
}

/** 权限串 → 人话。未知英文权限不原样铺给用户，归入「其他已授予范围」。 */
const PERMISSION_LABELS: Record<string, string> = {
  'account:manage_children': '创建与维护账号',
  'account:grant': '签发放行',
  'account:read': '查看账号',
  'account:write': '修改账号',
  'memory:read': '读取记忆',
  'memory:write': '写入记忆',
}

export function permissionLabel(value: unknown): string {
  const text = safeText(value).trim()
  if (!text) return ''
  const known = PERMISSION_LABELS[text]
  if (known) return known
  // 带冒号的原始权限串与纯英文标识都属于实现层写法，不直接展示。
  if (text.includes(':') || /^[A-Za-z_]+$/.test(text)) return '其他已授予范围'
  return text
}

export function outcomeTone(outcome: string): Tone {
  if (outcome === 'allow' || outcome === 'allowed' || outcome === 'granted') return 'ok'
  if (outcome === 'deny' || outcome === 'denied' || outcome === 'login_denied') return 'danger'
  if (outcome === 'defer' || outcome === 'conditional') return 'warn'
  if (outcome === 'login_success') return 'accent'
  if (outcome === 'success' || outcome === 'ok' || outcome === 'completed') return 'ok'
  if (outcome === 'error' || outcome === 'failure' || outcome === 'failed') return 'danger'
  return 'muted'
}

/**
 * 把任意值安全地转成字符串，用于展示或搜索匹配。
 *
 * 后端契约有时**撒谎**：`ActivityItem.principal` 在 `contracts.ts` 里声明为 `string`，
 * 但后端 `e.get("principal_id")` 实际可能返回 `null`。这种「类型说非空、运行时却为空」
 * 的落差会让 `value.toLowerCase()` 在 null 上直接抛 `TypeError`、整页白屏。
 *
 * `safeText` 是应对这种落差的总函数：null / undefined / 非字符串数字一律返回 `''`，
 * 字符串原样返回，数字 / 布尔转成字符串。调用方再 `.toLowerCase()` 就绝不会崩。
 * 缺失值返回空串（而非 `'—'`），因为这里是「参与搜索/拼接」的语境，
 * 显示层缺失才用 `'—'`（见各页面渲染处）。
 */
export function safeText(value: unknown): string {
  if (value === null || value === undefined) return ''
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  return ''
}

/**
 * 把任意值安全地转成数组。
 *
 * 同为契约落差兜底：`AppSummary.capabilities` 在契约里声明为 `string[]`，
 * 但后端可能不发这个字段（undefined）。`[...app.capabilities]` 在 undefined 上会抛
 * 「undefined is not iterable」，`app.capabilities.length` 会抛「Cannot read properties
 * of undefined」，两者都会白屏。`safeArray` 是非数组一律返回 `[]` 的总函数，
 * 让「缺少列表」降级成「空列表」而不是崩溃。
 */
export function safeArray<T = unknown>(value: unknown): T[] {
  return Array.isArray(value) ? (value as T[]) : []
}
