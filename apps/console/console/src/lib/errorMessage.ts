/**
 * 后端错误文案的**可读化**（微细节：错误必须能被看懂、能被照着做）。
 *
 * 两个真实缺陷驱动这个模块：
 * 1. 非字符串 `detail`：gateway 的 kernels.py 端点 404/409 返回的是对象
 *    （`{error: "unknown_kernel", name: "x" }`）。旧逻辑 `JSON.stringify` 之后
 *    界面上就是一坨 `{"error":"unknown_kernel","name":"x"}` —— 看得懂但读不快，
 *    键名是英文、顺序随机。这里压成 `error：unknown_kernel；name：x`。
 * 2. `trace_id` 被丢掉：500 响应带着 trace_id（服务端日志里检索完整堆栈的
 *    唯一钥匙），旧逻辑只取 `detail`，于是这条排障链在界面上断掉。
 */

export function describeDetail(detail: unknown): string {
  if (detail === null || detail === undefined) return ''
  if (typeof detail === 'string') return detail
  if (typeof detail === 'number' || typeof detail === 'boolean') return String(detail)
  if (Array.isArray(detail)) {
    return detail.map(describeDetail).filter(Boolean).join('；')
  }
  if (typeof detail === 'object') {
    const record = detail as Record<string, unknown>
    // GoalWorkError 的线路格式是 `{ code, message }`：`message` 已经是给人看的
    // 中文说明（例如「员工 X 已停用，无法接受指派」）。优先整句展示它，只把
    // 其它有信息量的字段附在后面；`code` 由调用方单独取用，不塞进人话里。
    const message = record.message
    if (typeof message === 'string' && message.trim()) {
      const extras = Object.entries(record)
        .filter(([key]) => key !== 'message' && key !== 'code')
        .filter(([, value]) => value !== null && value !== undefined && value !== '')
        .map(([key, value]) => `${key}：${describeDetail(value)}`)
      return extras.length > 0 ? `${message}；${extras.join('；')}` : message
    }
    return Object.entries(record)
      .filter(([, value]) => value !== null && value !== undefined && value !== '')
      .map(([key, value]) => `${key}：${describeDetail(value)}`)
      .join('；')
  }
  return String(detail)
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
 * 两者都没有时退回 HTTP 状态文本。
 *
 * fetch 路径走 `readError(response)`；拿不到 `Response` 的调用方（例如
 * 知识库上传用的 XHR）直接用它 —— 复制一份兜底逻辑，下次修复就会分叉。
 */
export function describeFailure(
  body: unknown,
  status: number,
  statusText: string,
  headerTraceId: string | null,
): FailureText {
  const record = (body && typeof body === 'object' ? body : {}) as {
    detail?: unknown
    trace_id?: unknown
  }
  const detail = describeDetail(record.detail)
  const bodyTraceId = typeof record.trace_id === 'string' ? record.trace_id : ''
  const traceId = bodyTraceId || headerTraceId || ''
  let code: string | null = null
  if (record.detail && typeof record.detail === 'object' && !Array.isArray(record.detail)) {
    const raw = (record.detail as Record<string, unknown>).code
    if (typeof raw === 'string' && raw.trim()) code = raw.trim()
  }
  // 429 的原始文案带着英文实现细节（`rate limit exceeded`），对用户没有信息量；
  // 统一成一句中性中文，保留下面的追踪 ID 以便排障。
  const message =
    status === 429 ? '请求过于频繁，请稍后再试' : detail || statusText || `HTTP ${status}`
  // 追踪 ID 单独一行：它是"去服务端日志里查完整堆栈"的唯一钥匙，必须让
  // 操作者能照抄。配合 Failure 的 `white-space: pre-line` 渲染成两行。
  return {
    message: traceId ? `${message}\n追踪 ID：${traceId}` : message,
    code,
  }
}

/** 一次非 2xx `fetch` 响应 → 可读文案 + 稳定错误码。 */
export async function readError(response: Response): Promise<FailureText> {
  let body: unknown = null
  try {
    body = await response.json()
  } catch {
    // 非 JSON 响应（例如边缘网关的纯文本 502）：退回状态文本。
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
