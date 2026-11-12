/**
 * 桌面端一次性证明（本机恢复码交接用）。
 *
 * 回环 IP 不等于“本人”：共享电脑上同机其他账户也能访问 127.0.0.1。桌面壳
 * 通过带 DACL 的命名管道申请一次性令牌，把令牌放在打开控制台 URL 的
 * fragment 里；这里负责把它收进 sessionStorage（不写入历史/日志），并在
 * 取恢复码、重置密码时放进对应的请求头。
 *
 * 令牌单次有效、带 TTL，刷新页面不会重新生成；用完即清。
 */

export type LocalProofKind = 'handoff' | 'reset'

const STORAGE_KEY: Record<LocalProofKind, string> = {
  handoff: 'liuhao.localProof.handoff',
  reset: 'liuhao.localProof.reset',
}

const HEADER: Record<LocalProofKind, string> = {
  handoff: 'X-LiuHao-Local-Proof',
  reset: 'X-LiuHao-Reset-Proof',
}

const FRAGMENT_KEY: Record<LocalProofKind, string> = {
  handoff: 'liuhao-local-proof',
  reset: 'liuhao-reset-proof',
}

function storage(): Storage | null {
  try {
    return typeof window !== 'undefined' ? window.sessionStorage : null
  } catch {
    return null
  }
}

/**
 * 启动时调用：把 URL fragment 里的令牌收进 sessionStorage，并立刻从地址栏
 * 抹掉，避免令牌停留在历史记录/截图里。
 */
export function captureLocalProofFromHash(): void {
  if (typeof window === 'undefined') return
  const raw = window.location.hash.replace(/^#/, '')
  if (!raw) return
  const params = new URLSearchParams(raw)
  const store = storage()
  let captured = false
  for (const kind of Object.keys(FRAGMENT_KEY) as LocalProofKind[]) {
    const value = params.get(FRAGMENT_KEY[kind])
    if (value && store) {
      store.setItem(STORAGE_KEY[kind], value)
      captured = true
    }
  }
  if (captured) {
    const clean = window.location.pathname + window.location.search
    window.history.replaceState(null, '', clean)
  }
}

export function localProofHeader(kind: LocalProofKind): Record<string, string> {
  const store = storage()
  const value = store?.getItem(STORAGE_KEY[kind])
  return value ? { [HEADER[kind]]: value } : {}
}

export function hasLocalProof(kind: LocalProofKind): boolean {
  return Boolean(storage()?.getItem(STORAGE_KEY[kind]))
}

export function clearLocalProof(kind: LocalProofKind): void {
  storage()?.removeItem(STORAGE_KEY[kind])
}
