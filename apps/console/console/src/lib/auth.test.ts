import { afterEach, describe, expect, test, vi } from 'vitest'
import { loadAuthConfig } from './auth'

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
    expect(result.error).toContain('无法连接到鎏灏服务')
    expect(result.error).toContain('请确认服务已经启动')
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
    expect(result.error).toContain('请确认鎏灏服务已经启动')
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
  })
})
