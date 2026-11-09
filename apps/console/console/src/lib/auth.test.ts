import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { loadAuthConfig, saveSession, sessionLossNotice, clearSession } from './auth'
import type { Session } from './auth'

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

const CONFIG = {
  auth_required: true,
  methods: ['password'],
  oauth: {},
  secret_store: { configured: true, source: 'file' },
  registered_humans: 1,
  login_eligible_humans: 1,
  client_modes: ['desktop'],
  token: { ttl_seconds: 3600 },
  message: '',
}

describe('登录页读取配置', () => {
  test('连不上后端 → 返回可行动的中文，不留下无限"读取中"', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.reject(new TypeError('Failed to fetch'))),
    )

    const result = await loadAuthConfig()

    expect(result.config).toBeNull()
    expect(result.error).toContain('鎏灏的本地服务没有运行')
    expect(result.error).toContain('请打开鎏灏')
    expect(result.error).not.toContain('Failed to fetch')
  })

  test('静态站点回 404 → 中文说明而不是浏览器英文', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: false,
        status: 404,
        headers: new Headers(),
        json: async () => {
          throw new SyntaxError('not json')
        },
        statusText: 'Not Found',
      })),
    )

    const result = await loadAuthConfig()
    expect(result.error).toContain('无法获取登录配置')
    expect(result.error).toContain('请确认鎏灏仍在运行')
    expect(result.error).not.toContain('Not Found')
  })

  test('200 但返回 HTML → 中文说明服务没有准备好', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        status: 200,
        headers: new Headers(),
        json: async () => {
          throw new SyntaxError("Unexpected token '<'")
        },
      })),
    )

    const result = await loadAuthConfig()
    expect(result.error).toContain('服务没有返回正常的数据')
    expect(result.error).not.toContain('Unexpected token')
  })

  test('成功时原样返回配置', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        status: 200,
        headers: new Headers(),
        json: async () => CONFIG,
      })),
    )

    const result = await loadAuthConfig()
    expect(result.error).toBeNull()
    expect(result.config?.login_eligible_humans).toBe(1)
    expect(result.status).toBe(200)
  })

  test('401（设备已被移除/未配对）→ 带回真实状态码，供登录页停止重试', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: false,
        status: 401,
        headers: new Headers({ 'Content-Type': 'application/json' }),
        json: async () => ({ detail: '设备未配对' }),
        text: async () => JSON.stringify({ detail: '设备未配对' }),
      })),
    )

    const result = await loadAuthConfig()
    expect(result.status).toBe(401)
    expect(result.config).toBeNull()
  })

  test('连不上时状态码为 null（登录页据此继续重试）', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.reject(new TypeError('Failed to fetch'))),
    )

    const result = await loadAuthConfig()
    expect(result.status).toBeNull()
  })
})

describe('会话失效时回登录页要说明原因', () => {
  test('本地令牌已被清掉（401）→ 提示"登录已过期"', () => {
    expect(sessionLossNotice(false)).toBe('expired')
  })

  test('令牌还在（网络/服务暂时不可达）→ 不登出、不提示', () => {
    expect(sessionLossNotice(true)).toBeNull()
  })
})

// ── 换账号：本地缓存不能把上一个账号的数据带给下一个 ──────────────────────

function memoryStorage(): Storage {
  const data = new Map<string, string>()
  return {
    get length() {
      return data.size
    },
    clear: () => data.clear(),
    getItem: (key: string) => data.get(key) ?? null,
    key: (index: number) => Array.from(data.keys())[index] ?? null,
    removeItem: (key: string) => {
      data.delete(key)
    },
    setItem: (key: string, value: string) => {
      data.set(key, String(value))
    },
  }
}

function sessionFor(principal: string): Session {
  return {
    token: `token-${principal}`,
    principal,
    displayName: null,
    scope: null,
    permissions: [],
    expiresAt: 0,
    client: 'web',
    isRoot: principal === 'root1',
  }
}

describe('同浏览器换账号：不串上一账号的本地缓存', () => {
  beforeEach(() => {
    vi.stubGlobal('localStorage', memoryStorage())
  })

  test('登主账号后留下的会话清单/身份/模型，在登子账号时被清掉', () => {
    saveSession(sessionFor('root1'), false)
    // 主账号使用过程中，工作台会把这些"属于 root1"的本地键写满。
    localStorage.setItem('liuhao.sessions', JSON.stringify(['root-session']))
    localStorage.setItem('liuhao.session_meta', JSON.stringify({ 'root-session': { title: '主账号会话', updatedAt: 1 } }))
    localStorage.setItem('liuhao.session_id', 'root-session')
    localStorage.setItem('liuhao.user_id', 'root1')
    localStorage.setItem('liuhao.model_id', 'root-model')

    saveSession(sessionFor('member1'), false)

    for (const key of [
      'liuhao.sessions',
      'liuhao.session_meta',
      'liuhao.session_id',
      'liuhao.user_id',
      'liuhao.model_id',
    ]) {
      expect(localStorage.getItem(key), key).toBeNull()
    }
    expect(localStorage.getItem('liuhao.last_principal')).toBe('member1')
  })

  test('同一账号重新登录（令牌刷新）不清自己的本地缓存', () => {
    saveSession(sessionFor('root1'), false)
    localStorage.setItem('liuhao.sessions', JSON.stringify(['root-session']))

    saveSession(sessionFor('root1'), false)

    expect(JSON.parse(localStorage.getItem('liuhao.sessions') || '[]')).toEqual(['root-session'])
  })

  test('登出即清账号级缓存（不等下一次登录）', () => {
    saveSession(sessionFor('root1'), true)
    localStorage.setItem('liuhao.sessions', JSON.stringify(['root-session']))
    localStorage.setItem('liuhao.session_meta', JSON.stringify({ 'root-session': { title: '主账号会话' } }))
    localStorage.setItem('liuhao.session_id', 'root-session')
    localStorage.setItem('liuhao.user_id', 'root1')
    localStorage.setItem('liuhao.model_id', 'root-model')

    clearSession()

    for (const key of [
      'liuhao.sessions',
      'liuhao.session_meta',
      'liuhao.session_id',
      'liuhao.user_id',
      'liuhao.model_id',
      'liuhao.last_principal',
      'liuhao.session',
    ]) {
      expect(localStorage.getItem(key), key).toBeNull()
    }
  })
})
