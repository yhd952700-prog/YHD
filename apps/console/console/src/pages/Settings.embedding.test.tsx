/**
 * 系统设置 · 知识库向量化卡（只读）回归。
 *
 * 覆盖：
 *   1. 知识页「去系统设置」的深链真的落到 `settings-embedding`：滚动、聚焦、高亮；
 *   2. 隐私卡不再承诺「显式选择云端向量化服务」，改说部署环境变量的事实；
 *   3. 第三种状态（enabled=false 但 provider/model 有值）必须说「向量库不可用」，
 *      不得说「尚未配置」，也不把 vectors=0 渲染成空库；
 *   4. 「重新检测」真的重新请求 `/v1/knowledge/status`；
 *   5. 既有 `?model=` 深链聚焦与成员只读口径不回归。
 */

import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { Settings } from './Settings'
import { findBannedTerms } from '../lib/accountCaps'
import type { Session } from '../lib/auth'
import type { DeviceModeState } from '../lib/mode'

const captured = vi.hoisted(() => ({
  focusModelId: undefined as string | null | undefined,
}))

vi.mock('../components/ModelManager', () => ({
  ModelManager: (props: { focusModelId?: string | null }) => {
    captured.focusModelId = props.focusModelId
    return <div data-testid="model-manager" />
  },
  providerLabel: (provider: string) => provider,
}))
vi.mock('../components/DiagnosticsExport', () => ({
  DiagnosticsExport: () => <div />,
}))
vi.mock('../components/LogRetentionPanel', () => ({
  LogRetentionPanel: () => <div />,
}))
vi.mock('../components/UpgradePanel', () => ({
  UpgradePanel: () => <div />,
}))

const ENABLED = {
  enabled: true,
  provider: 'ollama',
  model: 'nomic-embed-text',
  vectors: 120,
  reason: null,
  message: '知识库已启用',
}

const MISSING_MODEL =
  "知识库未启用：本机 Ollama 缺少 embedding 模型 'nomic-embed-text'（已安装：llama3:latest）。" +
  '请执行 `ollama pull nomic-embed-text`，或设置 LIUHAO_EMBEDDING_MODEL'

const SESSION: Session = {
  token: 't',
  principal: 'root',
  displayName: '主账号',
  scope: null,
  permissions: [],
  expiresAt: 0,
  client: 'desktop',
  isRoot: true,
}

const DEVICE: DeviceModeState = {
  mode: 'desktop',
  detected: 'desktop',
  override: null,
  theme: 'dark',
  setOverride: () => {},
  setTheme: () => {},
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

let statusPayload: unknown
let fetchMock: ReturnType<typeof vi.fn>
let originalScrollIntoView: unknown

beforeEach(() => {
  statusPayload = ENABLED
  captured.focusModelId = undefined
  window.history.replaceState(null, '', '/')
  fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url.startsWith('/v1/knowledge/status')) return json(200, statusPayload)
    if (url.startsWith('/v1/network/research')) {
      return json(200, { enabled: false, sites: [], updated_at: null })
    }
    return json(404, { detail: 'not found' })
  })
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  if (originalScrollIntoView === undefined) return
  Object.defineProperty(Element.prototype, 'scrollIntoView', {
    configurable: true,
    writable: true,
    value: originalScrollIntoView,
  })
  originalScrollIntoView = undefined
})

function renderSettings(options?: { focusModelId?: string | null; isRoot?: boolean }) {
  const session: Session = { ...SESSION, isRoot: options?.isRoot ?? true }
  return render(
    <Settings
      device={DEVICE}
      session={session}
      notifications={[]}
      onLogout={() => {}}
      focusModelId={options?.focusModelId}
    />,
  )
}

function statusCalls(): number {
  return fetchMock.mock.calls.filter(([url]) => String(url).startsWith('/v1/knowledge/status'))
    .length
}

