/**
 * 系统设置 · 软件更新 —— 「文档说有、界面没有」的自动拦截。
 *
 * 这条断言存在的唯一理由：交付说明里写了"系统设置已挂载软件更新卡片"，
 * 但曾经实测 12 张卡片里根本没有。渲染整页需要 Mock 模型管理、诊断导出等
 * 一堆与本次无关的端点，容易把断言淹没在 mock 噪音里；这里直接读源码结构，
 * 只回答三个问题：卡片在不在、面板挂没挂、没配置更新源时按钮是否禁用并给出原因。
 */

import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, test } from 'vitest'

const root = process.cwd()
const settings = readFileSync(join(root, 'src', 'pages', 'Settings.tsx'), 'utf-8')
const panel = readFileSync(join(root, 'src', 'components', 'UpgradePanel.tsx'), 'utf-8')

describe('系统设置 · 软件更新', () => {
  test('设置页确实导入并挂载了软件更新卡片', () => {
    expect(settings).toContain("from '../components/UpgradePanel'")
    expect(settings).toContain('title="软件更新"')
    expect(settings).toContain('<UpgradePanel />')
  })

  test('未配置更新源时按钮禁用，并把原因写在用户看得到的地方', () => {
    expect(panel).toContain('disabled={busy !== null || !sourceReady}')
    expect(panel).toContain('未配置更新源')
    expect(panel).toContain('手动更新（覆盖安装）')
  })

  test('三种检查结果都有明确文案：正在检查 / 已是最新 / 检查失败', () => {
    // 入口按钮与进行中文案。
    expect(panel).toContain("'检查更新'")
    expect(panel).toContain("'正在检查…'")
    // 已是最新。
    expect(panel).toContain("'当前已是最新版本。'")
    // 失败兜底：check.status 既不是 update_available 也不是 up_to_date 时给出可重试文案。
    expect(panel).toContain("'这次没有检查成功，请稍后重试。'")
  })
})
