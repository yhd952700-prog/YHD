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
import {
  LOCAL_SERVICE_OFFLINE,
  LOCAL_SERVICE_UNSTABLE,
  readError,
} from './errorMessage'

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
  return (await apiFetchWithStatus<T>(path, init, acceptStatus)).data
}

export interface ApiResult<T> {
  data: T
  /** 真实 HTTP 状态。`acceptStatus` 放行的 503 也要能看见，才能说"正在准备"。 */
  status: number
}

/**
 * 连不上时补一次 `/v1/ready` 探测：服务活着但某端点失败，与服务根本没起来，
 * 对用户是两件事。
 *
 * 刻意用原生 `fetch` 而不是 `apiFetch`：走本模块会递归（探测自身失败又会再探测），
 * 还平白带上鉴权头；`/v1/ready` 是公开端点。探测自身失败只说明"确认不了"，
 * 按"没运行"处理是更安全的一侧 —— 用户会去看贾维斯是否在运行，而不是盯着"数据不可用"。
 */
async function probeLocalService(): Promise<boolean> {
  try {
    const response = await fetch('/v1/ready', {
      cache: 'no-store',
      signal: AbortSignal.timeout(2500),
    })
    return response.status > 0
  } catch {
    return false
  }
}

/**
 * 撤掉 Service Worker 回退外壳时注入的「本地服务未运行」横幅。
 *
 * 横幅是 SW 直接写进 HTML 的，只有页面重新加载才会消失；而数据恢复是自动的，
 * 两者不同步就会出现"数据都回来了、顶上还写着服务没运行"的假警报。
 */
function clearOfflineBanner(): void {
  try {
    document.getElementById('liuhao-offline-banner')?.remove()
  } catch {
    // 非浏览器环境：忽略。
  }
}

