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
// 测试统一从控制台项目根启动，因此用 cwd + src 最稳（不要在这里写带
// 路径分隔符的项目目录名，见 tests/test_console_invariants.py 的单端口前缀门禁）。
const SRC = join(process.cwd(), 'src')

/**
 * 用户可见文案里禁止出现的内部标识 / 工程黑话。
 *
 * 每一条都对应一类"陌生人第一次用会看不懂"的东西：策略代号、脚本路径、
 * 层级词、机器字段、英文异常原文。新增规则时**先修文案再加规则** ——
 * 规则一旦进表，全库命中即红，门禁才有约束力。
 */
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
  { pattern: /\bprincipal\b/i, why: '工程字段 principal（应写"账号"）' },
  { pattern: /module_path/i, why: '插件内部字段' },
  { pattern: /state_change/i, why: '内部事件类型' },
  { pattern: /组织与任务/, why: '阶段名里的"组织"会被读成层级归属' },
  { pattern: /embedding/i, why: '向量模型的英文术语' },
  { pattern: /人类主权|admin 角色/, why: '内部角色术语' },
  { pattern: /ACCOUNT_/, why: '内部错误码' },
  { pattern: /CREDENTIAL_/, why: '内部错误码' },
  // ── 允许用户必须知道的文件名，禁止真实机密与绝对路径 ─────────────────
  // `model_config.key` / `model_registry.sqlite3` / `data-dir.txt` 这类
  // 「用户备份时必须一起带走」的文件名是**有意上屏**的，不是内部字段泄露。
  // 仍然禁止：真实密钥值、JWT 密钥、含用户名的绝对磁盘路径。
  { pattern: /[A-Za-z]:\\(?:Users|Documents and Settings)\\[^\\\s]+/i, why: '含用户名的绝对磁盘路径' },
  { pattern: /\/(?:home|Users)\/[^/\s]+/, why: '含用户名的绝对磁盘路径' },
  { pattern: /\bsk-[A-Za-z0-9_-]{16,}/, why: '真实密钥值' },
  { pattern: /\bAIza[0-9A-Za-z_-]{20,}/, why: '真实密钥值' },
  { pattern: /\bJWT_SECRET\b|\bSECRET_KEY\b/, why: 'JWT 密钥' },
  // ── 交付面审计追加：策略代号 / 脚本路径 / 层级词 / 机器串 ────────────
  { pattern: /fail-closed|fail-open/i, why: '策略实现术语' },
  { pattern: /\bPolicy\s*[A-Z]?-?\d+/i, why: '策略代号' },
  { pattern: /\bOD-\d+\b/, why: '内部决策编号' },
  { pattern: /[A-Za-z0-9_-]+\.py\b/, why: '脚本文件名' },
  { pattern: /\bscripts[\\/]/, why: '脚本路径' },
  { pattern: /主体/, why: '层级词（应写"账号"）' },
  { pattern: /主权/, why: '层级词（应写"人工确认"）' },
  { pattern: /内核/, why: '实现层术语（应写"系统/服务"）' },
  { pattern: /后端/, why: '工程术语（应写"本地服务"）' },
  { pattern: /\bSELECT\b/, why: 'SQL 语句' },
  { pattern: /\boutcome\b/i, why: '内部字段名（应写"结果"）' },
  { pattern: /\bJWT\b|\bBearer\b/i, why: '认证协议术语' },
  { pattern: /\bscope\b/i, why: '内部权限字段（应写"权限范围"）' },
  { pattern: /\btenant\b/i, why: '内部账号字段（应写"账号/组织"）' },
  { pattern: /\btoken\b/i, why: '认证字段 token（应写"登录凭证"）' },
  {
    pattern: /[A-Za-z_][A-Za-z0-9_]*\s*=\s*[^\s，。；、]+/,
    why: '键值形式的机器串',
  },
  {
    pattern:
      /Traceback|Exception|ENOENT|ECONNREFUSED|PermissionError|FileNotFoundError|Internal Server Error|Bad Gateway|Service Unavailable|Gateway Timeout|Not Found|Unauthorized|Forbidden/i,
    why: '英文异常原文',
  },
  { pattern: /\bError:|\bError\b/i, why: '英文异常原文' },
]

/** 显式标注：写了这句话的文案允许出现技术标识（给技术支持看）。 */
export const TECH_SUPPORT_MARKER = '这是给技术支持看的技术信息'

