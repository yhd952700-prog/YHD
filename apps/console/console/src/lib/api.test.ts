import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, test, vi } from 'vitest'
import { apiFetch, ApiError, useApi } from './api'

afterEach(() => {
  vi.useRealTimers()
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
    expect(apiError.message).toContain('鎏灏的本地服务没有运行')
    expect(apiError.message).toContain('请打开鎏灏')
    expect(apiError.message).not.toContain('Failed to fetch')
  })

  test('服务活着但端点失败 → 不说"服务没运行"，只说这次请求没成功', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL) => {
        // 主请求失败，就绪探测成功：说明服务在跑，只是这次请求没成。
        if (String(input) === '/v1/ready') {
          return Promise.resolve(new Response('{"status":"ready"}', { status: 200 }))
        }
        return Promise.reject(new TypeError('Failed to fetch'))
      }),
    )

    const error = (await apiFetch('/v1/dashboard/summary').catch(
      (err: unknown) => err,
    )) as ApiError
    expect(error.status).toBe(0)
    expect(error.message).toContain('鎏灏的本地服务在运行')
    expect(error.message).not.toContain('没有运行')
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

  test('长任务自带超时时不谎报默认 15 秒，并提醒先查状态', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.reject(new DOMException('The operation timed out.', 'TimeoutError')),
      ),
    )

    const error = (await apiFetch('/v1/upgrade/apply', {
      method: 'POST',
      signal: AbortSignal.timeout(60_000),
    }).catch((err: unknown) => err)) as ApiError

    expect(error.status).toBe(408)
    expect(error.message).not.toContain('15s')
    expect(error.message).toContain('刷新')
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
    expect(error.message).toContain('请确认启动窗口仍在运行')
    expect(error.message).not.toContain('Unexpected token')
  })
})

describe('useApi 断线后自动重连', () => {
  test('连不上服务时按退避自动重试，恢复后自己填上数据', async () => {
    vi.useFakeTimers()
    // 服务整体不可达：主请求与就绪探测都失败；恢复后一起成功。
    let down = true
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        if (down) throw new TypeError('Failed to fetch')
        return new Response(JSON.stringify({ ready: true }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }),
    )

    const { result } = renderHook(() => useApi<{ ready: boolean }>('/v1/ready'))

    // 首次请求失败（微任务）—— 断言"没有死路，只有进度"。
    await act(async () => {})
    expect(result.current.error).toContain('正在自动重连：第 1 次')
    expect(result.current.error).toContain('鎏灏的本地服务没有运行')
    expect(result.current.error).toContain('3 秒后')
    expect(result.current.data).toBeNull()

    // 服务恢复；退避首档 3 秒到点自动再试，不需要用户点任何按钮。
    down = false
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3_000)
    })

    expect(result.current.data).toEqual({ ready: true })
    expect(result.current.error).toBeNull()
    expect(result.current.httpStatus).toBe(200)
  })

  test('退避逐次拉长：第二次失败等 6 秒，并说明是第几次', async () => {
    vi.useFakeTimers()
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.reject(new TypeError('Failed to fetch'))),
    )

    const { result } = renderHook(() => useApi('/v1/ready'))

    await act(async () => {})
    expect(result.current.error).toContain('第 1 次')
    expect(result.current.error).toContain('3 秒后')

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3_000)
    })
    expect(result.current.error).toContain('第 2 次')
    expect(result.current.error).toContain('6 秒后')
  })

  test('被放行的 503（正在准备）不算错误，只记录真实状态码', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(JSON.stringify({ status: 'starting' }), {
          status: 503,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    )

    const { result } = renderHook(() =>
      useApi<{ status: string }>('/v1/ready', 0, [503]),
    )

    await waitFor(() => expect(result.current.data).toEqual({ status: 'starting' }))
    expect(result.current.error).toBeNull()
    expect(result.current.httpStatus).toBe(503)
  })
})
