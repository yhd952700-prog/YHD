import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, test, vi } from 'vitest'
import { DiagnosticsExport } from './DiagnosticsExport'
import { exportDiagnosticsBundle } from '../lib/diagnostics'

vi.mock('../lib/diagnostics', () => ({
  exportDiagnosticsBundle: vi.fn(),
}))

const mockedExport = vi.mocked(exportDiagnosticsBundle)

beforeEach(() => {
  mockedExport.mockReset()
})

describe('一键诊断包入口', () => {
  test('点击之前就能看到"这个包不含你的密钥"', () => {
    render(<DiagnosticsExport />)
    expect(screen.getByText(/这个包不含你的密钥/)).toBeTruthy()
  })

  test('导出成功后告诉用户文件在哪', async () => {
    mockedExport.mockResolvedValue({
      filename: 'liuhao-diagnostics-20260101-120000.zip',
      location: 'download',
      omitted: [],
    })

    render(<DiagnosticsExport />)
    fireEvent.click(screen.getByRole('button', { name: '导出诊断包' }))

    await waitFor(() => {
      expect(screen.getByRole('status').textContent).toContain(
        'liuhao-diagnostics-20260101-120000.zip',
      )
    })
    expect(screen.getByRole('status').textContent).toContain('下载位置')
  })

  test('降级导出时说明少了哪一类文件，而不是只给一个数字', async () => {
    mockedExport.mockResolvedValue({
      filename: 'liuhao-diagnostics-20260101-120000.zip',
      location: 'download',
      omitted: ['logs/gateway.log.txt', 'summary.json'],
    })

    render(<DiagnosticsExport />)
    fireEvent.click(screen.getByRole('button', { name: '导出诊断包' }))

    await waitFor(() => {
      expect(screen.getByRole('status').textContent).toContain(
        '有 1 个日志文件和 1 个诊断文件因含密钥未包含',
      )
    })
    const text = screen.getByRole('status').textContent ?? ''
    expect(text).toContain('REDACTED-MEMBERS.txt')
    // 不能退化成光秃秃的数量提示。
    expect(text).not.toContain('有 2 个文件因含密钥未包含')
  })

  test('只有日志被剔除时不出现"0 个诊断文件"', async () => {
    mockedExport.mockResolvedValue({
      filename: 'liuhao-diagnostics-20260101-120000.zip',
      location: 'download',
      omitted: ['logs/gateway.log.txt'],
    })

    render(<DiagnosticsExport />)
    fireEvent.click(screen.getByRole('button', { name: '导出诊断包' }))

    await waitFor(() => {
      expect(screen.getByRole('status').textContent).toContain(
        '有 1 个日志文件因含密钥未包含',
      )
    })
    expect(screen.getByRole('status').textContent).not.toContain('0 个诊断文件')
  })

  test('用户取消时说明"没有生成文件"，不冒充失败', async () => {
    mockedExport.mockResolvedValue(null)

    render(<DiagnosticsExport />)
    fireEvent.click(screen.getByRole('button', { name: '导出诊断包' }))

    await waitFor(() => {
      expect(screen.getByRole('status').textContent).toContain('已取消导出')
    })
  })

  test('失败时展示中文原因并给出重试建议', async () => {
    mockedExport.mockRejectedValue(new Error('无法连接到贾维斯服务。请确认服务已经启动、网络连接正常，然后重试。'))

    render(<DiagnosticsExport />)
    fireEvent.click(screen.getByRole('button', { name: '导出诊断包' }))

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toContain('无法连接到贾维斯服务')
    })
    expect(screen.getByRole('alert').textContent).toContain('重试')
  })
})
