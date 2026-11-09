/**
 * `mapProbeReason` 的回归测试。
 *
 * 连通性测试的 `reason` 是机器可读的英文枚举，界面必须把它翻译成"用户下一步该
 * 改什么"。这里把六种已知原因与两种回落路径钉死，避免以后改动把 `upstream_error`
 * 误标成"API Key 无效"这类错误归因。
 */

import { afterEach, describe, expect, test, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import {
  canBecomeDefault,
  describeModelDiff,
  mapProbeReason,
  ModelManager,
  presetFormPatch,
} from './ModelManager'
import type { ModelRecord } from './ModelManager'
import type { Session } from '../lib/auth'

describe('mapProbeReason', () => {
  test('成功（reason 为空）→ 带上耗时的连接成功', () => {
    expect(mapProbeReason('', 123.4, 'pong')).toBe('连接成功（123.4 ms）')
  })

  test.each([
    ['invalid_key', 'API Key 无效或没有权限。请检查 Key 是否填错、是否复制完整；建议重新生成后再试。'],
    ['key_revoked', 'API Key 已被提供方停用或吊销。请到提供方控制台重新生成 Key，再填到这里。'],
    ['quota_exhausted', '账户额度已用完。请到提供方控制台充值或提升额度后重试。'],
    ['model_not_found', '模型名不存在，或该接口地址不支持这个模型。请核对模型名称后重试。'],
    ['unreachable', '接口地址不可达。请检查地址、端口与网络连接是否正常。'],
    ['timeout', '连接超时。请检查网络，或稍后再试。'],
    ['rate_limited', '请求被上游限流（429）。请稍后重试。'],
  ])('%s → 明确中文建议', (reason, expected) => {
    expect(mapProbeReason(reason, 10, '上游原文')).toBe(expected)
  })

  test('发生过自动重试 → 在结论后追加一句可见提示', () => {
    // 重试意味着用户已经多等了一段：成功后也要说明，否则"怎么这么慢"无从解释。
    expect(mapProbeReason('', 3200, 'pong', '生成较慢，正在重试（模型：qwen2.5:3b）…')).toBe(
      '连接成功（3200 ms）（本次已自动重试一次：首次尝试较慢）',
    )
    expect(mapProbeReason('timeout', 10, '', '')).not.toContain('已自动重试')
  })

  test('upstream_error → 中性中文，不展示上游英文原文', () => {
    const message = mapProbeReason('upstream_error', 10, 'HTTP 500: upstream exploded')
    expect(message).toBe('模型服务返回了错误，连接没有成功。请稍后重试；持续出现时可导出诊断包。')
    expect(message).not.toContain('upstream exploded')
    expect(message).not.toContain('HTTP 500')
  })

  test('未知原因 → 给可执行的通用建议，不回显机器码或上游原文', () => {
    expect(mapProbeReason('bad_gateway', 10, '网关返回 502')).toBe(
      '连接失败：请检查服务商、模型名与接口地址设置后重试。',
    )
    // 回落到机器码会把 `bad_gateway` 这类内部枚举直接铺到界面上，必须禁止。
    expect(mapProbeReason('bad_gateway', 10, '')).toBe(
      '连接失败：请检查服务商、模型名与接口地址设置后重试。',
    )
  })
})

describe('canBecomeDefault', () => {
  test('未测试通过（或新建）的模型不允许设为默认', () => {
    expect(canBecomeDefault(undefined)).toBe(false)
    expect(canBecomeDefault({ verified: false })).toBe(false)
    expect(canBecomeDefault({})).toBe(false)
  })

  test('「测试连接」通过的已保存模型才允许', () => {
    expect(canBecomeDefault({ verified: true })).toBe(true)
  })
})

describe('presetFormPatch', () => {
  test('套用内置预设 → 只回填协议/端点/模型名', () => {
    expect(
      presetFormPatch({
        id: 'deepseek',
        provider: 'deepseek',
        label: 'DeepSeek',
        base_url: 'https://api.deepseek.com',
        model: 'deepseek-chat',
        requires_api_key: true,
      }),
    ).toEqual({
      provider: 'deepseek',
      baseUrl: 'https://api.deepseek.com',
      model: 'deepseek-chat',
    })
  })

  test('OpenAI 兼容自定义 → 端点与模型名留空，等用户填写', () => {
    expect(
      presetFormPatch({
        id: 'openai_compatible',
        provider: 'openai',
        label: 'OpenAI 兼容（自定义端点）',
        base_url: null,
        model: '',
        requires_api_key: true,
        custom: true,
      }),
    ).toEqual({ provider: 'openai', baseUrl: '', model: '' })
  })
})

const SESSION: Session = {
  token: 't',
  principal: 'root',
  displayName: '主账号',
  scope: null,
  permissions: ['admin'],
  expiresAt: 0,
  client: 'desktop',
  isRoot: true,
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const MODELS_EMPTY = {
  models: [],
  default_id: null,
  count: 0,
  supported_providers: ['openai_compatible'],
  encryption: { algorithm: 'aes-256-gcm', key_source: 'local' },
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('模型管理状态', () => {
  test('服务商列表读不到：表单上方给出错误与可点的重试', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation((input) => {
      const url = String(input)
      if (url.includes('/v1/models/presets')) return Promise.resolve(json({ presets: [] }))
      return Promise.resolve(
        json(
          {
            detail: { code: 'model_list_unavailable', message: '模型服务暂时读不到' },
          },
          500,
        ),
      )
    })

    render(<ModelManager session={SESSION} />)
    expect(await screen.findByText(/服务商与已有模型没读到/)).toBeTruthy()
    expect(
      screen.getByText(
        '可用服务商没读到，所以下拉是空的；先点上面的「重试」，读回来再选。',
      ),
    ).toBeTruthy()
    expect(screen.getAllByRole('button', { name: /重试/ }).length).toBeGreaterThan(0)
  })

  test('服务商为空但接口正常：说清“这次确实没有”，不留一个空下拉', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation((input) => {
      const url = String(input)
      if (url.includes('/v1/models/presets')) return Promise.resolve(json({ presets: [] }))
      return Promise.resolve(json({ ...MODELS_EMPTY, supported_providers: [] }))
    })

    render(<ModelManager session={SESSION} />)
    expect(await screen.findByText(/这次没有读到任何可用服务商/)).toBeTruthy()
    expect(screen.getByText(/这次没有内置预设/)).toBeTruthy()
  })

  test('预设接口失败不静默：给提示与重试，仍可手填', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation((input) => {
      const url = String(input)
      if (url.includes('/v1/models/presets')) {
        return Promise.resolve(
          json(
            { detail: { code: 'presets_unavailable', message: '预设暂时读不到' } },
            500,
          ),
        )
      }
      return Promise.resolve(json(MODELS_EMPTY))
    })

    render(<ModelManager session={SESSION} />)
    expect(await screen.findByText(/服务商预设没读到/)).toBeTruthy()
    expect(screen.getAllByRole('button', { name: /重试/ }).length).toBeGreaterThan(0)
  })
})

