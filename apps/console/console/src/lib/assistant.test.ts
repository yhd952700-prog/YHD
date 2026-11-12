/**
 * 单助手结合版回归（前端）：只有一个助手定义，没有可切换的 persona。
 *
 * 2026-11-12 用户纠正（最高优先级）：不要两个可切换的助手（Muse /
 * 小龙虾），只要两者的结合版。前端的唯一事实源是 `lib/assistant.ts`：
 * 名字只有一个（贾维斯，与后端 `DEFAULT_SYSTEM_PROMPT` 一致），快捷问题
 * 与工具箱都只有一套，全部走这一个助手；生成一律走后端真实模型。
 */

import { describe, expect, test } from 'vitest'
import { WORKBENCH_NAME_ZH } from '../brand'
import { resetWorkspaceNameForTest } from './workspaceName'
import {
  ASSISTANT_NAME,
  assistantName,
  ASSISTANT_QUICK_QUESTIONS,
  ASSISTANT_TAGLINE,
  ASSISTANT_TOOLS,
  assistantGreeting,
  buildToolPrompt,
} from './assistant'

describe('单助手定义', () => {
  test('名字只有一个：跟着可改的工作台名字走（Joseph 这份默认贾维斯）', () => {
    expect(ASSISTANT_NAME).toBe(WORKBENCH_NAME_ZH)
    expect(ASSISTANT_NAME).toBe('贾维斯')
  })

  test('运行时助手名就是当前工作台名（改名后跟着改；默认贾维斯）', () => {
    resetWorkspaceNameForTest()
    expect(assistantName()).toBe('贾维斯')
    expect(assistantGreeting(9, assistantName())).toContain('贾维斯')
  })

  test('问候语四个时段都称呼 Boss，且只出现唯一助手的名字', () => {
    for (const hour of [2, 8, 14, 20]) {
      const greeting = assistantGreeting(hour)
      expect(greeting).toContain('Boss')
      expect(greeting).toContain(ASSISTANT_NAME)
      // 问候语里不能出现另一个可轮换的名字。
      expect(greeting).not.toContain('小龙虾')
      expect(greeting).not.toContain('Muse')
    }
  })

  test('定位语同时点到 Muse 的能力与小龙虾的风格，但不展开成两个人', () => {
    expect(ASSISTANT_TAGLINE).toContain('沉稳')
    expect(ASSISTANT_TAGLINE).toContain('活泼亲切')
  })
})

describe('快捷问题：只有一套', () => {
  test('恰好一组快捷问题，没有按人格分流的第二组', () => {
    expect(ASSISTANT_QUICK_QUESTIONS.length).toBeGreaterThan(0)
    expect(new Set(ASSISTANT_QUICK_QUESTIONS).size).toBe(ASSISTANT_QUICK_QUESTIONS.length)
    for (const question of ASSISTANT_QUICK_QUESTIONS) {
      expect(question).not.toContain('小龙虾')
      expect(question).not.toContain('Muse')
    }
  })
})

describe('工具箱：只有一套，全部走后端', () => {
  test('恰好六个工具：总结、写邮件、润色、翻译、想点子、代码示例', () => {
    expect(ASSISTANT_TOOLS.map((tool) => tool.label)).toEqual([
      '总结',
      '写邮件',
      '润色',
      '翻译',
      '想点子',
      '代码示例',
    ])
    expect(ASSISTANT_TOOLS.map((tool) => tool.id)).toEqual([
      'summarize',
      'email',
      'polish',
      'translate',
      'brainstorm',
      'code',
    ])
  })

  test('每个工具的生成来源都是后端，没有本地编造的工具', () => {
    for (const tool of ASSISTANT_TOOLS) {
      expect(tool.source).toBe('backend')
      expect(tool.template).toContain('{text}')
    }
  })

  test('buildToolPrompt 把指令模板与用户内容拼成一条消息', () => {
    const tool = ASSISTANT_TOOLS.find((candidate) => candidate.id === 'summarize')
    expect(tool).toBeTruthy()
    const prompt = buildToolPrompt(tool!, '  本周完成了 A、B 两件事  ')
    expect(prompt).toContain('本周完成了 A、B 两件事')
    expect(prompt).toContain('总结')
    expect(prompt).not.toContain('{text}')
  })
})
