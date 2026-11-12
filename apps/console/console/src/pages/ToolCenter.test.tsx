/**
 * 工具中心的数据来源回归。
 *
 * 重点不是"页面好看"，而是每个区块都说得清数据从哪来：
 * - MCP 没有接口 → 「本地服务还没提供」，不画假服务；
 * - 本地执行器 / 插件 / 模型配置 → 用真实字段，条数与状态都由数组推导；
 * - 启用前必须看到来源、权限、风险，缺项时按钮保持禁用。
 */

import { afterEach, describe, expect, test, vi } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { ToolCenter } from './ToolCenter'

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function stubTools() {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input)
      if (path === '/v1/mcp/servers') return json({ detail: 'Not Found' }, 404)
      if (path === '/v1/employees/capabilities') {
        return json({
          count: 1,
          capabilities: [
            {
              id: 'cap-file-read',
              name: '文件读取',
              tool_id: 'file.read',
              description: '读取授权目录内的文本文件',
              risk: 'HIGH',
            },
          ],
        })
      }
      if (path === '/v1/plugins') {
        return json({
          total: 1,
          active: 0,
          plugins: [
            {
              plugin_id: 'demo-tool',
              name: '演示插件',
              version: '0.0.1',
              kernel_type: 'tool',
              status: 'ready',
              active: false,
              activatable: true,
              builtin: false,
              capabilities: ['demo.do'],
              scope: 'system',
              error: null,
            },
          ],
        })
      }
      if (path === '/v1/models') {
        return json({
          models: [
            {
              id: 'demo-model',
              display_name: '演示模型',
              provider_display_name: '某云',
              api_key_status: 'missing',
              api_key_set: false,
              enabled: true,
              verified: false,
              display_status: '未配置密钥',
            },
          ],
        })
      }
      return json({ detail: 'Not Found' }, 404)
    }),
  )
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('工具中心', () => {
  test('MCP 没有接口时显示「本地服务还没提供」', async () => {
    stubTools()
    render(<ToolCenter />)

    const empty = await screen.findByTestId('tools-mcp-unprovided')
    expect(empty.textContent).toContain('本地服务还没提供')

    const row = screen.getByText('GET /v1/mcp/servers').closest('tr')
    expect(row?.textContent).toContain('本地服务还没提供')
  })

  test('本地执行器展示真实来源与风险，高风险单独提示', async () => {
    stubTools()
    render(<ToolCenter />)

    expect(await screen.findByText('文件读取')).toBeTruthy()
    expect(screen.getByText('file.read')).toBeTruthy()
    expect(screen.getByText('读取授权目录内的文本文件')).toBeTruthy()
    expect(screen.getByTestId('tools-high-risk').textContent).toContain('高风险')

    const table = screen.getAllByRole('table')[0]
    expect(within(table).getByText('高风险')).toBeTruthy()
    expect(within(table).getByText('已注册，可调用')).toBeTruthy()
  })

  test('插件启用前检查：来源真实、权限与风险未提供时按钮禁用', async () => {
    stubTools()
    render(<ToolCenter />)

    expect(await screen.findByText('演示插件')).toBeTruthy()
    expect(screen.getByText('可加载插件')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '查看' }))

    const check = await screen.findByTestId('tools-plugin-precheck')
    expect(within(check).getByText('可加载插件')).toBeTruthy()
    // 权限与风险都没有后端字段：必须显示"未提供"/"风险未提供"，不能自己评级。
    expect(within(check).getAllByText('未提供').length).toBeGreaterThanOrEqual(1)
    expect(within(check).getByText('风险未提供')).toBeTruthy()
    expect(check.textContent).toContain('还缺「需要权限、风险等级」')
    expect(within(check).getByRole('button', { name: '启用' })).toHaveProperty('disabled', true)
  })

  test('API 配置展示来源与真实可用性，不伪造可用数量', async () => {
    stubTools()
    render(<ToolCenter />)

    expect(await screen.findByText('演示模型')).toBeTruthy()
    expect(screen.getByText('某云')).toBeTruthy()
    expect(screen.getByText('未配置')).toBeTruthy()
    expect(screen.getByText('不可用（未配置密钥）')).toBeTruthy()
    expect(
      screen.getByText('共 1 项配置，其中 0 项可用（已配置密钥且连接已验证）。'),
    ).toBeTruthy()
  })
})