const VERSION_CONFLICT = '这个模型刚在另一个窗口被改过，请刷新后再保存。'

const MODEL: ModelRecord = {
  id: 'm-1',
  name: '旧名字',
  provider: 'openai',
  vendor: 'openai',
  model: 'gpt-4o-mini',
  base_url: 'https://api.example.com/v1',
  timeout: 30,
  is_default: false,
  enabled: true,
  verified: true,
  api_key_set: true,
  api_key_masked: 'sk-***1234',
  api_key_status: 'ok',
  created_at: '2026-11-08T09:00:00',
  updated_at: '2026-11-08T10:00:00',
}

function modelsPayload(models: ModelRecord[]) {
  return {
    models,
    default_id: null,
    count: models.length,
    supported_providers: ['openai', 'openai_compatible'],
    encryption: { algorithm: 'aes-256-gcm', key_source: 'local' },
  }
}

describe('describeModelDiff', () => {
  test('只列出真正变化的字段，忽略 updated_at 本身', () => {
    const latest: ModelRecord = {
      ...MODEL,
      name: '新名字',
      timeout: 60,
      updated_at: '2026-11-08T10:05:00',
    }
    expect(describeModelDiff(MODEL, latest)).toBe('名称「旧名字」→「新名字」；超时 30 秒 → 60 秒')
    expect(describeModelDiff(MODEL, { ...MODEL, updated_at: '2026-11-08T10:30:00' })).toBe('')
  })
})

