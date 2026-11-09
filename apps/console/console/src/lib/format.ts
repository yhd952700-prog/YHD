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
 * 数值 → 展示文本。任何输入都不抛。
 *
 * 这是展示层**唯一**该直接调用 `toFixed` 的地方：后端可能不发某个数值字段
 * （`undefined`），或在降级响应里给 `null`。直接 `value.toFixed()` 会抛
 * `TypeError: Cannot read properties of undefined (reading "toFixed")`，
 * 后果是整页白屏。非有限值统一显示「—」；真实的 `0` 仍然显示 `0`。
 */
export function numberText(value: unknown, digits = 0): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
  return value.toFixed(digits)
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
  // `allow` 与 `allowed` 是两种真实存在的结果值（同一批审计里会同时出现）。
  // 都翻成「通过」会让图表/表格出现两段同名结果，用户无法区分，必须各自命名。
  allow: '放行',
  allowed: '已授权',
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
  create: '新增',
  deleted: '删除',
  updated: '修改',
  changed: '变更',
  set_default: '设为默认',
  manage: '管理',
  owner: '归属',
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
  ai: '模型',
  key: '密钥',
  employee: '员工',
  knowledge: '知识库',
  document: '文档',
  backup: '备份',
  export: '导出',
  import: '导入',
  assigned: '派活',
  assign_task: '派活',
  hired: '新增',
  removed: '移除',
  adopted: '取用',
  permission: '权限',
  granted: '授权',
  revoked: '撤销',
}

export function actionLabel(action: unknown): string {
  const text = safeText(action).trim()
  if (!text) return ''
  const direct = EVENT_LABELS[text]
  if (direct) return direct
  // 后端也见过用空格分隔的动作名（例如 `access allowed`）；点号、下划线、空格
  // 统一当分隔符，否则整串查不到映射，用户只能看到「系统操作」。
  const segments = text.split(/[._\s]+/).filter(Boolean)
  // 单个片段（如 assign_task 拆出来的 assigned）直接查表，绝不把 snake_case 露出。
  if (segments.length <= 1) {
    return ACTION_SEGMENTS[text] ?? ACTION_SEGMENTS[segments[0] ?? ''] ?? '系统操作'
  }
  const pieces = segments
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
  'capability.inspect': '能力清单',
  heartbeat: '心跳检查',
}

export function capabilityLabel(name: unknown): string {
  const text = safeText(name).trim()
  if (!text) return ''
  const known = CAPABILITY_LABELS[text]
  if (known) return known
  return /[\u4e00-\u9fa5]/.test(text) ? text : '其他能力'
}

/**
 * 关键词拆解器产出的任务名是内部英文（Plan / Search / WriteFile…）。
 * 这些名字可以直接出现在用户界面上，必须翻成人话；未知英文不再原样展示。
 */
const TASK_NAME_LABELS: Record<string, string> = {
  Search: '检索资料',
  Plan: '整理成稿',
  'Memory Store': '记入记忆',
  Compute: '执行计算',
  WriteFile: '写入文件',
  Execute: '执行目标',
  WriteChecksum: '写入校验文件',
}

const TASK_DESCRIPTION_LABELS: Record<string, string> = {
  'Search for information': '检索完成任务所需的信息',
  'Organize upstream findings into the final deliverable': '把上游结果整理成最终交付',
  'Store in memory': '保存到记忆',
  'Run a local computation': '执行本地计算',
  'Write a file inside the workspace': '在工作区写入文件',
  'Execute goal': '执行目标',
  'Write an integrity sidecar (sha256) for the produced file':
    '为产出文件生成完整性校验',
}

/** 任务名 → 人话。中文自定义名原样保留；未知英文归入「未命名任务」。 */
export function taskNameLabel(name: unknown): string {
  const text = safeText(name).trim()
  if (!text) return '未命名任务'
  const known = TASK_NAME_LABELS[text]
  if (known) return known
  return /[\u4e00-\u9fa5]/.test(text) ? text : '未命名任务'
}

/** 任务描述 → 人话。只翻译已知的内部英文描述，其余原样返回。 */
export function taskDescriptionLabel(description: unknown): string {
  const text = safeText(description).trim()
  if (!text) return ''
  return TASK_DESCRIPTION_LABELS[text] ?? text
}

/** 权限串 → 人话。未知英文权限不原样铺给用户，归入「未命名权限」，原串只进 title。 */
const PERMISSION_LABELS: Record<string, string> = {
  // 服务端真实权限串（见 src/kernels/identity/_accounts.py 的 SCOPE_* 常量）。
  'account:read': '查看账号',
  'account:manage_children': '创建与维护账号',
  'account:grant': '调整成员权限',
  'account:audit_read': '查看审计记录',
  'account:usage_read': '查看用量',
  'account:key_borrow': '借用成员密钥',
  'account:write': '修改账号',
  'model:manage': '配置模型',
  'model:use': '使用模型',
  'knowledge:read': '读取知识库',
  'knowledge:write': '写入知识库',
  'memory:read': '读取记忆',
  'memory:write': '写入记忆',
  // 授权表的「对应动作 / 对应资源」是按冒号拆出来的裸串，这里补齐，避免派生列掉回
  // 「未命名权限」。
  account: '账号',
  model: '模型',
  knowledge: '知识库',
  memory: '记忆',
  manage_children: '创建与维护账号',
  audit_read: '审计记录',
  usage_read: '用量',
  key_borrow: '成员密钥借用',
  grant: '调整成员权限',
  read: '读取',
  write: '写入',
  manage: '配置',
  use: '使用',
}

export function permissionLabel(value: unknown): string {
  const text = safeText(value).trim()
  if (!text) return ''
  const known = PERMISSION_LABELS[text]
  if (known) return known
  // 带冒号的原始权限串与纯英文标识都属于实现层写法，不直接展示。
  if (text.includes(':') || /^[A-Za-z_]+$/.test(text)) return '未命名权限'
  return text
}

/** title 提示用：保留原始机器串，空值不给 title。 */
export function permissionTitle(value: unknown): string | undefined {
  const text = safeText(value).trim()
  return text || undefined
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
