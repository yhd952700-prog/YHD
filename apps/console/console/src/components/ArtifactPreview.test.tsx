import { afterEach, expect, test, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { ArtifactPreview } from './ArtifactPreview'
import { Markdown } from './Markdown'

afterEach(() => {
  vi.unstubAllGlobals()
})

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

test('Markdown 渲染标题 / 列表 / 代码块，且不执行 HTML', () => {
  const { container } = render(
    <Markdown
      text={'# 周报\n\n- 第一条\n- 第二条\n\n```ts\nconst a = 1\n```\n\n<script>alert(1)</script>'}
    />,
  )
  expect(screen.getByRole('heading', { name: '周报' })).toBeTruthy()
  expect(screen.getAllByRole('listitem').map((node) => node.textContent)).toEqual([
    '第一条',
    '第二条',
  ])
  expect(container.querySelector('pre')?.textContent).toContain('const a = 1')
  // 产物是模型写的，HTML 只能当文本，不能变成可执行节点。
  expect(container.querySelector('script')).toBeNull()
  expect(container.textContent).toContain('<script>alert(1)</script>')
})

test('Markdown 文件在抽屉里就地渲染，并提供真实下载', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      jsonResponse({
        path: 'reports/q3.md',
        content: '# 季度复盘\n\n- 指标达成\n',
        encoding: 'utf-8',
        size: 32,
      }),
    ),
  )

  render(<ArtifactPreview path="reports/q3.md" onClose={() => {}} />)
  expect(await screen.findByRole('heading', { name: '季度复盘' })).toBeTruthy()
  expect(screen.getByRole('button', { name: '下载' })).toBeTruthy()
  expect(screen.getByText(/reports\/q3\.md/)).toBeTruthy()
})

test('二进制文件：展示真实原因与路径，不给假下载', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => jsonResponse({ detail: '该文件不是 UTF-8 文本，不能在线预览' }, 415)),
  )

  render(<ArtifactPreview path="exports/report.pdf" onClose={() => {}} />)
  const alert = await screen.findByRole('alert')
  expect(alert.textContent).toContain('不能在线预览')
  expect(screen.queryByRole('button', { name: '下载' })).toBeNull()
  expect(screen.getByText(/exports\/report\.pdf/)).toBeTruthy()
})

test('Esc 关闭抽屉，把控制权交还给调用方', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ content: 'ok', size: 2, encoding: 'utf-8' })))
  const onClose = vi.fn()
  render(<ArtifactPreview path="notes/a.txt" onClose={onClose} />)
  await waitFor(() => expect(screen.getByRole('dialog')).toBeTruthy())
  fireEvent.keyDown(window, { key: 'Escape' })
  expect(onClose).toHaveBeenCalledTimes(1)
})
