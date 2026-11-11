/**
 * 关于与隐私在手机端不应暴露主机的盘符与绝对路径。
 *
 * 报告实测：360px 下 7 条本机路径（程序目录、数据目录、日志、LICENSE、
 * 第三方声明、licenses、SBOM）全部溢出。
 */

import { afterEach, expect, test, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { About } from './About'

const ABOUT = {
  app: 'LiuHao',
  version: '0.2.1',
  commit: null,
  build_time: null,
  app_dir: 'D:\\LiuHao\\app',
  data_dir: 'D:\\LiuHao\\data',
  log_dir: 'D:\\LiuHao\\logs',
  product_license: {
    path: 'D:\\LiuHao\\app\\LICENSE',
    exists: true,
    bytes: 1200,
    text: 'LICENSE TEXT',
  },
  third_party: {
    notices_path: 'D:\\LiuHao\\app\\THIRD-PARTY-NOTICES.txt',
    notices_exists: true,
    notices_text: 'NOTICES TEXT',
    licenses_dir: 'D:\\LiuHao\\app\\licenses',
    license_files: ['a.txt'],
    license_entries: [{ name: 'a.txt', bytes: 10, text: 'A' }],
  },
  sbom: {
    path: 'D:\\LiuHao\\app\\sbom.json',
    exists: true,
    spec_version: '1.5',
    component_count: 3,
    generated_at: null,
    product_version: '0.2.1',
    parse_error: null,
  },
}

const EGRESS = { total: 0, egress_count: 0, placeholder_count: 0, groups: [] }

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

function stub() {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input)
      if (path.startsWith('/v1/about')) return json(ABOUT)
      if (path.startsWith('/v1/network/egress')) return json(EGRESS)
      return json({})
    }),
  )
}

function setWidth(value: number) {
  Object.defineProperty(window, 'innerWidth', {
    configurable: true,
    writable: true,
    value,
  })
}

afterEach(() => {
  vi.unstubAllGlobals()
})

test('手机端不展示本机绝对路径，只保留可读说明', async () => {
  setWidth(390)
  stub()
  try {
    render(<About />)
    expect(await screen.findByText('0.2.1')).toBeTruthy()

    const text = document.body.textContent ?? ''
    expect(text).not.toContain('D:\\')
    expect(text).toContain('本机数据目录（桌面端可见）')
    expect(text).toContain('随包 SBOM 文件（本机路径在桌面端可见）')
    expect(screen.queryByRole('button', { name: '复制数据目录路径' })).toBeNull()
  } finally {
    setWidth(1024)
  }
})

test('宽屏仍显示数据目录路径，便于核对安装位置', async () => {
  stub()
  render(<About />)
  expect(await screen.findByText('D:\\LiuHao\\data')).toBeTruthy()
})
