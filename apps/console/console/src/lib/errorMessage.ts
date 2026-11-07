/**
 * 后端错误文案的**可读化**（微细节：错误必须能被看懂、能被照着做）。
 *
 * 交付面纪律：**英文原文、变量名、`key=value` 机器串一律不上屏**。
 * 服务端可能返回 `{"error":"unknown_kernel","name":"x"}` 这类只有机器字段的
 * 对象，或 `Not Found` 这种英文短语；直接铺给用户既读不懂也无从行动。
 * 这里只保留**含中文的可读句子**，其余交给 `describeFailure` 按状态码给
 * 中性中文兜底；排障所需的 `trace_id` 仍然单独保留。
 */

const HAS_CJK = /[\u4e00-\u9fff]/

/**
 * 连不上本机服务时的统一说法。
 *
 * 用户看到的必须是"哪台服务没运行、现在该做什么"，而不是"浏览器服务"这类指
 * 向错误的措辞：这些卡片背后是板子上的本地网关，不是浏览器。
 *
 * "三个信号"缺一不可：**对象**（鎏灏的本地服务）、**动作**（打开鎏灏 / 保持启动
 * 窗口）、**预期**（几秒后自动重连）。只写"数据不可用"会让用户以为数据坏了，
 * 实际上只是进程没起来。
 */
export const LOCAL_SERVICE_OFFLINE =
  '鎏灏的本地服务没有运行。请打开鎏灏（启动窗口应保持打开）；稍等几秒会自动重连，也可以重启软件后再试。'

/** 同上，但用于"需要用户自己再点一次"的表单/导出场景（没有自动重连）。 */
export const LOCAL_SERVICE_OFFLINE_MANUAL =
  '鎏灏的本地服务没有运行。请打开鎏灏（启动窗口应保持打开）后重试；刚启动的话请等几秒。'

/**
 * 服务活着、但这一次请求没成功。
 *
 * 与 `LOCAL_SERVICE_OFFLINE` 分开，是因为两者该做的事不同：这一条只需要等
 * （自动重连已经在跑），那一条需要用户去把软件打开。若都用同一句话，
 * 用户会去重启一个本来就在运行的服务。
 */
export const LOCAL_SERVICE_UNSTABLE =
  '鎏灏的本地服务在运行，但这次请求没有成功。稍等会自动重连；持续出现请重启软件后再试。'

/** 文案是否属于"本机服务没运行"这一类（卡片标题据此区分"服务没开"与"数据异常"）。 */
export function isLocalServiceOffline(message: string): boolean {
  return message.includes('鎏灏的本地服务没有运行')
}

/**
 * 字段名 → 用户能看懂的称呼。
 *
 * 422 的 `loc` 里是 Pydantic 字段名（`password` / `principal` / `natural_language`）。
 * 这些英文标识符**不能上屏**；没登记的字段退回中性称呼，绝不显示英文名。
 */
const FIELD_LABELS: Record<string, string> = {
  principal: '账号',
  account: '账号',
  username: '账号',
  name: '名称',
  as_name: '新名字',
  password: '密码',
  new_password: '新密码',
  old_password: '当前密码',
  current_password: '当前密码',
  secret: '密码',
  recovery_code: '恢复码',
  passphrase: '口令短语',
  message: '消息内容',
  natural_language: '任务描述',
  instruction: '指令',
  employee_name: '员工姓名',
  agent_id: '执行个体',
  project_id: '项目',
  scope: '执行范围',
  plan_mode: '计划方式',
  role_description: '职责描述',
  agent_count: '员工数量',
  agent_types: '员工类型',
  default_capabilities: '默认能力',
  owner: '所属账号',
  format: '格式',
  session_id: '会话',
  client_request_id: '请求编号',
  model_id: '模型',
  api_key: '密钥',
  target: '授权对象',
  actions: '授权动作',
}

/** Pydantic 校验项（后端已脱敏：敏感字段没有 input / ctx）。 */
type ValidationItem = {
  loc?: unknown
  msg?: unknown
  type?: unknown
  ctx?: unknown
}

function isValidationItem(value: unknown): value is ValidationItem {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false
  const record = value as Record<string, unknown>
  return 'loc' in record || 'msg' in record
}

function fieldLabel(name: string): string {
  return FIELD_LABELS[name.toLowerCase()] ?? ''
}

function numberFromContext(ctx: unknown, key: string): number | null {
  if (!ctx || typeof ctx !== 'object') return null
  const raw = (ctx as Record<string, unknown>)[key]
  const num = Number(raw)
  return Number.isFinite(num) ? num : null
}

/** Pydantic 的英文前缀（`Value error, `）不进入界面。 */
function stripEnglishPrefix(msg: string): string {
  return msg.replace(/^value error,\s*/i, '').trim()
}

