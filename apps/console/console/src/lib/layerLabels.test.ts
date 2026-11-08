/**
 * 回归测试：界面上的能力层 / 阶段标签必须是中性说法。
 *
 * 名册接口下发的是注册表原文（「认知内核」「内核闭环」），总览页直接渲染就会
 * 把实现层术语铺到用户面前。这个文件锁定两件事：已知标签有固定映射，
 * 未知标签（包括未登记但含「内核」的）有兜底，且任何输出都不含「内核」。
 */

import { describe, expect, test } from 'vitest'
import {
  capabilityModuleLabel,
  capabilityStatusLabel,
  capabilityStatusTone,
  neutralLayerLabel,
  phaseLabel,
} from './layerLabels'

describe('neutralLayerLabel', () => {
  test.each([
    ['认知内核', '能力'],
    ['执行内核', '任务执行'],
    ['治理内核', '规则'],
    ['信任内核', '校验'],
    ['内核闭环', '运行闭环'],
    ['组织与任务', '协作与任务'],
    ['世界与治理', '外部协作'],
    ['验证与进化', '验证与改进'],
  ])('已知标签：%s → %s', (label, expected) => {
    expect(neutralLayerLabel(label)).toBe(expected)
  })

  test('未登记但含「内核」的标签：替换为「能力」', () => {
    expect(neutralLayerLabel('网络内核')).toBe('网络能力')
  })

  test('替换后只剩「能力」或为空时归入「其他能力」', () => {
    expect(neutralLayerLabel('内核')).toBe('其他能力')
    expect(neutralLayerLabel(' 内核 ')).toBe('其他能力')
  })

  test('未知中文标签原样返回', () => {
    expect(neutralLayerLabel('增长引擎')).toBe('增长引擎')
  })

  test('空值返回「未分组」', () => {
    expect(neutralLayerLabel(undefined)).toBe('未分组')
    expect(neutralLayerLabel('')).toBe('未分组')
    expect(neutralLayerLabel('   ')).toBe('未分组')
  })

  test('任何输入的输出都不含「内核」', () => {
    const inputs = [
      '认知内核',
      '执行内核',
      '治理内核',
      '信任内核',
      '内核闭环',
      '组织与任务',
      '世界与治理',
      '验证与进化',
      '网络内核',
      '内核',
      '网络内核与协议适配',
      '增长引擎',
      '',
      undefined,
    ]
    for (const input of inputs) {
      expect(neutralLayerLabel(input)).not.toContain('内核')
    }
  })

  test('注册表英文模块名按稳定键翻成中文，不把英文原名上屏', () => {
    expect(capabilityModuleLabel('identity', 'Identity Kernel — Agent identity')).toBe('身份与权限')
    expect(capabilityModuleLabel('memory', 'Memory Kernel — Long-term memory')).toBe('记忆存储')
    expect(capabilityModuleLabel('security', 'Security Kernel')).toBe('安全控制')
    expect(capabilityModuleLabel('audit', 'Audit Kernel — Append-only trail')).toBe('审计留痕')
    expect(capabilityModuleLabel('plugin', 'Plugin Kernel — Extension loading')).toBe('插件扩展')
    expect(capabilityModuleLabel('workflow', 'Workflow Kernel')).toBe('能力模块')
    expect(capabilityModuleLabel('', 'Workflow Kernel')).toBe('能力模块')
    expect(capabilityModuleLabel('unknown', '增长引擎')).toBe('增长引擎')
  })
})

describe('能力状态与阶段', () => {
  test.each([
    ['IMPLEMENTED', '已具备'],
    ['implemented', '已具备'],
    ['PARTIAL', '部分具备'],
    ['PARTIALLY_IMPLEMENTED', '部分具备'],
    ['NOT_IMPLEMENTED', '未提供'],
    ['', '状态未知'],
    [undefined, '状态未知'],
  ])('状态 %s → %s', (status, expected) => {
    expect(capabilityStatusLabel(status)).toBe(expected)
  })

  test('徽章色调与状态口径一致', () => {
    expect(capabilityStatusTone('IMPLEMENTED')).toBe('ok')
    expect(capabilityStatusTone('PARTIAL')).toBe('warn')
    expect(capabilityStatusTone('NOT_IMPLEMENTED')).toBe('muted')
  })

  test('Phase 9 显示为「第 9 阶段」，不把英文阶段名上屏', () => {
    expect(phaseLabel('Phase 9')).toBe('第 9 阶段')
    expect(phaseLabel('第 9 阶段')).toBe('第 9 阶段')
    expect(phaseLabel('')).toBe('—')
    expect(phaseLabel(undefined)).toBe('—')
  })
})
