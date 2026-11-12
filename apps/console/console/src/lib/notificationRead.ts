/**
 * 通知已读状态 —— 只记在这台设备上。
 *
 * 为什么不写服务端
 * ----------------
 * 通知中心复用的是 `/v1/events`（事件总线里真实发生过的事件）。已读/未读是
 * 个人阅读习惯，不是业务事实；在没有相应写入端点、也不该为此新增数据模型
 * 的前提下，唯一诚实的做法是把它留在本机（localStorage），并在界面上明说
 * 「只记在这台设备上」——换设备或清缓存后重来一次，不会假装服务端记得。
 *
 * 键的组成
 * --------
 * 用 `id:timestamp:type` 而不是单一 id：总线事件的 id 是关联 id，同一次关联下
 * 可能有多条事件，只用 id 会把它们一起标成已读。
 */

const STORAGE_KEY = 'liuhao.notifications.read.v1'
/** 只保留最近这么多条已读键，避免 localStorage 无限增长。 */
const KEEP_LIMIT = 800

/** 事件在已读集合里的唯一键。 */
export function notificationKey(event: { id: string; timestamp: string; type: string }): string {
  return `${event.id}:${event.timestamp}:${event.type}`
}

function readRaw(): string[] {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (!raw) return []
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed.filter((item): item is string => typeof item === 'string')
  } catch {
    // 隐私模式或存储被禁用：当作"没有已读记录"，不抛错挡住页面。
    return []
  }
}

export function loadReadKeys(): Set<string> {
  return new Set(readRaw())
}

/** 写入已读键，超出上限时丢掉最早的；返回落盘后的集合。 */
export function saveReadKeys(keys: Iterable<string>): Set<string> {
  const list = [...keys].slice(-KEEP_LIMIT)
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(list))
  } catch {
    // 存不进去就只在本页内存里生效，不阻塞阅读。
  }
  return new Set(list)
}
