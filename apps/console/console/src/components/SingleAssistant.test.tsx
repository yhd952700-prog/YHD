/**
 * 唯一助手回归（用户纠正，最高优先级）。
 *
 * 控制台只有一个助手，不可切换：
 *   * 助手就是「贾维斯」（你的 AI 管家），能力与风格都在这一个身份里；
 *   * 不存在 Muse / 小龙虾两个可切换的助手，也不接受把切换器加回来。
 *
 * 这个文件钉的是界面事实：如果将来有人把切换器（aria-label「切换助手」、
 * Muse / 小龙虾两个入口）加回对话面板，这个用例会变红。
 * 已交付的单文件成品（控制台与 AI 工具）同样已合为唯一的结合版 Muse，
 * 由 /tmp/d2_single_assistant_check.js 做成品级断言。
 */

import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ChatPanel } from './ChatPanel'
import { ASSISTANT_TAGLINE } from '../lib/assistant'
import { UNKNOWN_PROVIDER } from '../lib/providerStatus'

/** 测试环境没有 localStorage（Node 提示 --localstorage-file 未提供），补一个内存实现。 */
function memoryStorage(): Storage {
  const data = new Map<string, string>()
  return {
    get length() {
      return data.size
    },
    clear: () => data.clear(),
    getItem: (key: string) => data.get(key) ?? null,
    key: (index: number) => [...data.keys()][index] ?? null,
    removeItem: (key: string) => {
      data.delete(key)
    },
    setItem: (key: string, value: string) => {
      data.set(key, String(value))
    },
  } as Storage
}

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

beforeEach(() => {
  vi.stubGlobal('localStorage', memoryStorage())
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) === '/v1/models') return json({ models: [], default_id: null })
      return json({})
    }),
  )
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('控制台只有一个助手', () => {
  test('对话面板只出现一个助手身份：贾维斯，定位语同时点到 Muse 的能力与小龙虾的风格', async () => {
    render(<ChatPanel provider={UNKNOWN_PROVIDER} onConfigureModel={vi.fn()} />)

    expect(await screen.findByText('贾维斯')).toBeTruthy()
    expect(screen.getAllByText('贾维斯')).toHaveLength(1)
    // 定位语来自唯一的助手定义 lib/assistant.ts（沉稳 = Muse 的能力，活泼亲切 = 小龙虾的风格）。
    expect(screen.getByText(ASSISTANT_TAGLINE)).toBeTruthy()
    expect(ASSISTANT_TAGLINE).toContain('沉稳')
    expect(ASSISTANT_TAGLINE).toContain('活泼亲切')
  })

  test('没有任何切换助手的入口：没有切换控件，也没有 Muse / 小龙虾两个名字', async () => {
    render(<ChatPanel provider={UNKNOWN_PROVIDER} onConfigureModel={vi.fn()} />)
    await screen.findByText('贾维斯')

    expect(screen.queryByRole('group', { name: /切换助手/ })).toBeNull()
    expect(screen.queryByRole('button', { name: /切换助手/ })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Muse' })).toBeNull()
    expect(screen.queryByRole('button', { name: '小龙虾' })).toBeNull()
    expect(screen.queryByText('小龙虾')).toBeNull()
    expect(screen.queryByText('Muse')).toBeNull()
  })
})
