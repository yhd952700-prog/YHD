/**
 * provider 诚实性判据 —— "我看到的输出是真的吗" 的唯一翻译处。
 *
 * 为什么需要这个文件
 * ------------------
 * `configured` 回答的是"环境变量/密钥到位了吗"，**不回答"这是不是假 AI"**。
 * 内置 `mock` provider 无需密钥，所以它 `configured === true`；前端曾据此把
 * 它渲染成"已配置，可用于真实调用" + 绿色"就绪"徽标。
 *
 * 后果不是"文案不好看"，而是**一个正在被 mock 服务的部署看起来和一个接了真
 * 模型的部署完全一样**。后端其实一直在如实播报（`mode` / `simulated`，见
 * `src/ai/providers.py` 的 `ai_provider_status()`，注释原文"Never silently
 * pretends a simulation is real"），但名册端点没带这两个字段，前端类型也没声明，
 * 于是这层信息**从未抵达过人的眼睛**。
 *
 * 所以规则只有一条，且必须落在共享函数里而不是散落在各页面：
 *   `configured` 决定**能否接通**（色调/徽标）
 *   `mode`/`simulated` 决定**接通之后拿到的是不是真的**（措辞/警示）
 * 任何一处把前者当成后者，就是同一个谎话换个地方重说。
 */

import type { Tone } from './format'

/** 一个 provider 的诚实性状态。 */
export interface ProviderPosture {
  /** 会不会产生真实调用。`true` = 假（mock / 空 / none）。 */
  isMock: boolean
  /** 执行内核是否走模拟路径。 */
  simulated: boolean
  /** 只有"真实 provider 且未开模拟"才是真跑。 */
  isReal: boolean
  /** 一句话说清现状（中文，面向人）。 */
  verdict: string
  /** 该用哪个色调渲染。假/模拟一律 warn 及以上，绝不给绿。 */
  tone: Tone
  /** 徽标文字。 */
  badge: string
}

/**
 * 从后端播报的字段算出诚实性状态。
 *
 * **未知就是未知**：`mode` 缺失时按"无法判定"处理（`isReal=false`，措辞说明
 * 原因），绝不默认当作 real。理由同全仓的诚实性原则 —— 一个没说清楚的东西
 * 显示成绿色，比显示成警告危险得多。
 */
export function providerPosture(input: {
  mode?: string | null
  simulated?: boolean | null
}): ProviderPosture {
  const mode = (input.mode ?? '').trim().toLowerCase()
  const simulated = input.simulated === true
  const isMock = mode === 'mock' || mode === '' || mode === 'none'
  const unknown = mode === ''
  const isReal = !isMock && !simulated && mode === 'real'

  if (simulated && isMock) {
    return {
      isMock,
      simulated,
      isReal,
      verdict: '内置 mock provider + 执行内核模拟已开启：输出为编造，不产生任何真实调用',
      tone: 'danger',
      badge: '模拟输出',
    }
  }
  if (isMock) {
    return {
      isMock,
      simulated,
      isReal,
      verdict: unknown
        ? '后端未播报 provider 诚实性判据，无法判定是否为真实调用'
        : '内置 mock provider：不调用真实模型，返回编造结果',
      tone: 'danger',
      badge: unknown ? '真实性未知' : '模拟',
    }
  }
  if (simulated) {
    return {
      isMock,
      simulated,
      isReal,
      verdict: 'provider 是真实的，但执行内核显式开启了模拟路径：结果并非真实执行产物',
      tone: 'danger',
      badge: '模拟执行',
    }
  }
  if (isReal) {
    return {
      isMock,
      simulated,
      isReal,
      verdict: '真实 provider，未开启模拟：调用会产生真实结果',
      tone: 'ok',
      badge: '真实',
    }
  }
  // mode 存在但不是 mock/real —— 后端扩展了新取值。如实说不知道，不硬套。
  return {
    isMock,
    simulated,
    isReal,
    verdict: `后端播报了未知的 provider 模式「${mode}」，无法判定真实性`,
    tone: 'warn',
    badge: '无法判定',
  }
}

/** `Overview` 的 provider 行副标题：把"能否接通"与"是否真实"分开陈述。 */
export function providerRowSubtitle(input: {
  configured: boolean
  requiredEnv: string[]
  posture: ProviderPosture
}): string {
  if (!input.configured) {
    return input.requiredEnv.length > 0
      ? `缺少密钥：${input.requiredEnv.join(' / ')}`
      : '无需密钥'
  }
  // configured 只说明"环境齐了"。接通之后是真是假，必须另说一句 —— 一句都不能省。
  return input.posture.verdict
}