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
 * 把一次非 2xx 响应解成给人类看的一句话。
 *
 * 优先用后端体里的 `detail`，并把 `trace_id` 一并附上（回退到响应头
 * `X-Trace-ID`，跨域部署下也能拿到）。两者都没有时退回 HTTP 状态文本。
 */
export async function readError(response: Response): Promise<string> {
  let detail = ''
  let traceId = ''
  try {
    const body = (await response.json()) as { detail?: unknown; trace_id?: unknown }
    detail = describeDetail(body?.detail)
    traceId = typeof body?.trace_id === 'string' ? body.trace_id : ''
  } catch {
    // 非 JSON 响应（例如边缘网关的纯文本 502）：退回状态文本。
  }
  if (!traceId) traceId = response.headers.get('X-Trace-ID') ?? ''
  const message = detail || response.statusText || `HTTP ${response.status}`
  // 追踪 ID 单独一行：它是"去服务端日志里查完整堆栈"的唯一钥匙，必须让
  // 操作者能照抄。配合 Failure 的 `white-space: pre-line` 渲染成两行。
  return traceId ? `${message}\n追踪 ID：${traceId}` : message
}