describe('乐观并发（P2 前端）', () => {
  test('编辑保存把读取到的 updated_at 作为 base_updated_at 带回', async () => {
    let putBody: Record<string, unknown> | null = null
    vi.spyOn(globalThis, 'fetch').mockImplementation((input, init) => {
      const url = String(input)
      if (url.includes('/v1/models/presets')) return Promise.resolve(json({ presets: [] }))
      if (init?.method === 'PUT') {
        putBody = JSON.parse(String(init.body)) as Record<string, unknown>
        return Promise.resolve(json(MODEL))
      }
      return Promise.resolve(json(modelsPayload([MODEL])))
    })

    render(<ModelManager session={SESSION} />)
    fireEvent.click(await screen.findByRole('button', { name: '编辑模型「旧名字」' }))
    fireEvent.click(screen.getByRole('button', { name: '保存修改' }))

    await waitFor(() => expect(putBody).not.toBeNull())
    expect(putBody).toMatchObject({ base_updated_at: '2026-11-08T10:00:00' })
  })

  test('409 版本冲突：给可操作中文提示，重新加载后能看出差异并再次保存覆盖', async () => {
    const latest: ModelRecord = {
      ...MODEL,
      name: '新名字',
      timeout: 60,
      updated_at: '2026-11-08T10:05:00',
    }
    let current = MODEL
    const putBodies: Record<string, unknown>[] = []
    vi.spyOn(globalThis, 'fetch').mockImplementation((input, init) => {
      const url = String(input)
      if (url.includes('/v1/models/presets')) return Promise.resolve(json({ presets: [] }))
      if (init?.method === 'PUT') {
        const body = JSON.parse(String(init.body)) as Record<string, unknown>
        putBodies.push(body)
        if (body.base_updated_at !== current.updated_at) {
          return Promise.resolve(
            json(
              { detail: { code: 'MODEL_VERSION_CONFLICT', message: VERSION_CONFLICT } },
              409,
            ),
          )
        }
        current = {
          ...current,
          name: String(body.name ?? current.name),
          timeout: Number(body.timeout ?? current.timeout),
          updated_at: '2026-11-08T10:10:00',
        }
        return Promise.resolve(json(current))
      }
      return Promise.resolve(json(modelsPayload([current])))
    })

    render(<ModelManager session={SESSION} />)
    fireEvent.click(await screen.findByRole('button', { name: '编辑模型「旧名字」' }))
    // 另一个窗口在这期间保存过：服务器上的版本已经变了。
    current = latest
    fireEvent.click(screen.getByRole('button', { name: '保存修改' }))

    expect(await screen.findByText(new RegExp(VERSION_CONFLICT))).toBeTruthy()
    const reloadButton = screen.getByRole('button', { name: '重新加载最新配置' })
    fireEvent.click(reloadButton)

    expect(
      await screen.findByText(/另一个窗口改动了：名称「旧名字」→「新名字」；超时 30 秒 → 60 秒/),
    ).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '保存修改' }))
    await waitFor(() => expect(putBodies).toHaveLength(2))
    // 第二次保存带的是重新加载后的版本号，所以不会再冲突。
    expect(putBodies[1]).toMatchObject({ base_updated_at: '2026-11-08T10:05:00' })
    expect(await screen.findByText('模型配置已更新')).toBeTruthy()
  })

  test('新建（POST）遇到 409 不被版本冲突逻辑劫持', async () => {
    let postBody: Record<string, unknown> | null = null
    vi.spyOn(globalThis, 'fetch').mockImplementation((input, init) => {
      const url = String(input)
      if (url.includes('/v1/models/presets')) return Promise.resolve(json({ presets: [] }))
      if (init?.method === 'POST' && url.endsWith('/v1/models')) {
        postBody = JSON.parse(String(init.body)) as Record<string, unknown>
        return Promise.resolve(
          json({ detail: { code: 'MODEL_NAME_TAKEN', message: '同名模型已存在' } }, 409),
        )
      }
      return Promise.resolve(json(MODELS_EMPTY))
    })

    render(<ModelManager session={SESSION} />)
    fireEvent.change(await screen.findByLabelText('名称（必填）'), {
      target: { value: '新模型' },
    })
    fireEvent.change(screen.getByLabelText('服务商（必填）'), {
      target: { value: 'openai_compatible' },
    })
    fireEvent.change(screen.getByLabelText('模型名（必填）'), {
      target: { value: 'gpt-4o-mini' },
    })
    fireEvent.click(screen.getByRole('button', { name: '添加模型' }))

    expect(await screen.findByText('同名模型已存在')).toBeTruthy()
    expect(screen.queryByRole('button', { name: '重新加载最新配置' })).toBeNull()
    expect(postBody).not.toBeNull()
    expect(postBody).not.toHaveProperty('base_updated_at')
  })

  test('删除与设为默认的 409 只走普通错误，不弹版本冲突', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    vi.spyOn(globalThis, 'fetch').mockImplementation((input, init) => {
      const url = String(input)
      if (url.includes('/v1/models/presets')) return Promise.resolve(json({ presets: [] }))
      if (init?.method === 'DELETE') {
        return Promise.resolve(
          json(
            { detail: { code: 'MODEL_IN_USE', message: '该模型正在被使用，暂时不能删除' } },
            409,
          ),
        )
      }
      if (url.endsWith('/default')) {
        return Promise.resolve(
          json(
            {
              detail: {
                code: 'MODEL_NOT_VERIFIED',
                message: '该模型尚未通过连通测试，不能设为默认',
              },
            },
            409,
          ),
        )
      }
      return Promise.resolve(json(modelsPayload([MODEL])))
    })

    render(<ModelManager session={SESSION} />)
    fireEvent.click(await screen.findByRole('button', { name: '删除模型「旧名字」' }))
    expect(await screen.findByText('该模型正在被使用，暂时不能删除')).toBeTruthy()
    expect(screen.queryByRole('button', { name: '重新加载最新配置' })).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: '把模型「旧名字」设为默认' }))
    expect(await screen.findByText('该模型尚未通过连通测试，不能设为默认')).toBeTruthy()
    expect(screen.queryByRole('button', { name: '重新加载最新配置' })).toBeNull()
  })
})