/**
 * 把一条 422 校验错误翻成「哪一项 + 什么规则」。
 *
 * 后端只回 `{loc, msg, type}`，敏感字段连 `ctx` 都会去掉（防回显口令），
 * 所以阈值还要能从英文 msg 里取（`at least 8 characters`）。两处都拿不到
 * 时才给中性兜底 —— 不允许出现「操作没有完成」这种什么都说明不了的句子。
 */
function describeValidationItem(item: ValidationItem): string {
  const loc = Array.isArray(item.loc) ? item.loc : []
  const names = loc
    .map((part) => String(part))
    .filter(
      (part) =>
        !/^(body|query|path|json|header|cookie)$/i.test(part) && !/^\d+$/.test(part),
    )
  const subject = fieldLabel(names[names.length - 1] ?? '') || '填写内容'
  const type = typeof item.type === 'string' ? item.type : ''
  const rawMsg = typeof item.msg === 'string' ? stripEnglishPrefix(item.msg) : ''

  // 后端自定义校验器直接给了中文规则（如「密码至少 8 位」）：原样采用。
  if (rawMsg && HAS_CJK.test(rawMsg)) return rawMsg

  if (type === 'missing' || type === 'value_error.missing') {
    return `${subject}不能为空`
  }
  if (type === 'string_too_short' || type === 'too_short') {
    const atLeast = rawMsg.match(/at least (\d+)/i)
    const min =
      numberFromContext(item.ctx, 'min_length') ??
      numberFromContext(item.ctx, 'min_items') ??
      (atLeast ? Number(atLeast[1]) : null)
    return min === null ? `${subject}太短` : `${subject}至少 ${min} 位`
  }
  if (type === 'string_too_long' || type === 'too_long') {
    const atMost = rawMsg.match(/at most (\d+)/i)
    const max =
      numberFromContext(item.ctx, 'max_length') ??
      numberFromContext(item.ctx, 'max_items') ??
      (atMost ? Number(atMost[1]) : null)
    return max === null ? `${subject}太长` : `${subject}不能超过 ${max} 位`
  }
  if (type === 'string_pattern_mismatch') return `${subject}格式不正确`
  if (type === 'enum') return `${subject}的取值不在允许范围内`
  if (
    type === 'int_parsing' ||
    type === 'float_parsing' ||
    type === 'int_type' ||
    type === 'float_type'
  ) {
    return `${subject}必须是数字`
  }
  if (type === 'bool_parsing' || type === 'bool_type') {
    return `${subject}只能填「是」或「否」`
  }
  if (type === 'json_invalid') return '提交的内容格式不正确，请刷新页面后重试'
  if (type === 'extra_forbidden') return `${subject}不需要填写，请去掉后重试`
  return `${subject}填写不符合要求`
}

export function describeDetail(detail: unknown): string {
  if (detail === null || detail === undefined) return ''
  if (typeof detail === 'string') {
    // 英文短语 / 机器串不进入界面文案。
    return HAS_CJK.test(detail) ? detail : ''
  }
  if (typeof detail === 'number' || typeof detail === 'boolean') return ''
  if (Array.isArray(detail)) {
    const parts = detail
      .map((item) =>
        isValidationItem(item) ? describeValidationItem(item) : describeDetail(item),
      )
      .filter(Boolean)
    // 同一条规则命中多个字段时只保留一句，避免「不能为空；不能为空」。
    return Array.from(new Set(parts)).join('；')
  }
  if (typeof detail === 'object') {
    const record = detail as Record<string, unknown>
    // GoalWorkError 的线路格式是 `{ code, message }`：`message` 已经是给人看的
    // 中文说明（例如「员工 X 已停用，无法接受指派」）。只取它，不再把英文键名
    // 拼进人话；`code` 由调用方单独取用，不塞进文案。
    const message = record.message
    if (typeof message === 'string' && message.trim() && HAS_CJK.test(message)) {
      return message.trim()
    }
    return ''
  }
  return ''
}

export interface FailureText {
  message: string
  code: string | null
}

/**
 * 把「已解析的响应体 + 状态」归一成一句话 + 稳定错误码。
 *
 * 为什么需要 `code`：`agent_not_found` 与 `agent_unavailable` 都是非 2xx，
 * 但**该做的事完全不同**（前者重新选人，后者先恢复员工）。只看文案做字符串
 * 匹配会随文案改动就失效；后端已经在 `detail.code` 里给了稳定标识
 * （见 `GoalWorkError`），这里原样取出，交给调用方决定提示与后续动作。
 *
 * `trace_id` 一并附上（回退到响应头 `X-Trace-ID`，跨域部署下也能拿到）。
 * 两者都没有时按状态给一句中性中文兜底（不再回显浏览器的英文状态文本）。
 *
 * fetch 路径走 `readError(response)`；拿不到 `Response` 的调用方（例如
 * 知识库上传用的 XHR）直接用它 —— 复制一份兜底逻辑，下次修复就会分叉。
 */
