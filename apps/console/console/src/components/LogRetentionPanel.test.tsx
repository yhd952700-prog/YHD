/**
 * 系统设置 · 日志与磁盘 —— 「用户看得见、改得了」的自动拦截。
 *
 * 渲染整页要 mock 十来个与本次无关的端点，断言容易被噪音淹没；这里直接读源码，
 * 只回答：卡片挂没挂、三个端点对不对、文案有没有说清「只删旧备份」。
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, test } from 'vitest'

const root = process.cwd()
const settings = readFileSync(join(root, 'src', 'pages', 'Settings.tsx'), 'utf-8')
const panel = readFileSync(join(root, 'src', 'components', 'LogRetentionPanel.tsx'), 'utf-8')

describe('系统设置 · 日志与磁盘', () => {
  test('设置页确实导入并挂载了日志卡片', () => {
    expect(settings).toContain("from '../components/LogRetentionPanel'")
    expect(settings).toContain('title="日志与磁盘"')
    expect(settings).toContain('<LogRetentionPanel canManage={canManageSettings} />')
  })

  test('读取、保存、立即清理三个端点都在', () => {
    expect(panel).toContain("'/v1/settings/logs'")
    expect(panel).toContain("'/v1/settings/logs/prune'")
    expect(panel).toContain("method: 'PUT'")
    expect(panel).toContain("method: 'POST'")
  })

  test('展示占用与生效中的保留值，并说明只删旧备份', () => {
    expect(panel).toContain('日志占用')
    expect(panel).toContain('保留')
    expect(panel).toContain('正在写入的日志不会删除')
    expect(panel).toContain('立即清理')
    expect(panel).toContain('保存设置')
  })

  test('环境变量覆盖时明确提示改了不生效', () => {
    expect(panel).toContain('env_override')
    expect(panel).toContain('正被环境变量覆盖')
  })

  test('成员视角只读，两张卡片都说明由主账号统一管理', () => {
    expect(settings).toContain('const canManageSettings = session.isRoot')
    expect(settings).toContain('该设置由主账号统一管理')
    expect(panel).toContain('canManage = true')
    expect(panel).toContain('disabled={!canManage')
    expect(panel).toContain('该设置由主账号统一管理')
  })
})
