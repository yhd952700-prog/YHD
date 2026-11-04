/**
 * `mapProbeReason` 的回归测试。
 *
 * 连通性测试的 `reason` 是机器可读的英文枚举，界面必须把它翻译成"用户下一步该
 * 改什么"。这里把六种已知原因与两种回落路径钉死，避免以后改动把 `upstream_error`
 * 误标成"API Key 无效"这类错误归因。
 */

import { describe, expect, test } from 'vitest'
import { mapProbeReason } from './ModelManager'

describe('mapProbeReason', () => {
  test('成功（reason 为空）→ 带上耗时的连接成功', () => {
    expect(mapProbeReason('', 123.4, 'pong')).toBe('连接成功（123.4 ms）')
  })

  test.each([
    ['invalid_key', 'API Key 无效或没有权限（401/403）'],
    ['model_not_found', '模型名不存在或该端点不支持这个模型（404）'],
    ['unreachable', '端点不可达：检查 base_url、端口与网络（连接被拒绝/域名解析失败）'],
    ['timeout', '连接超时：端点响应太慢，或 base_url 不可达'],
    ['rate_limited', '被上游限流（429），请稍后重试'],
  ])('%s → 明确中文建议', (reason, expected) => {
    expect(mapProbeReason(reason, 10, '上游原文')).toBe(expected)
  })

  test('upstream_error → 原样展示后端 detail', () => {
    expect(mapProbeReason('upstream_error', 10, 'HTTP 500: upstream exploded')).toBe(
      'HTTP 500: upstream exploded',
    )
  })

  test('未知原因 → 优先展示后端 detail，缺失时保留原因', () => {
    expect(mapProbeReason('bad_gateway', 10, '网关返回 502')).toBe('网关返回 502')
    expect(mapProbeReason('bad_gateway', 10, '')).toBe('上游返回错误（bad_gateway）')
  })
})
