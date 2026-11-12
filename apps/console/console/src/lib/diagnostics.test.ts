import { afterEach, describe, expect, test, vi } from 'vitest'
import { exportDiagnosticsBundle } from './diagnostics'

interface FakeResponseOptions {
  ok?: boolean
  status?: number
  filename?: string
  disposition?: string
  body?: Blob
  json?: unknown
  omitted?: string[]
}

function fakeResponse(options: FakeResponseOptions = {}): Response {
  const headers = new Headers()
  if (options.filename) headers.set('X-LiuHao-Diagnostics-Filename', options.filename)
  if (options.disposition) headers.set('Content-Disposition', options.disposition)
  if (options.omitted) {
    headers.set('X-LiuHao-Diagnostics-Omitted', String(options.omitted.length))
    headers.set('X-LiuHao-Diagnostics-Omitted-Members', JSON.stringify(options.omitted))
  }
  return {
    ok: options.ok ?? true,
    status: options.status ?? 200,
    headers,
    blob: async () => options.body ?? new Blob(['PK']),
    json: async () => options.json ?? {},
    statusText: '',
  } as unknown as Response
}

function stubPicker(impl: (...args: unknown[]) => Promise<unknown>) {
  ;(window as unknown as { showSaveFilePicker?: unknown }).showSaveFilePicker = vi.fn(impl)
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  delete (window as unknown as { showSaveFilePicker?: unknown }).showSaveFilePicker
})

describe('一键诊断包导出', () => {
  test('支持另存为时写入用户选择的位置，并回报文件名', async () => {
    const write = vi.fn(async () => undefined)
    const close = vi.fn(async () => undefined)
    stubPicker(async () => ({
      name: 'liuhao-diagnostics-20260101-120000.zip',
      createWritable: async () => ({ write, close }),
    }))
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        fakeResponse({ filename: 'liuhao-diagnostics-20260101-120000.zip' }),
      ),
    )

    const saved = await exportDiagnosticsBundle()

    expect(saved).toEqual({
      filename: 'liuhao-diagnostics-20260101-120000.zip',
      location: 'chosen',
      omitted: [],
    })
    expect(write).toHaveBeenCalledTimes(1)
    expect(close).toHaveBeenCalledTimes(1)
  })

  test('不支持另存为时触发下载，并回报文件名', async () => {
    vi.stubGlobal('URL', {
      createObjectURL: vi.fn(() => 'blob:diagnostics'),
      revokeObjectURL: vi.fn(),
    })
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, 'click')
      .mockImplementation(() => undefined)
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => fakeResponse({ disposition: 'attachment; filename="diag.zip"' })),
    )

    const saved = await exportDiagnosticsBundle()

    expect(saved).toEqual({ filename: 'diag.zip', location: 'download', omitted: [] })
    expect(click).toHaveBeenCalledTimes(1)
  })

  test('用户在系统对话框里取消 → 返回 null，不报错', async () => {
    stubPicker(async () => {
      throw new DOMException(' cancelled ', 'AbortError')
    })
    vi.stubGlobal('fetch', vi.fn(async () => fakeResponse()))

    await expect(exportDiagnosticsBundle()).resolves.toBeNull()
  })

  test('连不上服务时给出中文动作，不回显 Failed to fetch', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.reject(new TypeError('Failed to fetch'))),
    )

    const error = (await exportDiagnosticsBundle().catch((err: unknown) => err)) as Error
    expect(error.message).toContain('贾维斯的本地服务没有运行')
    expect(error.message).toContain('请打开贾维斯')
    expect(error.message).not.toContain('Failed to fetch')
  })

  test('降级导出时把被剔除的文件名带回界面', async () => {
    vi.stubGlobal('URL', {
      createObjectURL: vi.fn(() => 'blob:diagnostics'),
      revokeObjectURL: vi.fn(),
    })
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        fakeResponse({
          disposition: 'attachment; filename="diag.zip"',
          omitted: ['logs/gateway.log.txt'],
        }),
      ),
    )

    const saved = await exportDiagnosticsBundle()

    expect(saved?.omitted).toEqual(['logs/gateway.log.txt'])
  })

  test('后端拒绝时不返回半截文件，文案来自统一错误解析', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        fakeResponse({
          ok: false,
          status: 409,
          json: {
            detail: {
              code: 'DIAGNOSTIC_BUNDLE_BLOCKED',
              message:
                '诊断包内所有文件都未通过密钥自检，已停止导出。下一步：先退出贾维斯，把旧日志改名备份后再导出。',
              omitted_members: ['logs/liuhao.log.txt'],
            },
          },
        }),
      ),
    )

    const error = (await exportDiagnosticsBundle().catch((err: unknown) => err)) as Error
    expect(error.message).toContain('已停止导出')
    expect(error.message).toContain('下一步')
    expect(error.message).not.toContain('请重试')
  })
})