describe('系统设置 · 知识库向量化卡', () => {
  test('深链 ?focus=embedding 真的滚到卡片、聚焦并高亮', async () => {
    originalScrollIntoView = Element.prototype.scrollIntoView
    const seen: Element[] = []
    Object.defineProperty(Element.prototype, 'scrollIntoView', {
      configurable: true,
      writable: true,
      value: function (this: Element) {
        seen.push(this)
      },
    })

    window.history.replaceState(null, '', '/?p=settings&focus=embedding')
    renderSettings()

    await screen.findByText('知识库向量化')
    const card = document.getElementById('settings-embedding')
    expect(card).toBeTruthy()

    // 断言落点：滚动的是这张卡，而不只是卡片存在。
    await waitFor(() => expect(seen).toContain(card))
    await waitFor(() =>
      expect(document.getElementById('settings-embedding')?.className).toContain(
        'sc-panel-active',
      ),
    )
    expect(document.activeElement).toBe(card)
    // 落点参数一次性消费，避免之后每次进设置页都再高亮一次。
    expect(new URLSearchParams(window.location.search).get('focus')).toBeNull()
  })

  test('隐私卡改说事实：默认只走本机 Ollama，云端不提供界面配置', async () => {
    renderSettings()
    await screen.findByText('知识库向量化')

    expect(screen.getByText(/文档分片不离开本机/)).toBeTruthy()
    expect(screen.getByText(/云端向量化目前不提供界面配置/)).toBeTruthy()
    expect(screen.queryByText(/只有你显式选择云端向量化服务/)).toBeNull()
  })

  test('第三种状态：向量模型已就绪、向量库不可用，不得出现「尚未配置」', async () => {
    statusPayload = {
      enabled: false,
      provider: 'ollama',
      model: 'nomic-embed-text',
      vectors: 0,
      reason: '向量库不可用：OperationalError',
      message: '向量库不可用：OperationalError',
    }
    renderSettings()

    await screen.findByText('向量模型已就绪，但向量库不可用')
    expect(screen.queryByText(/尚未配置/)).toBeNull()
    expect(screen.queryByText(/还没有配置向量模型/)).toBeNull()
    // vectors=0 是回退值，不渲染成「0 条向量」。
    expect(screen.queryByText('向量总数')).toBeNull()
  })

  test('卡内的「重新检测」真的重新请求状态并更新界面', async () => {
    statusPayload = {
      enabled: false,
      provider: null,
      model: null,
      vectors: 0,
      reason: MISSING_MODEL,
      message: '知识库未启用',
    }
    renderSettings()
    await screen.findByText('本机 Ollama 缺少向量模型')
    expect(statusCalls()).toBe(1)

    statusPayload = ENABLED
    fireEvent.click(screen.getByRole('button', { name: '重新检测' }))

    await waitFor(() => expect(statusCalls()).toBe(2))
    await waitFor(() => expect(screen.queryByText('不可用原因')).toBeNull())
  })

  test('成员视角沿用同一套只读口径，既有 ?model= 聚焦不回归', async () => {
    renderSettings({ focusModelId: 'embed-3', isRoot: false })

    await screen.findByText('知识库向量化')
    expect(captured.focusModelId).toBe('embed-3')
    // 只读口径落在向量化卡内（页面别处也有同一句成员只读说明）。
    const card = document.getElementById('settings-embedding')
    expect(card).toBeTruthy()
    expect(within(card as HTMLElement).getByText(/该设置由系统统一管理/)).toBeTruthy()
    // 只读口径不能反过来暴露账号层级：卡内文案不得出现任何层级/角色词。
    expect(findBannedTerms((card as HTMLElement).textContent ?? '')).toEqual([])
    // 只读卡只有「重新检测」这一个重新取数的动作，没有任何保存类按钮。
    expect(within(card as HTMLElement).queryByRole('button', { name: /保存/ })).toBeNull()
  })

  test('联网检索设置读取中：说明为什么暂时点不了', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        if (url.startsWith('/v1/knowledge/status')) return json(200, ENABLED)
        if (url.startsWith('/v1/network/research')) return new Promise<Response>(() => {})
        return json(404, { detail: 'not found' })
      }),
    )

    renderSettings()
    expect(await screen.findByText(/正在读取联网检索设置/)).toBeTruthy()
    expect(screen.queryByText(/联网检索设置没读到/)).toBeNull()
  })

  test('联网检索设置读取失败：给出可点的重试，而不是永久灰着', async () => {
    let attempts = 0
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        if (url.startsWith('/v1/knowledge/status')) return json(200, ENABLED)
        if (url.startsWith('/v1/network/research')) {
          attempts += 1
          if (attempts === 1) {
            return json(500, {
              detail: { code: 'research_unavailable', message: '检索设置暂时读不到' },
            })
          }
          return json(200, { enabled: false, sites: [], updated_at: null })
        }
        return json(404, { detail: 'not found' })
      }),
    )

    renderSettings()
    expect(await screen.findByText(/联网检索设置没读到/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /重试/ }))
    await waitFor(() => expect(screen.queryByText(/联网检索设置没读到/)).toBeNull())
  })
})
