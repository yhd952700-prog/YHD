/**
 * 模型状态单一事实源的契约。
 *
 * 这些用例对应 P0 缺陷：未配真实模型时界面必须明说"模拟回复"，不能因为
 * `/v1/health` 还没返回就默认"真实可用"。
 */

import { describe, expect, test } from 'vitest'
import { deriveProviderStatus, UNKNOWN_PROVIDER } from './providerStatus'

describe('deriveProviderStatus', () => {
  test('内置 mock：明确标注尚未接入真实模型', () => {
    const status = deriveProviderStatus({
      provider: { type: 'mock', mode: 'mock', simulated: false },
    })
    expect(status.isReal).toBe(false)
    expect(status.isMock).toBe(true)
    expect(status.label).toBe('尚未接入真实模型')
    expect(status.detail).toContain('演示内容')
    expect(status.posture.tone).toBe('danger')
  })

  test('真实 provider：只有 mode=real 且未开模拟才算真实', () => {
    const status = deriveProviderStatus({
      provider: { type: 'openai', mode: 'real', simulated: false },
    })
    expect(status.isReal).toBe(true)
    expect(status.label).toBe('真实模型')
  })

  test('真实 provider + 模拟执行：不得标成真实', () => {
    const status = deriveProviderStatus({
      provider: { type: 'openai', mode: 'real', simulated: true },
    })
    expect(status.isReal).toBe(false)
    expect(status.label).toBe('模拟执行')
  })

  test('配置无效：给出明确错误而不是静默降级', () => {
    const status = deriveProviderStatus({
      provider: { type: 'unknown-provider', mode: 'invalid', error: 'unsupported provider' },
    })
    expect(status.label).toBe('模型服务配置无效')
    expect(status.detail).toContain('unsupported provider')
  })

  test('health 未返回：诚实地说未知，不默认成真实', () => {
    expect(UNKNOWN_PROVIDER.isReal).toBe(false)
    expect(UNKNOWN_PROVIDER.label).toBe('模型状态未知')
  })

  test('health 请求失败：诚实说读不到，并给出下一步', () => {
    const status = deriveProviderStatus({ error: '请求失败（500）' })
    expect(status.label).toBe('模型状态未知')
    expect(status.detail).toContain('暂时无法读取')
    expect(status.detail).toContain('稍后重试')
  })

  test('mode 缺失：不把它当成 mock，也不当成真实', () => {
    const status = deriveProviderStatus({ provider: { type: 'ollama' } })
    expect(status.isMock).toBe(false)
    expect(status.isReal).toBe(false)
    expect(status.label).toBe('模型状态无法判定')
  })
})