/** 同 `apiFetch`，但把 HTTP 状态一起返回。 */
export async function apiFetchWithStatus<T>(
  path: string,
  init?: RequestInit,
  acceptStatus?: readonly number[],
): Promise<ApiResult<T>> {
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

  const callerSignal = init?.signal
  const signal = callerSignal ?? AbortSignal.timeout(API_TIMEOUT_MS)
  let response: Response
  try {
    response = await fetch(path, { ...init, headers, cache: 'no-store', signal })
  } catch (err) {
    if (err instanceof DOMException && err.name === 'TimeoutError') {
      // 调用方自带 signal 时不知道它设了多久（升级/回滚会放宽到数十分钟），
      // 不能谎报默认的 15 秒；这类长操作超时后服务端可能仍在执行。
      throw new ApiError(
        408,
        callerSignal
          ? '请求超时。这次操作可能仍在进行中，请刷新后查看最新状态，不要重复提交。'
          : `请求超时（>${API_TIMEOUT_MS / 1000}s），请稍后重试`,
      )
    }
    // 用户主动取消（离开页面）不是故障；其余 fetch 失败基本都是"连不上服务"：
    // 后端没启动、端口不对、代理断了、本机断网。浏览器原文是 "Failed to fetch"，
    // 对用户没有信息量，这里统一换成"发生了什么 + 怎么办"。
    if (!(err instanceof DOMException && err.name === 'AbortError')) {
      // 补一次就绪探测：连接失败可能是"服务没开"，也可能只是"这一个端点出错"。
      // 两者对用户的要求完全不同（去打开贾维斯 / 直接等），不能都写成同一句
      // "数据不可用"。
      const alive = await probeLocalService()
      throw new ApiError(0, alive ? LOCAL_SERVICE_UNSTABLE : LOCAL_SERVICE_OFFLINE)
    }
    throw err
  }

  // 任一请求成功即说明本地服务已经回来：撤掉 SW 回退时注入的「本地服务未运行」
  // 横幅。否则数据已经恢复、横幅还挂着，界面反过来骗人。
  clearOfflineBanner()

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
    return { data: undefined as T, status: response.status }
  }
  try {
    return { data: (await response.json()) as T, status: response.status }
  } catch {
    // 200 却不是 JSON：典型是只起了静态站点（后端没起来）或反向代理把
    // 错误页当 200 返回。浏览器原文是 "Unexpected token '<'" 之类的解析错误，
    // 用户读不懂；这里换成"服务没准备好"这一类可执行提示。
    throw new ApiError(
      response.status,
      '服务没有返回正常的数据。请确认贾维斯仍在运行并完成初始化，然后重试。',
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
  /**
   * 最近一次**成功**响应的 HTTP 状态（含被 `acceptStatus` 放行的 503）。
   * 失败或尚未取数时为 null。`/v1/ready` 用它区分"真的降级"与"刚启动、正在准备"。
   */
  httpStatus: number | null
  /** 首次加载尚未完成 */
  loading: boolean
  reload: () => void
}

/** 自动重连的退避：3s → 6s → 12s → 24s → 封顶 30s。 */
const RETRY_BASE_MS = 3_000
const RETRY_MAX_MS = 30_000

/**
 * 只有"连接层"失败才值得自动重连：连不上（status 0）与网关暂时不可用
 * （502/503/504）。403/404 这类重试多少次结果都一样，交给用户改操作。
 */
function shouldAutoRetry(status: number | null): boolean {
  return status === 0 || status === 502 || status === 503 || status === 504
}

function retryDelayMs(attempt: number): number {
  return Math.min(RETRY_BASE_MS * 2 ** attempt, RETRY_MAX_MS)
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
  const [httpStatus, setHttpStatus] = useState<number | null>(null)
  const [nonce, setNonce] = useState(0)
  const requestId = useRef(0)

  // 数组字面量每次渲染都是新引用；用 join 结果做依赖，避免无意义的重新轮询。
  const acceptKey = acceptStatus ? acceptStatus.join(',') : ''

  const reload = useCallback(() => setNonce((value) => value + 1), [])

  useEffect(() => {
    if (!path) return

    let alive = true
    let retryTimer = 0
    let attempt = 0
    let inFlight = false

    const clearRetry = () => {
      if (retryTimer) window.clearTimeout(retryTimer)
      retryTimer = 0
    }

    const load = async () => {
      // 退避重连与固定轮询可能同时到点；同一时刻只发一个请求。
      if (inFlight) return
      inFlight = true
      const id = ++requestId.current
      try {
        const accepted = acceptKey
          ? acceptKey.split(',').map((value) => Number(value))
          : undefined
        const result = await apiFetchWithStatus<T>(path, undefined, accepted)
        if (!alive || id !== requestId.current) return
        setData(result.data)
        setError(null)
        setStatus(null)
        setHttpStatus(result.status)
        attempt = 0
        clearRetry()
      } catch (err) {
        if (!alive || id !== requestId.current) return
        const failure = err instanceof ApiError ? err.status : null
        const message = err instanceof Error ? err.message : '请求失败'
        setStatus(failure)
        setHttpStatus(null)
        if (shouldAutoRetry(failure)) {
          // 进度写进文案：用户看到的是"第几次、多久后再试"，而不是一句冻住的报错。
          const delay = retryDelayMs(attempt)
          attempt += 1
          setError(
            `${message}（正在自动重连：第 ${attempt} 次，约 ${Math.round(delay / 1000)} 秒后）`,
          )
          clearRetry()
          retryTimer = window.setTimeout(() => {
            if (alive) void load()
          }, delay)
        } else {
          setError(message)
        }
      } finally {
        inFlight = false
      }
    }

    void load()
    const timer = intervalMs > 0 ? window.setInterval(() => void load(), intervalMs) : 0
    return () => {
      alive = false
      clearRetry()
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
    httpStatus: idle ? null : httpStatus,
    loading: idle ? false : data === null && error === null,
    reload,
  }
}