export function describeFailure(
  body: unknown,
  status: number,
  _statusText: string,
  headerTraceId: string | null,
): FailureText {
  const record = (body && typeof body === 'object' && !Array.isArray(body) ? body : {}) as {
    detail?: unknown
    message?: unknown
    code?: unknown
    trace_id?: unknown
  }
  // 文案来源按真实线序取：服务端结构化错误会把 `message` 摊到顶层（`detail`
  // 同时保留一封等价副本）；422 则把校验数组放在 `detail` 里。
  const detail = Array.isArray(body)
    ? describeDetail(body)
    : describeDetail(record.detail) || describeDetail(record.message)
  const bodyTraceId = typeof record.trace_id === 'string' ? record.trace_id : ''
  const traceId = bodyTraceId || headerTraceId || ''
  const code = pickErrorCode(record)
  // 429 的原始文案带着英文实现细节（`rate limit exceeded`），对用户没有信息量；
  // 统一成一句中性中文，保留下面的追踪 ID 以便排障。
  //
  // 非 JSON 响应（边缘网关的纯文本 502、反向代理的 HTML 错误页）拿不到后端
  // 文案，``statusText`` 是浏览器的英文原文（"Bad Gateway"），同样不能直接
  // 铺给用户。这里按状态给一句"发生了什么 + 怎么办"。
  const message =
    status === 429
      ? '请求过于频繁，请稍后再试'
      : detail || noDetailFallback(status)
  // 追踪 ID 单独一行：它是"去服务端日志里查完整堆栈"的唯一钥匙，必须让
  // 操作者能照抄。配合 Failure 的 `white-space: pre-line` 渲染成两行。
  return {
    message: traceId ? `${message}\n追踪 ID：${traceId}` : message,
    code,
  }
}

/**
 * 稳定错误码：**顶层优先，兼容旧的 `detail.code`**。
 *
 * 服务端已把 `{"code","message"}` 摊平到顶层（`structured_error_body`），
 * 旧端点仍可能只放在 `detail.code` 里。只读其中一处会让 `ApiError.code`
 * 静默变成 null，依赖它分支的界面（项目强制删除、管家派活指引）就永远不触发。
 */
function pickErrorCode(record: {
  detail?: unknown
  code?: unknown
}): string | null {
  if (typeof record.code === 'string' && record.code.trim()) {
    return record.code.trim()
  }
  const nested = record.detail
  if (nested && typeof nested === 'object' && !Array.isArray(nested)) {
    const legacy = (nested as Record<string, unknown>).code
    if (typeof legacy === 'string' && legacy.trim()) return legacy.trim()
  }
  return null
}

/** 响应里没有可读文案时的兜底：中性中文 + 下一步动作。 */
function noDetailFallback(status: number): string {
  if (status === 0) {
    return LOCAL_SERVICE_OFFLINE_MANUAL
  }
  if (status === 403) {
    return '当前账号没有执行这个操作的权限。请换用有权限的账号，或联系安装者。'
  }
  if (status === 404) {
    return '要访问的内容不存在，可能已被删除或移动。请返回上一页后重试。'
  }
  if (status === 422) {
    return '填写的内容有一项不符合要求，请检查后重试。'
  }
  if (status === 502 || status === 503 || status === 504) {
    return '服务暂时不可用，可能是本地服务没启动或正在重启。请稍后重试；持续出现时可导出诊断包。'
  }
  if (status >= 500) {
    return '服务内部出错了，本次操作没有完成。请稍后重试；持续出现时可导出诊断包。'
  }
  return '操作没有完成，请稍后重试。若持续出现，请导出诊断包并附上提示信息。'
}

/** 一次非 2xx `fetch` 响应 → 可读文案 + 稳定错误码。 */
export async function readError(response: Response): Promise<FailureText> {
  let body: unknown = null
  try {
    body = await response.json()
  } catch {
    // 非 JSON 响应（例如边缘网关的纯文本 502）：交给 describeFailure 的状态兜底，
    // 不把浏览器的英文 statusText 铺给用户。
  }
  return describeFailure(
    body,
    response.status,
    response.statusText,
    response.headers.get('X-Trace-ID'),
  )
}

/** 只要面向人的一句话（登录页、审批客户端等只展示文案的调用方）。 */
export async function readErrorMessage(response: Response): Promise<string> {
  return (await readError(response)).message
}
