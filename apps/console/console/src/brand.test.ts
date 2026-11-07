import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, test } from 'vitest'
import { BRAND_LOCKUP, BRAND_NAME_EN, BRAND_NAME_ZH } from './brand'

describe('品牌单一来源', () => {
  test('lockup 由英文名与中文名组成', () => {
    expect(BRAND_NAME_EN).toBe('LiuHao AI OS')
    expect(BRAND_NAME_ZH).toBe('鎏灏智能中枢')
    expect(BRAND_LOCKUP).toBe(`${BRAND_NAME_EN} · ${BRAND_NAME_ZH}`)
  })

  test('index.html 与 manifest 使用同一套主名', () => {
    // Vitest 下 import.meta.url 可能是 http 协议，按项目根（vitest cwd）定位静态文件。
    const root = process.cwd()
    const index = readFileSync(resolve(root, 'index.html'), 'utf-8')
    expect(index).toContain(`<title>${BRAND_LOCKUP}</title>`)

    const manifest = JSON.parse(
      readFileSync(resolve(root, 'public', 'manifest.webmanifest'), 'utf-8'),
    ) as { name: string; short_name: string }
    expect(manifest.name).toBe(BRAND_LOCKUP)
    expect(manifest.short_name).toBe(BRAND_NAME_ZH)
  })
})
