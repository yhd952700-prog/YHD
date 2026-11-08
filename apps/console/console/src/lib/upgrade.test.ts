import { afterEach, describe, expect, test, vi } from 'vitest'
import { ApiError } from './api'
import {
  applyUpgrade,
  checkForUpdate,
  fetchUpgradeStatus,
  rollbackUpgrade,
} from './upgrade'

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: '',
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => body,
  } as unknown as Response
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('自我升级取数', () => {
  test('读取状态是只读 GET', async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ ok: true, source_configured: false }))
    vi.stubGlobal('fetch', fetchMock)

    const status = await fetchUpgradeStatus()

    expect(status.source_configured).toBe(false)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [path, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(path).toBe('/v1/upgrade/status')
    expect(init.method).toBeUndefined()
  })

  test('检查更新走 POST，不携带版本号（避免误装）', async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse({ ok: true, check: { status: 'up_to_date' }, status: { ok: true } }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await checkForUpdate()

    const [path, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(path).toBe('/v1/upgrade/check')
    expect(init.method).toBe('POST')
  })

  test('安装必须显式确认并指定版本', async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ ok: true }))
    vi.stubGlobal('fetch', fetchMock)

    await applyUpgrade('0.2.0')

    const [path, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(path).toBe('/v1/upgrade/apply')
    expect(init.method).toBe('POST')
    expect(JSON.parse(String(init.body))).toEqual({ confirm: true, version: '0.2.0' })
  })

  test('回滚默认取最近一次备份，确认位必须为真', async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ ok: true }))
    vi.stubGlobal('fetch', fetchMock)

    await rollbackUpgrade()

    const [path, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(path).toBe('/v1/upgrade/rollback')
    expect(JSON.parse(String(init.body))).toEqual({ confirm: true })
  })

  test('未配置更新源时把服务端的中文原因原样抛出', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        jsonResponse(
          {
            detail: {
              code: 'source_not_configured',
              message: '未配置更新源：请设置本机的发布目录或清单地址',
            },
          },
          409,
        ),
      ),
    )

    const failure = await checkForUpdate().catch((err: unknown) => err)

    expect(failure).toBeInstanceOf(ApiError)
    expect((failure as Error).message).toContain('未配置更新源')
    expect((failure as { code: string | null }).code).toBe('source_not_configured')
  })
})
