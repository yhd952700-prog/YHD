/**
 * 禁止在 `src/lib/format.ts` 之外直调 `.toFixed(`。
 *
 * 背景：后端漏发数值字段时，`undefined.toFixed()` 会抛
 *   TypeError: Cannot read properties of undefined (reading "toFixed")
 * 整个页面白屏。展示层的唯一出口是 `lib/format.ts` 的 `numberText()`：
 * 非有限值显示「—」，真实 0 仍显示 0。
 *
 * 用法：
 *   node scripts/check-no-direct-tofixed.mjs            # 扫描 src/
 *   node scripts/check-no-direct-tofixed.mjs --file x  # 扫描单个文件（反证用）
 *
 * 注释里的示例不计入（先剥离注释，保持行号不变）。
 */

import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const ALLOWED = 'src/lib/format.ts'
const DIRECT = /\.toFixed\s*\(/

/** 把注释替换成等长空白，保留行号；字符串里的示例无法区分，测试夹具需自行拆分。 */
function stripComments(text) {
  return text
    .replace(/\/\*[\s\S]*?\*\//g, (block) => block.replace(/[^\n]/g, ' '))
    .replace(/(^|[^:])\/\/[^\n]*/g, (match, prefix) => prefix + ' '.repeat(match.length - prefix.length))
}

export function scanText(filePath, text) {
  const normalized = filePath.split('\\').join('/')
  if (normalized.endsWith(ALLOWED)) return []
  const lines = stripComments(text).split(/\r?\n/)
  const violations = []
  lines.forEach((line, index) => {
    if (DIRECT.test(line)) {
      violations.push({ file: normalized, line: index + 1, source: line.trim() })
    }
  })
  return violations
}

export function scanRepo(root) {
  const src = resolve(root, 'src')
  const violations = []
  const walk = (dir) => {
    for (const name of readdirSync(dir)) {
      const full = join(dir, name)
      if (statSync(full).isDirectory()) {
        walk(full)
        continue
      }
      if (!/\.(ts|tsx)$/.test(name)) continue
      const rel = relative(root, full).split('\\').join('/')
      violations.push(...scanText(rel, readFileSync(full, 'utf8')))
    }
  }
  walk(src)
  return violations
}

const isMain =
  process.argv[1] && resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url))

if (isMain) {
  const fileFlag = process.argv.indexOf('--file')
  const single = fileFlag >= 0 ? process.argv[fileFlag + 1] : null
  const violations = single
    ? scanText(relative(process.cwd(), resolve(single)), readFileSync(resolve(single), 'utf8'))
    : scanRepo(process.cwd())

  if (violations.length === 0) {
    console.log('[toFixed] OK：只有 src/lib/format.ts 可以直调 .toFixed')
    process.exit(0)
  }

  console.error('[toFixed] 禁止直调 .toFixed，请改用 lib/format.ts 的 numberText()：')
  for (const item of violations) {
    console.error(`  ${item.file}:${item.line}  ${item.source}`)
  }
  process.exit(1)
}
