/**
 * 带会话的取数层 —— 驾驶舱所有真实数据的统一入口。
 *
 * 两个职责：
 * 1. 自动附带会话令牌（私有头 `X-Liuhao-Token` + 标准头 `Authorization`，见
 *    `auth.tokenHeaders`），并在 401 时**立刻清除会话**、广播
 *    未认证事件（否则界面会停在一个"看起来已登录但每个请求都失败"的状态）；
 * 2. 轮询组件 `useApi`，把"过期响应覆盖新响应"这类竞态挡在组件之外。
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { clearSession, tokenHeaders, UNAUTHENTICATED_EVENT } from './auth'
import { readError } from './errorMessage'

export class ApiError extends Error {
  status: number
  /**
   * 后端 `detail.code` 的稳定错误码（如 `agent_not_found`）。
   * 某些端点的 `detail` 是纯字符串，此时为 null —— 界面按原始文案展示，
   * 绝不自己从人类可读句子里猜测错误类型。
   */
  code: string | null
  constructor(status: number, message: string, code: string | null = null) {
    super(message)
    this.status = status
    this.code = code
  }
}

/** 默认请求超时：没有超时的 fetch 会在后端卡住时永远悬着，界面一直转圈。 */
const API_TIMEOUT_MS = 15000

/** 发一个带会话的请求。401 会清会话并广播未认证。
 *
 * 所有请求统一带超时（`API_TIMEOUT_MS`）；调用方显式传入 `signal` 时以它为准
 * （例如用户主动取消）。
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
  // 令牌走「私有头 + 标准头」双写（见 auth.TOKEN_HEADER）：托管边缘网关会
  // 改写 `Authorization`，只有它不认识的私有头能原样穿过。
  const headers = tokenHeaders(init?.headers)
  // FormData 的 Content-Type 必须由浏览器自己写（带 multipart boundary）。
  if (
    init?.body &&
    !(init.body instanceof FormData) &&
    !headers.has('Content-Type')
  ) {
    headers.set('Content-Type', 'application/json')
  }

  const signal = init?.signal ?? AbortSignal.timeout(API_TIMEOUT_MS)
  let response: Response
  try {
    response = await fetch(path, { ...init, headers, cache: 'no-store', signal })
  } catch (err) {
    if (err instanceof DOMException && err.name === 'TimeoutError') {
      throw new ApiError(408, `请求超时（>${API_TIMEOUT_MS / 1000}s），请稍后重试`)
    }
    // 用户主动取消（离开页面）不是故障；其余 fetch 失败基本都是"连不上服务"：
    // 后端没启动、端口不对、代理断了、本机断网。浏览器原文是 "Failed to fetch"，
    // 对用户没有信息量，这里统一换成"发生了什么 + 怎么办"。
    if (!(err instanceof DOMException && err.name === 'AbortError')) {
      throw new ApiError(
        0,
        '无法连接到鎏灏服务。请确认服务已经启动、网络连接正常，然后重试；如果刚启动，请等几秒再试。',
      )
    }
    throw err
  }

  if (response.status === 401) {
    clearSession()
    try {
      window.dispatchEvent(new CustomEvent(UNAUTHENTICATED_EVENT))
    } catch {
      // 非浏览器环境：忽略。
    }
    const parts = await readError(response)
    throw new ApiError(401, parts.message, parts.code)
  }
  if (!response.ok && !acceptStatus?.includes(response.status)) {
    const parts = await readError(response)
    throw new ApiError(response.status, parts.message, parts.code)
  }
  if (response.status === 204) {
    return undefined as T
  }
  try {
    return (await response.json()) as T
  } catch {
    // 200 却不是 JSON：典型是只起了静态站点（后端没起来）或反向代理把
    // 错误页当 200 返回。浏览器原文是 "Unexpected token '<'" 之类的解析错误，
    // 用户读不懂；这里换成"服务没准备好"这一类可执行提示。
    throw new ApiError(
      response.status,
      '服务没有返回正常的数据。请确认鎏灏服务已经启动并完成初始化，然后重试。',
    )
  }
}

export interface ApiState<T> {
  data: T | null
  error: string | null
  /**
   * 最近一次失败的真实 HTTP 状态。403 与「空列表」是两件事：队列只对能签发
   * 凭据的主体可见，非管理员不是“没有待办”，而是“你看不到”。调用方需要
   * 状态码才能说对话。
   */
  status: number | null
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
  const [status, setStatus] = useState<number | null>(null)
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
        setStatus(null)
      } catch (err) {
        if (!alive || id !== requestId.current) return
        setError(err instanceof Error ? err.message : '请求失败')
        setStatus(err instanceof ApiError ? err.status : null)
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
    status: idle ? null : status,
    loading: idle ? false : data === null && error === null,
    reload,
  }
}
