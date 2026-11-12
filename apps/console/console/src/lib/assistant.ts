/**
 * 唯一的助手定义（单助手结合版，前端唯一事实源）。
 *
 * 2026-11-12 用户纠正（最高优先级，覆盖之前"可切换 Muse / 小龙虾"的
 * 要求）：不要两个可切换的助手，只要 Muse 与小龙虾的结合版。因此这里
 * 只有**一份**助手定义，没有 persona 切换分支，没有"切到另一个人格"
 * 的入口：
 *
 * * 名字只有一个：助手就叫当前设置的工作台名字（`WORKBENCH_NAME_ZH`，
 *   Joseph 这份默认贾维斯），跟着可改名配置走，不再写死；后端
 *   ``DEFAULT_SYSTEM_PROMPT``（见 ``src/ai/liuhao.py``）目前同样只认
 *   贾维斯这一个名字，G 组落地运行时改名后由后端跟着配置读；
 *   Muse 与小龙虾是这个助手的"能力来源 + 风格来源"，不是两个可轮换的名字；
 * * 能力像 Muse：沉稳、能干，写邮件/总结/翻译/想点子/做清单都行；
 * * 说话风格融合小龙虾的活泼亲切：热情、口语化、有温度，但不失分寸，
 *   不一本正经，也不只卖萌；
 * * 快捷问题与工具箱都只有一套，全部走这一个助手；
 * * 生成一律走后端真实模型（``POST /v1/chat/stream``）：工具箱只是把
 *   指令模板 + 用户内容拼成一条消息发给同一个助手；未配置模型时后端
 *   只返回"未配置模型，请先配置模型密钥"，不编两种风格的回复。
 */

import type { ActiveProviderStatus } from './providerStatus'
import { getAssistantName } from './workspaceName'
import { WORKBENCH_NAME_ZH } from '../brand'

/** 唯一助手的名字（出厂默认）：顶栏、欢迎语、问候里只出现这一个。 */
export const ASSISTANT_NAME = WORKBENCH_NAME_ZH

/**
 * 运行时助手名：就是当前工作台的中文名（见 `lib/workspaceName.ts`，
 * 全站唯一的名字来源）。用户在设置里改了工作台名字，助手名跟着改；
 * 服务端还没返回时就是出厂默认 `ASSISTANT_NAME`。
 */
export function assistantName(): string {
  return getAssistantName()
}

/** 一句话定位：能力（Muse）+ 风格（小龙虾）都点到，但不展开成两个人。 */
export const ASSISTANT_TAGLINE = '沉稳能干，活泼亲切——写邮件、总结、翻译、想点子都行'

/** 首屏问候：按本地时间打招呼，称呼固定为 Boss，语气是结合版的活泼亲切。 */
export function assistantGreeting(hour: number, name: string = ASSISTANT_NAME): string {
  if (hour >= 5 && hour < 12) return `早上好，Boss！我是${name}，新的一天想让我先帮你搞定什么？`
  if (hour >= 12 && hour < 18) return `下午好，Boss！我是${name}，有什么想让我搭把手的？`
  if (hour >= 18 && hour < 23) return `晚上好，Boss！我是${name}，今天还剩点什么想交给我？`
  return `夜深了，Boss，我是${name}，还在呢——有什么想让我帮你收个尾？`
}

/**
 * 快捷问题：只有一套，全部走唯一助手。都是能直接发送的开场白，不按
 * 人格分流（以前"市场分析/销售机会…"那套与助手无关的 chips 已删掉）。
 */
export const ASSISTANT_QUICK_QUESTIONS = [
  '今天有什么待办？',
  '帮我写一封工作邮件',
  '总结一下这周的进展',
  '帮我想几个新点子',
  '给我一段 Python 入门示例',
]

/** 工具箱的一项：模板里的 `{text}` 是用户在输入框里的内容。 */
export interface AssistantTool {
  id: string
  label: string
  /** 悬停/无障碍说明：这一步要用户提供什么。 */
  hint: string
  /** 发给助手的指令模板；`{text}` 处填用户内容。 */
  template: string
  /**
   * 生成来源：全部是 `backend` —— 模板拼好后作为一条普通消息走
   * `POST /v1/chat/stream`，由后端真实模型生成；没有任何工具在本地
   * 编造结果。未配置模型时同样走后端，后端只返回未配置提示。
   */
  source: 'backend'
}

/** 工具箱：只有一套（总结、写邮件、润色、翻译、想点子、代码示例）。 */
export const ASSISTANT_TOOLS: AssistantTool[] = [
  {
    id: 'summarize',
    label: '总结',
    hint: '把输入框里的内容提炼成要点',
    template: '请总结以下内容，提炼出要点（不超过 5 条），最后给一句话结论：\n\n{text}',
    source: 'backend',
  },
  {
    id: 'email',
    label: '写邮件',
    hint: '按输入框里的要点写一封得体的邮件',
    template: '请根据以下要点写一封得体的邮件：称呼、正文、结尾都要有，语气亲切但不失分寸：\n\n{text}',
    source: 'backend',
  },
  {
    id: 'polish',
    label: '润色',
    hint: '把输入框里的文字改得更通顺得体',
    template: '请润色以下文字：更通顺、得体，保持原意，不要改变事实：\n\n{text}',
    source: 'backend',
  },
  {
    id: 'translate',
    label: '翻译',
    hint: '把输入框里的内容译成英文（已是英文则译成中文）',
    template: '请把以下内容翻译成英文；如果原文已经是英文，就翻译成中文：\n\n{text}',
    source: 'backend',
  },
  {
    id: 'brainstorm',
    label: '想点子',
    hint: '围绕输入框里的主题想几个点子',
    template: '请围绕以下主题想 5 个点子，每个点子一句话说清亮点，再标出你最推荐的一个：\n\n{text}',
    source: 'backend',
  },
  {
    id: 'code',
    label: '代码示例',
    hint: '按输入框里的需求给一段能直接用的代码',
    template: '请针对以下需求给一段能直接用的代码示例，并用两三句话解释关键步骤：\n\n{text}',
    source: 'backend',
  },
]

/** 把工具模板 + 用户内容拼成一条发给助手的消息。 */
export function buildToolPrompt(tool: AssistantTool, text: string): string {
  return tool.template.replace('{text}', text.trim())
}

/** 未配置模型时工具箱的统一说明：不编结果，只指路。 */
export const TOOLBOX_UNCONFIGURED_NOTE =
  '工具箱走后端模型生成；未配置模型，请先配置模型密钥，配置好之后再点工具。'

/** 给操作台页用的助手状态说明（与 ChatPanel 共用同一份判据）。 */
export function assistantStatusNote(provider: ActiveProviderStatus): string {
  return provider.isReal ? '' : TOOLBOX_UNCONFIGURED_NOTE
}
