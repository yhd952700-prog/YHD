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
    return Object.entries(detail as Record<string, unknown>)
      .filter(([, value]) => value !== null && value !== undefined && value !== '')
      .map(([key, value]) => `${key}：${describeDetail(value)}`)
      .join('；')
  }
  return String(detail)
}

/**
 * 把一次非 2xx 响应解成「给人看的一句话 + 给程序判断的稳定错误码」。
 *
 * 为什么需要 `code`：`agent_not_found` 与 `agent_unavailable` 都是非 2xx，
 * 但**该做的事完全不同**（前者重新选人，后者先恢复员工）。只看文案做字符串
 * 匹配会随文案改动就失效；后端已经在 `detail.code` 里给了稳定标识
 * （见 `GoalWorkError`），这里原样取出，交给调用方决定提示与后续动作。
 *
 * `trace_id` 一并附上（回退到响应头 `X-Trace-ID`，跨域部署下也能拿到）。
 * 两者都没有时退回 HTTP 状态文本。
 */
export async function readErrorParts(
  response: Response,
): Promise<{ message: string; code: string | null }> {
  let detail = ''
  let traceId = ''
  let code: string | null = null
  try {
    const body = (await response.json()) as {
      detail?: unknown
      trace_id?: unknown
    }
    detail = describeDetail(body?.detail)
    traceId = typeof body?.trace_id === 'string' ? body.trace_id : ''
    if (body?.detail && typeof body.detail === 'object' && !Array.isArray(body.detail)) {
      const raw = (body.detail as Record<string, unknown>).code
      if (typeof raw === 'string' && raw.trim()) code = raw.trim()
    }
  } catch {
    // 非 JSON 响应（例如边缘网关的纯文本 502）：退回状态文本。
  }
  if (!traceId) traceId = response.headers.get('X-Trace-ID') ?? ''
  const message = detail || response.statusText || `HTTP ${response.status}`
  // 追踪 ID 单独一行：它是"去服务端日志里查完整堆栈"的唯一钥匙，必须让
  // 操作者能照抄。配合 Failure 的 `white-space: pre-line` 渲染成两行。
  return {
    message: traceId ? `${message}\n追踪 ID：${traceId}` : message,
    code,
  }
}

/** 只要面向人的一句话（旧调用方的兼容入口）。 */
export async function readError(response: Response): Promise<string> {
  return (await readErrorParts(response)).message
}
