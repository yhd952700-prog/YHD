/**
 * `mapProbeReason` 的回归测试。
 *
 * 连通性测试的 `reason` 是机器可读的英文枚举，界面必须把它翻译成"用户下一步该
 * 改什么"。这里把六种已知原因与两种回落路径钉死，避免以后改动把 `upstream_error`
 * 误标成"API Key 无效"这类错误归因。
 */

import { describe, expect, test } from 'vitest'
import { canBecomeDefault, mapProbeReason, presetFormPatch } from './ModelManager'

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
