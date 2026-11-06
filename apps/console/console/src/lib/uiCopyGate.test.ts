/**
 * 用户可见文案门禁。
 *
 * 只扫"会出现在界面上的字符串"：JSX 文本节点，以及 title / sub / label / hint /
 * placeholder / emptyTitle / emptyHint / detail 等用户可见属性。变量名、类型名、
 * 代码注释一律不算 —— 否则 `success` 这种字段名会把整库扫成一片红，门禁很快
 * 就会被"加白名单"绕过。
 */

import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative, sep } from 'node:path'
import { describe, expect, test } from 'vitest'

// vitest 在 jsdom 下把模块 URL 变成 http://，不能用 import.meta.url 定位源码。
// 测试统一从项目根（apps/console/console）启动，因此用 cwd + src 最稳。
const SRC = join(process.cwd(), 'src')

/** 用户可见文案里禁止出现的内部标识 / 工程黑话。 */
export const BANNED_UI_PATTERNS: Array<{ pattern: RegExp; why: string }> = [
  { pattern: /provider\s*=/, why: '键值形式的内部配置' },
  { pattern: /service\./i, why: '内部服务标识' },
  { pattern: /\bkernel\b/i, why: '内核英文名' },
  { pattern: /阻断性发现/, why: '安全扫描术语' },
  { pattern: /脱敏/, why: '数据处理术语' },
  { pattern: /未采样/, why: '数据处理术语' },
  { pattern: /\bsandbox\b/i, why: '运行环境标识' },
  { pattern: /\bsuccess\b/i, why: '机器状态值（应翻译成"成功"）' },
  { pattern: /\bprovider\b/i, why: '工程术语 provider（应写"服务商/模型服务"）' },
  { pattern: /\bprincipal\b/i, why: '工程字段 principal（应写"账号/主体"）' },
  { pattern: /module_path/i, why: '插件内部字段' },
  { pattern: /state_change/i, why: '内部事件类型' },
  { pattern: /组织与任务/, why: '阶段名里的"组织"会被读成层级归属' },
  { pattern: /embedding/i, why: '向量模型的英文术语' },
  { pattern: /人类主权|admin 角色/, why: '内部角色术语' },
  { pattern: /\.db\b/i, why: '数据库文件名' },
  { pattern: /ACCOUNT_/, why: '内部错误码' },
  { pattern: /CREDENTIAL_/, why: '内部错误码' },
]

const VISIBLE_ATTR =
  /(title|sub|label|hint|placeholder|emptyTitle|emptyHint|detail|alt|aria-label|summary)\s*[=:]\s*(?:'([^'\\]*(?:\\.[^'\\]*)*)'|"([^"\\]*(?:\\.[^"\\]*)*)"|`([^`$]*?)`)/g

/** JSX 文本节点（不含表达式）。 */
const JSX_TEXT = />([^<>{}\n][^<>{}\n]*)</g

export function extractVisibleCopy(source: string): string[] {
  const found: string[] = []
  for (const match of source.matchAll(VISIBLE_ATTR)) {
    const value = match[2] ?? match[3] ?? match[4] ?? ''
    if (value.trim()) found.push(value)
  }
  for (const match of source.matchAll(JSX_TEXT)) {
    const value = match[1] ?? ''
    // 纯代码片段（如 "=> {" 的残留）没有中文时跳过，避免误报。
    if (/[\u4e00-\u9fff]/.test(value)) found.push(value.trim())
  }
  return found
}

export function findBannedCopy(source: string): Array<{ text: string; why: string }> {
  const hits: Array<{ text: string; why: string }> = []
  for (const text of extractVisibleCopy(source)) {
    for (const rule of BANNED_UI_PATTERNS) {
      if (rule.pattern.test(text)) hits.push({ text, why: rule.why })
    }
  }
  return hits
}

function walk(dir: string, files: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry)
    const stats = statSync(full)
    if (stats.isDirectory()) {
      walk(full, files)
      continue
    }
    if (!/\.(ts|tsx)$/.test(entry)) continue
    if (/\.test\.(ts|tsx)$/.test(entry)) continue
    files.push(full)
  }
  return files
}

describe('用户可见文案门禁', () => {
  test('门禁本身能抓到违规文案（防止规则失效）', () => {
    const planted = 'const node = <Card title="阻断性发现: provider=ollama" />'
    const hits = findBannedCopy(planted)
    expect(hits.some((hit) => hit.why === '键值形式的内部配置')).toBe(true)
  })

  test('pages / components / App 与展示层 lib 中不出现内部标识', () => {
    const targets = walk(SRC).filter((file) => {
      const rel = relative(SRC, file).split(sep).join('/')
      return (
        rel.startsWith('pages/') ||
        rel.startsWith('components/') ||
        rel === 'App.tsx' ||
        rel === 'lib/format.ts' ||
        rel === 'lib/honesty.ts' ||
        rel === 'lib/providerStatus.ts' ||
        rel === 'lib/nav.ts'
      )
    })
    expect(targets.length).toBeGreaterThan(10)

    const violations: string[] = []
    for (const file of targets) {
      const hits = findBannedCopy(readFileSync(file, 'utf8'))
      for (const hit of hits) {
        violations.push(`${relative(SRC, file)} → 「${hit.text}」(${hit.why})`)
      }
    }
    expect(violations).toEqual([])
  })
})
