/**
 * 技能中心的取数契约回归。
 *
 * 三条底线：
 * 1. 后端没有 `/v1/skills` 时，显示「本地服务还没提供」，并且写入动作全部禁用；
 * 2. 后端真实返回技能时，来源 / 权限 / 风险 / 使用员工逐条展示，高风险单独提示；
 * 3. 后端漏发 `used_by` 时显示「未提供」，不猜"没有员工使用"。
 */

import { afterEach, describe, expect, test, vi } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { SkillCenter } from './SkillCenter'

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function stubSkills(handler: (path: string, init?: RequestInit) => Response) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => handler(String(input), init)),
  )
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('技能中心', () => {
  test('接口不存在时显示「本地服务还没提供」，且不提交任何写入', async () => {
    stubSkills(() => json({ detail: 'Not Found' }, 404))
    render(<SkillCenter />)

    const empty = await screen.findByTestId('skills-unprovided')
    expect(empty.textContent).toContain('本地服务还没提供')
    expect(empty.textContent).toContain('不会用占位数字或示例数据代替')

    expect(screen.getByRole('button', { name: '创建技能' })).toHaveProperty('disabled', true)
    expect(screen.getByRole('button', { name: '安装技能' })).toHaveProperty('disabled', true)
    expect(screen.getByLabelText('技能名称')).toHaveProperty('disabled', true)
  })

  test('真实返回时展示来源、权限、风险与使用员工，并提示高风险', async () => {
    stubSkills(() =>
      json({
        skills: [
          {
            id: 'weekly-report',
            name: '周报整理',
            description: '汇总一周的进展与风险',
            source: 'imported',
            version: '1.2.0',
            risk: 'HIGH',
            permissions: ['files.read'],
            enabled: true,
            used_by: ['运营助理', '内容编辑'],
          },
        ],
      }),
    )
    render(<SkillCenter />)

    expect((await screen.findAllByText('周报整理')).length).toBeGreaterThan(0)
    expect(screen.getByText('外部导入')).toBeTruthy()
    expect(screen.getByText('版本：1.2.0')).toBeTruthy()
    expect(screen.getByText('files.read')).toBeTruthy()
    expect(screen.getByText('运营助理、内容编辑')).toBeTruthy()
    expect(screen.getByText('已启用')).toBeTruthy()

    const risky = screen.getByTestId('skills-high-risk')
    expect(risky.textContent).toContain('高风险')

    const table = screen.getByRole('table')
    expect(within(table).getByText('高风险')).toBeTruthy()
  })

  test('后端漏发 used_by 时显示「未提供」，不写成没有员工绑定', async () => {
    stubSkills(() =>
      json({
        skills: [
          {
            id: 'note-tidy',
            name: '笔记归档',
            source: 'builtin',
            risk: 'LOW',
            permissions: [],
            enabled: false,
          },
        ],
      }),
    )
    render(<SkillCenter />)

    expect((await screen.findAllByText('笔记归档')).length).toBeGreaterThan(0)
    const usage = screen.getByTestId('skills-usage-note')
    expect(usage.textContent).toContain('绑定员工')
    expect(usage.textContent).toContain('不等于「没有员工绑定」')

    const table = screen.getByRole('table')
    // 版本与绑定情况都缺字段，因此「未提供」会出现多次；重点是不能出现"暂无员工绑定"。
    expect(within(table).getAllByText('未提供').length).toBeGreaterThanOrEqual(1)
    expect(within(table).queryByText('暂无员工绑定')).toBeNull()
  })

  test('接口可用时「测试」按钮走真实请求并显示后端结论', async () => {
    const calls: string[] = []
    stubSkills((path, init) => {
      calls.push(`${init?.method ?? 'GET'} ${path}`)
      if (path === '/v1/skills/weekly-report/test') {
        return json({ passed: true, detail: '样例输入跑通' })
      }
      return json({
        skills: [
          {
            id: 'weekly-report',
            name: '周报整理',
            source: 'user',
            risk: 'MEDIUM',
            permissions: ['files.read'],
            enabled: true,
            used_by: [],
          },
        ],
      })
    })
    render(<SkillCenter />)

    const testButton = await screen.findByRole('button', { name: '测试' })
    fireEvent.click(testButton)

    const feedback = await screen.findByTestId('skills-feedback')
    expect(feedback.textContent).toContain('测试技能已完成')
    expect(feedback.textContent).toContain('通过')
    expect(calls).toContain('POST /v1/skills/weekly-report/test')
  })

  test('安装支持 .zip / .md / .json，并在安装页显眼说明执行边界', async () => {
    stubSkills(() => json({ skills: [] }))
    render(<SkillCenter />)

    const input = (await screen.findByLabelText(
      '技能包文件（.zip / .md / .json）',
    )) as HTMLInputElement
    expect(input.accept).toBe('.zip,.md,.json')

    const boundary = screen.getByTestId('skills-execution-boundary')
    expect(boundary.textContent).toContain('受限执行，不是沙箱')
    expect(boundary.textContent).toContain('拦不住')

    fireEvent.change(input, {
      target: { files: [new File(['zip'], 'skill.zip', { type: 'application/zip' })] },
    })
    expect(screen.getByRole('button', { name: '安装技能' })).not.toHaveProperty('disabled', true)

    // 范围之外的扩展名仍然明确拒绝，不静默忽略。
    fireEvent.change(input, {
      target: { files: [new File(['x'], 'skill.exe', { type: 'application/octet-stream' })] },
    })
    const notice = await screen.findByTestId('skills-import-notice')
    expect(notice.textContent).toContain('只支持 .zip / .md / .json')
  })
})
