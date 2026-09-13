/**
 * 带会话的取数层 —— 驾驶舱所有真实数据的统一入口。
 *
 * 两个职责：
 * 1. 自动附带 `Authorization: Bearer`，并在 401 时**立刻清除会话**、广播
 *    未认证事件（否则界面会停在一个"看起来已登录但每个请求都失败"的状态）；
 * 2. 轮询组件 `useApi`，把"过期响应覆盖新响应"这类竞态挡在组件之外。
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { clearSession, currentToken, UNAUTHENTICATED_EVENT } from './auth'

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function readError(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown }
    if (typeof body?.detail === 'string') return body.detail
    if (body?.detail) return JSON.stringify(body.detail)
  } catch {
    // 非 JSON：退回状态文本。
  }
  return response.statusText || `HTTP ${response.status}`
}

/** 发一个带会话的请求。401 会清会话并广播未认证。
 *
 * `acceptStatus` 允许调用方声明"这些非 2xx 也是有效响应"。唯一的用途是
 * ``/v1/ready``：它在降级时返回 503 **并且带着完整的检查项**，而"哪一项不健康"
 * 恰恰是状态页最需要显示的内容。把它当作错误会丢掉最有用的信息。
 */
export async function apiFetch<T>(
  path: string,
  init?: RequestInit,
  acceptStatus?: readonly number[],
): Promise<T> {
  const token = currentToken()
  const headers = new Headers(init?.headers)
  if (token) headers.set('Authorization', `Bearer ${token}`)
  if (init?.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }

  const response = await fetch(path, { ...init, headers, cache: 'no-store' })

  if (response.status === 401) {
    clearSession()
    try {
      window.dispatchEvent(new CustomEvent(UNAUTHENTICATED_EVENT))
    } catch {
      // 非浏览器环境：忽略。
    }
    throw new ApiError(401, await readError(response))
  }
  if (!response.ok && !acceptStatus?.includes(response.status)) {
    throw new ApiError(response.status, await readError(response))
  }
  return (await response.json()) as T
}

export interface ApiState<T> {
  data: T | null
  error: string | null
  /** 首次加载尚未完成 */
  loading: boolean
  reload: () => void
}

/**
 * 轮询一个只读端点。
 *
 * `path` 为 `null` 时不发请求（例如未登录）。`intervalMs` 为 0 表示只取一次。
 * 竞态处理：每次请求带一个自增序号，只有最新一次的结果会被写入 state —— 否则
 * 慢的旧响应会覆盖新响应。
 */
export function useApi<T>(
  path: string | null,
  intervalMs = 0,
  acceptStatus?: readonly number[],
): ApiState<T> {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [nonce, setNonce] = useState(0)
  const requestId = useRef(0)

  // 数组字面量每次渲染都是新引用；用 join 结果做依赖，避免无意义的重新轮询。
  const acceptKey = acceptStatus ? acceptStatus.join(',') : ''

  const reload = useCallback(() => setNonce((value) => value + 1), [])

  useEffect(() => {
    if (!path) return

    let alive = true

    const load = async () => {
      const id = ++requestId.current
      try {
        const accepted = acceptKey
          ? acceptKey.split(',').map((value) => Number(value))
          : undefined
        const payload = await apiFetch<T>(path, undefined, accepted)
        if (!alive || id !== requestId.current) return
        setData(payload)
        setError(null)
      } catch (err) {
        if (!alive || id !== requestId.current) return
        setError(err instanceof Error ? err.message : '请求失败')
      }
    }

    void load()
    const timer = intervalMs > 0 ? window.setInterval(() => void load(), intervalMs) : 0
    return () => {
      alive = false
      if (timer) window.clearInterval(timer)
    }
  }, [path, intervalMs, nonce, acceptKey])

  // 两个状态都是**派生**的，不需要额外的 state 去跟踪：
  //
  // * `idle`：`path === null` 表示"暂不取数"（例如未登录）。在这里覆盖而不是在
  //   effect 里 setState —— 后者会多跑一轮渲染，还会让"登出之后上一个账号的数据
  //   仍挂在屏幕上"成为可能。
  // * `loading`：只有"还没有任何结果"才算加载中，也就是 `data === null` 且
  //   `error === null`。这样轮询刷新时不会把已有数据换回骨架屏。
  const idle = path === null
  return {
    data: idle ? null : data,
    error: idle ? null : error,
    loading: idle ? false : data === null && error === null,
    reload,
  }
}