/**
 * 「机器标识漏到用户文案」的三类形态。单独成表，是因为它们允许被
 * `TECH_SUPPORT_MARKER` 显式豁免 —— 其余黑话没有任何豁免通道。
 */
export const MACHINE_ID_PATTERNS: Array<{ pattern: RegExp; why: string }> = [
  // 机器字段名仍禁止；但带扩展名的文件名（model_config.key）放行 ——
  // 负向前瞻让 `model_config` 后面紧跟 `.key` 时不命中。
  {
    pattern: /\b[a-z][a-z0-9]*_[a-z0-9_]+\b(?!\.[a-z0-9]+)/,
    why: '机器字段名（snake_case）',
  },
  // 小写开头、至少一段驼峰：nextSinceSeq / modelConfig。
  {
    pattern: /\b[a-z][a-z0-9]*(?:[A-Z][a-z0-9]+)+\b/,
    why: '技术标识（camelCase）',
  },
  // 用户不需要在界面上读到的数据 / 日志文件名。
  {
    pattern: /\b[A-Za-z0-9_-]+\.(?:json|db|log)\b/,
    why: '数据/日志文件名',
  },
]

const VISIBLE_ATTR =
  /(title|sub|label|hint|placeholder|emptyTitle|emptyHint|detail|alt|aria-label|summary)\s*[=:]\s*(?:'([^'\\]*(?:\\.[^'\\]*)*)'|"([^"\\]*(?:\\.[^"\\]*)*)"|`([^`$]*?)`)/g

/** JSX 文本节点（不含表达式）；允许跨行 —— 长文案经常是换行写的。 */
const JSX_TEXT = />([^<>{]+)</g

export function extractVisibleCopy(source: string): string[] {
  const found: string[] = []
  const stripped = stripComments(source)
  for (const match of stripped.matchAll(VISIBLE_ATTR)) {
    const value = match[2] ?? match[3] ?? match[4] ?? ''
    if (value.trim()) found.push(value)
  }
  for (const match of stripped.matchAll(JSX_TEXT)) {
    const value = match[1] ?? ''
    // 纯代码片段（如 "=> {" 的残留）没有中文时跳过，避免误报。
    if (!/[\u4e00-\u9fff]/.test(value)) continue
    // `>` / `<` 也是比较运算符：跨行扫描会把 `(x) => x.id === id` 这类代码夹进来。
    // 中文文案里不会出现 ASCII 的 = ( ) ` —— 出现即当代码片段丢弃。
    if (/[=()`]/.test(value)) continue
    found.push(value.trim())
  }
  // 写在 JSX 表达式里的文案（`{error ?? '…'}`、`setError('…')`）同样是用户能看到的，
  // 但既不是文本节点也不是可见属性。剥掉注释后按字符串字面量再扫一遍，只收含中文的，
  // 避免把类名、导入路径扫进来。
  for (const match of stripped.matchAll(STRING_LITERAL)) {
    const value = match[1] ?? match[2] ?? ''
    if (/[\u4e00-\u9fff]/.test(value)) found.push(value)
  }
  return found
}

/** 注释里写"后端/内核"是给维护者看的，不算上屏，扫之前先剥掉。 */
function stripComments(source: string): string {
  return (
    source
      .replace(/\/\*[\s\S]*?\*\//g, ' ')
      .replace(/^\s*\/\/.*$/gm, ' ')
      // 只用于相等比较的字面量是"标识符"而不是文案：例如 `label === '组织与任务'`
      // 是把服务端阶段名映射成中性写法，本身不会上屏。
      .replace(/(?:===|!==|==|!=)\s*(['"])[^'"\n]*\1/g, ' ')
  )
}

/** 单/双引号字符串。模板字符串不在此列：它能跨行，容易把整段 JSX 吞进来。 */
const STRING_LITERAL = /'([^'\\\n]*)'|"([^"\\\n]*)"|`([^`$\\\n]*)`/g

export function findBannedCopy(source: string): Array<{ text: string; why: string }> {
  const hits: Array<{ text: string; why: string }> = []
  for (const text of extractVisibleCopy(source)) {
    for (const rule of BANNED_UI_PATTERNS) {
      if (rule.pattern.test(text)) hits.push({ text, why: rule.why })
    }
    // 唯一豁免：文案自己写明了「这是给技术支持看的技术信息」。
    if (text.includes(TECH_SUPPORT_MARKER)) continue
    for (const rule of MACHINE_ID_PATTERNS) {
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

  test.each([
    ['<p title="当前为 fail-closed 模式" />', '策略实现术语'],
    ['<p title="依据 Policy C-7 拒绝" />', '策略代号'],
    ['<p title="请运行 scripts/register_human_identity.py" />', '脚本路径'],
    ['<p title="为人类主体授权" />', '层级词（应写"账号"）'],
    ['<p title="人类主权动作" />', '层级词（应写"人工确认"）'],
    ['<p title="内核状态正常" />', '实现层术语（应写"系统/服务"）'],
    ['<p title="后端没启动" />', '工程术语（应写"本地服务"）'],
    ['<p title="粘贴 Bearer 令牌" />', '认证协议术语'],
    ['<p title="子账号 scope 为空" />', '内部权限字段（应写"权限范围"）'],
    ['<p title="当前 tenant 不可见" />', '内部账号字段（应写"账号/组织"）'],
    ['<p title="token 已过期" />', '认证字段 token（应写"登录凭证"）'],
    ['<p title="account_id 缺失" />', '机器字段名（snake_case）'],
    ['<p title="请求失败 target=*" />', '键值形式的机器串'],
    ['<p title="ENOENT: no such file" />', '英文异常原文'],
    ['<p title="Error: something broke" />', '英文异常原文'],
  ])('新规则命中：%s', (source, why) => {
    expect(findBannedCopy(source).some((hit) => hit.why === why)).toBe(true)
  })

  test('允许用户必须知道的文件名（正例）', () => {
    // 这些文件名是备份说明的一部分：用户不带它们，已存的 Key 就解不开。
    const source =
      '<p title="备份必须同时带 data\\model_config.key 与 data\\model_registry.sqlite3，以及 data-dir.txt" />'
    expect(findBannedCopy(source)).toEqual([])
  })

  test('反证：next_since_seq 漏进用户文案必须报红', () => {
    const hits = findBannedCopy('<p>；记录较多，请用 next_since_seq 继续导出</p>')
    expect(hits.some((hit) => hit.why === '机器字段名（snake_case）')).toBe(true)
  })

  test('反证：camelCase 技术名与数据/日志文件名必须报红', () => {
    expect(
      findBannedCopy('<p>请检查 nextSinceSeq 字段</p>').some(
        (hit) => hit.why === '技术标识（camelCase）',
      ),
    ).toBe(true)
    for (const file of ['data.json', 'ledger.db', 'server.log']) {
      expect(
        findBannedCopy(`<p>删除 ${file} 后重试</p>`).some(
          (hit) => hit.why === '数据/日志文件名',
        ),
      ).toBe(true)
    }
  })

  test('显式标注为技术支持信息时放行', () => {
    const annotated = `<p>请把 data.json 发给支持人员（${TECH_SUPPORT_MARKER}）</p>`
    expect(findBannedCopy(annotated)).toEqual([])
  })

  test('真实密钥与含用户名的绝对路径仍然拦截（反例）', () => {
    expect(
      findBannedCopy('<p title="密钥是 sk-abcdefghijklmnop123456" />').some(
        (hit) => hit.why === '真实密钥值',
      ),
    ).toBe(true)
    expect(
      findBannedCopy(
        '<p title="配置文件在 C:\\Users\\Administrator\\liuhao\\model_config.key" />',
      ).some((hit) => hit.why === '含用户名的绝对磁盘路径'),
    ).toBe(true)
    expect(
      findBannedCopy('<p title="JWT_SECRET=abcdefghijklmnop" />').some(
        (hit) => hit.why === 'JWT 密钥',
      ),
    ).toBe(true)
  })

  test('跨行 JSX 文案与表达式里的字符串同样在扫描范围内', () => {
    const multiline = '<p className="os-hint">\n  后端没有启动，请稍后重试。\n</p>'
    expect(findBannedCopy(multiline).some((hit) => hit.why === '工程术语（应写"本地服务"）')).toBe(
      true,
    )
    const expression = "setError(ok ? '已保存' : '后端没有启动')"
    expect(findBannedCopy(expression).some((hit) => hit.why === '工程术语（应写"本地服务"）')).toBe(
      true,
    )
  })

  test('注释里的实现说明不算上屏文案', () => {
    const commented = '/** 后端会返回 kernel 状态，供内部排障。 */\nconst value = 1'
    expect(findBannedCopy(commented)).toEqual([])
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
