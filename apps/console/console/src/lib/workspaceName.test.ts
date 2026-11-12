import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import {
  getAssistantName,
  getWorkspaceName,
  refreshWorkspaceName,
  resetWorkspaceNameForTest,
  setWorkspaceNameLocal,
} from './workspaceName'

describe('工作台名字（全站唯一的名字来源）', () => {
  beforeEach(() => {
    resetWorkspaceNameForTest()
    vi.unstubAllGlobals()
  })

  afterEach(() => {
    resetWorkspaceNameForTest()
    vi.unstubAllGlobals()
  })

  test('默认就是贾维斯 / Jarvis，助手名就是工作台中文名', () => {
    expect(getWorkspaceName().lockup).toBe('Jarvis · 贾维斯')
    expect(getWorkspaceName().customized).toBe(false)
    expect(getAssistantName()).toBe('贾维斯')
  })

  test('后端返回改后的名字，store 与助手名一起跟着变', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        json: async () => ({
          name_en: '阿强',
          name_zh: '阿强',
          lockup: '阿强',
          customized: true,
        }),
      })),
    )
    const next = await refreshWorkspaceName()
    expect(next.lockup).toBe('阿强')
    expect(next.customized).toBe(true)
    expect(getWorkspaceName().name_zh).toBe('阿强')
    expect(getAssistantName()).toBe('阿强')
  })

  test('后端不可达时保持出厂默认，不抛错', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => {
      throw new Error('unreachable')
    }))
    const next = await refreshWorkspaceName()
    expect(next.lockup).toBe('Jarvis · 贾维斯')
    expect(getAssistantName()).toBe('贾维斯')
  })

  test('设置页保存后直接写入 store，界面立即刷新', () => {
    setWorkspaceNameLocal({
      name_en: 'Friday',
      name_zh: '星期五',
      lockup: 'Friday · 星期五',
      customized: true,
    })
    expect(getWorkspaceName().lockup).toBe('Friday · 星期五')
    expect(getAssistantName()).toBe('星期五')
  })
})
