import { afterEach, describe, expect, test, vi } from 'vitest'
import { apiFetch, ApiError } from './api'

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('apiFetch 的网络失败文案', () => {
  test('浏览器 "Failed to fetch" 不直接上屏，换成"发生了什么 + 怎么办"', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.reject(new TypeError('Failed to fetch'))),
    )

    const error = await apiFetch('/v1/ready').catch((err: unknown) => err)
    expect(error).toBeInstanceOf(ApiError)
    const apiError = error as ApiError
    expect(apiError.status).toBe(0)
    expect(apiError.message).toContain('无法连接到鎏灏服务')
    expect(apiError.message).toContain('请确认服务已经启动')
    expect(apiError.message).not.toContain('Failed to fetch')
  })

  test('请求超时仍是 408 + 中文', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.reject(new DOMException('The operation timed out.', 'TimeoutError')),
      ),
    )

    const error = (await apiFetch('/v1/ready').catch((err: unknown) => err)) as ApiError
    expect(error.status).toBe(408)
    expect(error.message).toContain('请求超时')
  })

  test('调用方主动取消（AbortError）保持原样，不伪装成故障', async () => {
    const abort = new DOMException('aborted', 'AbortError')
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.reject(abort)),
    )

    await expect(apiFetch('/v1/ready')).rejects.toBe(abort)
  })

  test('返回 200 但不是 JSON（静态页/代理错误页）→ 中文提示而不是解析异常', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        status: 200,
        json: async () => {
          throw new SyntaxError("Unexpected token '<', \"<!DOCTYPE \"... is not valid JSON")
        },
      })),
    )

    const error = (await apiFetch('/v1/auth/config').catch((err: unknown) => err)) as ApiError
    expect(error).toBeInstanceOf(ApiError)
    expect(error.message).toContain('服务没有返回正常的数据')
    expect(error.message).toContain('请确认鎏灏服务已经启动')
    expect(error.message).not.toContain('Unexpected token')
  })
})
