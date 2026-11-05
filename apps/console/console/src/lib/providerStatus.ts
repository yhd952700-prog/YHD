/**
 * 当前生效 provider 的**唯一**前端判据。
 *
 * 为什么集中到这里
 * ----------------
 * 模型状态曾在三处互相矛盾：`/v1/health` 说 mock，总览 provider 面板从名册
 * `providers.mode` 自己推导，`/v1/dashboard/summary` 又是另一份快照。三份数据
 * 三个时间点，谁也不知道该信哪个。
 *
 * 现在的规则：**只读 `/v1/health` 的 `ai_provider`**。名册只回答"各项配没配
 * 密钥"，不再提供真实性判据；summary 的 provider 字段前端不再消费。
 * 这样"现在到底是不是真实模型"永远只有一个答案。
 */

import type { ProviderInfo } from './contracts'
import { providerPosture } from './honesty'
import type { ProviderPosture } from './honesty'

export interface ActiveProviderStatus {
  provider: ProviderInfo | null
  posture: ProviderPosture
  mode: string
  /** 真实 provider 且未开模拟路径。 */
  isReal: boolean
  /** 内置 mock（默认开发态）：回复是编造的，不是真实模型输出。 */
  isMock: boolean
  /** 给人看的一句话结论（短）。 */
  label: string
  /** 补充说明（为什么、下一步能做什么）。 */
  detail: string
  error: string | null
  loading: boolean
  reload: () => void
}

function postureOf(provider: ProviderInfo | null): ProviderPosture {
  return providerPosture({
    mode: provider?.mode ?? null,
    simulated: provider?.simulated ?? null,
  })
}

/**
 * 把 `/v1/health` 的 `ai_provider` 翻译成界面可用的状态。
 *
 * `mode` 缺失 = 不知道，绝不默认成 real（同 `honesty.providerPosture` 的原则）。
 */
export function deriveProviderStatus(input: {
  provider?: ProviderInfo | null
  error?: string | null
  loading?: boolean
  reload?: () => void
}): ActiveProviderStatus {
  const provider = input.provider ?? null
  const mode = (provider?.mode ?? '').trim().toLowerCase()
  const posture = postureOf(provider)
  const isReal = posture.isReal
  const isMock = mode === 'mock'

  let label: string
  let detail: string
  if (!provider) {
    label = '模型状态未知'
    detail = input.error
      ? `无法读取 /v1/health：${input.error}`
      : '正在读取 /v1/health…'
  } else if (mode === 'invalid') {
    label = 'Provider 配置无效'
    detail = provider.error || '后端报告的 provider 类型不受支持，请到模型管理修正。'
  } else if (isReal) {
    label = '真实模型'
    detail = `当前生效 provider：${provider.type ?? '—'}`
  } else if (isMock) {
    label = '模拟回复（未配置真实模型）'
    detail =
      '后端当前使用内置 mock provider：不会调用任何真实模型，回复是编造内容。请先配置真实模型。'
  } else if (posture.simulated) {
    label = '模拟执行'
    detail = posture.verdict
  } else {
    label = '模型状态无法判定'
    detail = posture.verdict
  }

  return {
    provider,
    posture,
    mode,
    isReal,
    isMock,
    label,
    detail,
    error: input.error ?? provider?.error ?? null,
    loading: input.loading === true,
    reload: input.reload ?? (() => {}),
  }
}

/** `/v1/health` 尚未返回时的安全初值 —— 不假装"真实可用"。 */
export const UNKNOWN_PROVIDER = deriveProviderStatus({})
