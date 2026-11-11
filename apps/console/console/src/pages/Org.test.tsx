/**
 * 组织架构页的真实链路。
 *
 * 钉住六件事：
 * 1. 树按层级显示，负责人与成员数来自真实响应结构；
 * 2. 新建节点把 parent_id 一起提交，同级重名 409 就地提示且表单不消失；
 * 3. 删除前先跑只读预演；预演说"没查任务库"时界面必须是「未检查 —— 原因」，
 *    **不能出现「无风险」**；「组织内任职失效」必须写明不代表权限被收回；
 * 4. 回滚必须先展示"会删除/会恢复/会改名"，确认前不发回滚请求；
 * 5. 键盘能完成树的遍历与收起/展开（拖拽不是唯一路径）；
 * 6. 移动节点、设负责人、移除成员都走真实接口，移除有下级汇报时必须显式确认。
 */

import { beforeEach, describe, expect, test, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { Org } from './Org'
import type { OrgAssignment, OrgImpact, OrgTreeNode } from '../lib/org'

// 11 条用例本身要 3.2s（≈290ms/条），全量时 74 个 jsdom 文件抢 CPU，
// 默认 1s 的 findBy/waitFor 偶发不够用。给本文件放宽单测超时，
// 不改并发、不加 retry——页面本身没有串行慢请求，这里买的是调度余量。
vi.setConfig({ testTimeout: 10_000 })

// ── 测试数据 ────────────────────────────────────────────────────

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function assignment(
  partial: Partial<OrgAssignment> & { assignment_id: string; node_id: string },
): OrgAssignment {
  return {
    account_id: 'acct',
    subject_type: 'employee',
    subject_id: partial.assignment_id,
    subject_label: partial.assignment_id,
    title: '',
    is_lead: false,
    is_primary: false,
    reports_to_assignment_id: null,
    created_at: 1_700_000_000,
    updated_at: 1_700_000_000,
    ...partial,
  }
}

function node(partial: Partial<OrgTreeNode> & { node_id: string; name: string }): OrgTreeNode {
  return {
    account_id: 'acct',
    parent_id: null,
    kind: 'department',
    description: '',
    sort_order: 0,
    created_at: 1_700_000_000,
    updated_at: 1_700_000_000,
    created_by: 'me',
    lead: null,
    assignments: [],
    depth: 0,
    children: [],
    ...partial,
  }
}

const LEAD = assignment({
  assignment_id: 'a1',
  node_id: 'n1',
  subject_id: '小鎏',
  subject_label: '小鎏',
  title: '总经理',
  is_lead: true,
  is_primary: true,
})

const MEMBER = assignment({
  assignment_id: 'a2',
  node_id: 'n2',
  subject_id: '阿蓝',
  subject_label: '阿蓝',
  title: '产品经理',
})

interface MockState {
  tree: {
    account_id: string
    roots: OrgTreeNode[]
    node_count: number
    assignment_count: number
    latest_version: {
      version_id: string
      account_id: string
      seq: number
      created_at: number
      actor: string
      action: string
      target_node_id: string | null
      summary: Record<string, unknown>
    }
    view_account: null
  }
  calls: Array<{ method: string; url: string; body: unknown }>
  versionCalls: number
  rollbackCalls: number
  impactCalls: number
}

function makeState(): MockState {
  const n3 = node({ node_id: 'n3', name: '前端组', parent_id: 'n2', kind: 'team' })
  const n4 = node({ node_id: 'n4', name: '市场部', parent_id: 'n1', kind: 'department' })
  const n2 = node({
    node_id: 'n2',
    name: '研发部',
    parent_id: 'n1',
    kind: 'department',
    children: [n3],
    assignments: [MEMBER],
  })
  const n1 = node({
    node_id: 'n1',
    name: '鎏灏科技',
    kind: 'company',
    lead: LEAD,
    assignments: [LEAD],
    children: [n2, n4],
  })
  return {
    tree: {
      account_id: 'acct',
      roots: [n1],
      node_count: 4,
      assignment_count: 2,
      latest_version: {
        version_id: 'v3',
        account_id: 'acct',
        seq: 3,
        created_at: 1_700_000_900,
        actor: 'me',
        action: 'create_node',
        target_node_id: 'n3',
        summary: {},
      },
      view_account: null,
    },
    calls: [],
    versionCalls: 0,
    rollbackCalls: 0,
    impactCalls: 0,
  }
}

function baseImpact(partial: Partial<OrgImpact>): OrgImpact {
  return {
    action: 'delete_node',
    account_id: 'acct',
    allowed: true,
    blockers: [],
    warnings: {
      orphaned_lead: { label: '有人失去负责人', count: 0, items: [] },
      unattended_tasks: {
        label: '任务无人接管',
        checked: false,
        count: null,
        items: [],
        reason: '任务库暂时读不到（连接超时）。',
      },
      revoked_authority: {
        label: '组织内任职失效',
        count: 1,
        items: [{ node_name: '研发部', assignment_id: 'a2' }],
        note: '组织内任职记录失效；这不代表账号权限被收回。',
      },
    },
    affected_node_ids: [],
    note: '影响检查是只读预演，不会写入任何数据。',
    ...partial,
  }
}

function impactFor(body: Record<string, unknown>): OrgImpact {
  const action = String(body.action ?? '')
  if (action === 'delete_node' && !body.cascade) {
    return baseImpact({
      allowed: false,
      blockers: [
        {
          code: 'ORG_NODE_HAS_CHILDREN',
          message: '该节点下还有 1 个子节点，直接删除会一并移除它们。',
          node_ids: ['n3'],
          node_names: ['前端组'],
        },
      ],
      affected_node_ids: ['n2', 'n3'],
    })
  }
  if (action === 'remove_assignment' && !body.clear_reports) {
    return baseImpact({
      action,
      allowed: false,
      blockers: [
        {
          code: 'ORG_ASSIGNMENT_HAS_REPORTS',
          message: '这条任职下还有 1 条汇报关系，直接移除会留下无上级的汇报。',
        },
      ],
    })
  }
  return baseImpact({ action })
}

function findNode(nodes: OrgTreeNode[], nodeId: string): OrgTreeNode | null {
  for (const item of nodes) {
    if (item.node_id === nodeId) return item
    const hit = findNode(item.children, nodeId)
    if (hit) return hit
  }
  return null
}

function mockFetch(state: MockState) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = (init?.method ?? 'GET').toUpperCase()
    const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : undefined
    state.calls.push({ method, url, body })

    if (url === '/v1/org/tree') return json(200, state.tree)

    if (url.startsWith('/v1/org/templates?')) {
      return json(200, {
        templates: [
          {
            template_id: 'builtin.software-dev',
            name: '软件研发',
            source: 'builtin',
            industry: '软件与互联网',
            description: '仅作参考的研发组织。',
            version: 1,
            revision: null,
            sort: 10,
            hidden: false,
            editable: false,
            deletable: false,
            deletable_reason: '内置模板随包分发，只允许隐藏。',
            node_count: 12,
            position_count: 5,
            max_depth: 3,
          },
        ],
        total: 1,
        hidden_count: 0,
        note: '模板只作参考；选择、预览、套用都必须由人在组织架构页确认。',
      })
    }

    if (url === '/v1/employees') {
      return json(200, {
        employees: [
          { name: '小鎏', role_description: '总经理', agent_count: 0, agents: [] },
          { name: '阿蓝', role_description: '产品经理', agent_count: 0, agents: [] },
        ],
      })
    }

    if (url.startsWith('/v1/org/versions?')) {
      state.versionCalls += 1
      return json(200, {
        account_id: 'acct',
        count: 2,
        total: 2,
        versions: [
          {
            version_id: 'v3',
            account_id: 'acct',
            seq: 3,
            created_at: 1_700_000_900,
            actor: 'me',
            action: 'create_node',
            target_node_id: 'n3',
            summary: {},
          },
          {
            version_id: 'v2',
            account_id: 'acct',
            seq: 2,
            created_at: 1_700_000_500,
            actor: 'me',
            action: 'update_node',
            target_node_id: 'n2',
            summary: {},
          },
        ],
      })
    }

    if (url === '/v1/org/versions/v2') {
      return json(200, {
        version_id: 'v2',
        account_id: 'acct',
        seq: 2,
        created_at: 1_700_000_500,
        actor: 'me',
        action: 'update_node',
        target_node_id: 'n2',
        summary: {},
        snapshot: {
          nodes: [
            {
              node_id: 'n1',
              parent_id: '',
              kind: 'company',
              name: '鎏灏科技',
              description: '',
              sort_order: 0,
              created_at: 0,
              updated_at: 0,
              created_by: 'me',
            },
            {
              node_id: 'n2',
              parent_id: 'n1',
              kind: 'department',
              name: '研发中心',
              description: '',
              sort_order: 0,
              created_at: 0,
              updated_at: 0,
              created_by: 'me',
            },
          ],
          assignments: [],
        },
      })
    }

    if (url === '/v1/org/versions/v2/rollback') {
      state.rollbackCalls += 1
      return json(200, { restored_nodes: 1, restored_assignments: 0, version: null })
    }

    if (url === '/v1/org/impact') {
      state.impactCalls += 1
      return json(200, impactFor(body ?? {}))
    }

    if (method === 'POST' && url === '/v1/org/nodes') {
      const name = String(body?.name ?? '')
      if (name === '重名部门') {
        return json(409, {
          code: 'ORG_DUP_NAME',
          message: '同一上级下已有同名节点：研发部。',
        })
      }
      const created = node({
        node_id: 'n-new',
        name,
        kind: String(body?.kind ?? 'department'),
        parent_id: (body?.parent_id as string | null) ?? null,
      })
      const parentId = (body?.parent_id as string | null) ?? null
      if (parentId) {
        const parent = findNode(state.tree.roots, parentId)
        parent?.children.push(created)
      } else {
        state.tree.roots.push(created)
      }
      state.tree.node_count += 1
      return json(200, { node: created })
    }

    if (url.startsWith('/v1/org/nodes/n2/move')) {
      return json(200, { node: null })
    }

    if (method === 'DELETE' && url.startsWith('/v1/org/nodes/')) {
      return json(200, { removed_nodes: 1, removed_assignments: 1 })
    }

    if (method === 'PUT' && url.startsWith('/v1/org/nodes/n2/lead')) {
      return json(200, { assignment: assignment({ assignment_id: 'a9', node_id: 'n2' }) })
    }

    if (method === 'DELETE' && url.startsWith('/v1/org/assignments/')) {
      return json(200, { reports_unbound: 1 })
    }

    return json(404, { code: 'NOT_FOUND', message: `没有这个接口：${method} ${url}` })
  })
}

let state: MockState

beforeEach(() => {
  state = makeState()
  vi.stubGlobal('fetch', mockFetch(state))
})

function renderPage(query = '') {
  return render(<Org query={query} />)
}

async function selectNode(name: string) {
  const button = await screen.findByRole('button', { name: new RegExp(`^${name}，`) })
  fireEvent.click(button)
  return button
}

// ── 用例 ────────────────────────────────────────────────────────

describe('组织架构页', () => {
  test('按层级展示节点、负责人与成员数', async () => {
    renderPage()

    expect(await screen.findByText('鎏灏科技')).toBeTruthy()
    expect(screen.getAllByText('研发部').length).toBeGreaterThan(0)
    expect(screen.getByText('前端组')).toBeTruthy()
    expect(screen.getByText('4 个节点 · 2 条任职')).toBeTruthy()
    expect(screen.getByText('负责人：小鎏')).toBeTruthy()
    expect(screen.getAllByText('1 人').length).toBe(2)
  })

  test('新建顶层节点会提交名称、类型与空上级', async () => {
    renderPage()
    await screen.findByText('鎏灏科技')

    fireEvent.click(screen.getByRole('button', { name: '新建顶层节点' }))
    fireEvent.change(await screen.findByLabelText('节点名称'), {
      target: { value: '新事业部' },
    })
    fireEvent.change(screen.getByLabelText('节点类型'), { target: { value: 'business_unit' } })
    fireEvent.click(screen.getByRole('button', { name: '新建' }))

    await waitFor(() => {
      const call = state.calls.find((item) => item.method === 'POST' && item.url === '/v1/org/nodes')
      expect(call?.body).toMatchObject({
        name: '新事业部',
        kind: 'business_unit',
        parent_id: null,
      })
    })
    expect((await screen.findAllByText('新事业部')).length).toBeGreaterThan(0)
    expect(screen.queryByLabelText('节点名称')).toBeNull()
  })

  test('同级重名返回 409 时，错误留在表单里而不是静默失败', async () => {
    renderPage()
    await screen.findByText('鎏灏科技')

    fireEvent.click(screen.getByRole('button', { name: '新建顶层节点' }))
    fireEvent.change(await screen.findByLabelText('节点名称'), { target: { value: '重名部门' } })
    fireEvent.click(screen.getByRole('button', { name: '新建' }))

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('已有同名节点')
    expect((screen.getByLabelText('节点名称') as HTMLInputElement).value).toBe('重名部门')
  })

  test('删除先预演：任务库没查就写未检查，并说明任职失效不等于收回权限', async () => {
    renderPage()
    await selectNode('研发部')

    fireEvent.click(screen.getByRole('button', { name: '删除节点' }))

    expect(await screen.findByText(/未检查/)).toBeTruthy()
    expect(screen.getByText(/任务库暂时读不到/)).toBeTruthy()
    expect(screen.queryByText(/无风险/)).toBeNull()
    expect(screen.getAllByText(/不代表账号权限被收回/).length).toBeGreaterThan(0)
    // 有子节点时必须先显式勾选，期间不发出删除请求
    expect(
      state.calls.some((item) => item.method === 'DELETE' && item.url.startsWith('/v1/org/nodes/')),
    ).toBe(false)

    fireEvent.click(screen.getByLabelText(/连同其下的全部子节点/))
    await waitFor(() => expect(state.impactCalls).toBe(2))
    fireEvent.click(await screen.findByRole('button', { name: '确认删除（含子节点）' }))

    await waitFor(() => {
      expect(
        state.calls.some(
          (item) => item.method === 'DELETE' && item.url === '/v1/org/nodes/n2?cascade=true',
        ),
      ).toBe(true)
    })
  })

  test('回滚先展示会发生什么，确认前不发请求', async () => {
    renderPage()
    await screen.findByText('鎏灏科技')

    fireEvent.click(screen.getByRole('button', { name: '版本历史' }))
    await screen.findByText('第 2 版')
    const buttons = screen.getAllByRole('button', { name: '查看回滚影响' })
    fireEvent.click(buttons[1])

    expect(await screen.findByText('回滚到第 2 版会发生什么')).toBeTruthy()
    expect(screen.getByText(/会删除 2 个节点：前端组、市场部/)).toBeTruthy()
    expect(screen.getByText(/会改回名字/)).toBeTruthy()
    expect(screen.getByText(/研发中心/)).toBeTruthy()
    expect(state.rollbackCalls).toBe(0)

    fireEvent.click(screen.getByRole('button', { name: '确认回滚' }))
    await waitFor(() => expect(state.rollbackCalls).toBe(1))
  })

  test('方向键可以在树内移动并展开/收起', async () => {
    renderPage()
    const first = await screen.findByRole('button', { name: /^鎏灏科技，/ })
    expect((first as HTMLButtonElement).tabIndex).toBe(0)

    fireEvent.keyDown(first, { key: 'ArrowDown' })
    const second = screen.getByRole('button', { name: /^研发部，/ })
    await waitFor(() => expect(document.activeElement).toBe(second))

    fireEvent.keyDown(second, { key: 'ArrowLeft' })
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: /^前端组，/ })).toBeNull(),
    )
    fireEvent.keyDown(second, { key: 'ArrowRight' })
    expect(await screen.findByRole('button', { name: /^前端组，/ })).toBeTruthy()
  })

  test('键盘路径也能移动节点：选新上级 → 检查影响 → 确认', async () => {
    renderPage()
    await selectNode('研发部')

    fireEvent.click(screen.getByRole('button', { name: '移动' }))
    fireEvent.change(await screen.findByLabelText('新的上级'), { target: { value: 'n4' } })

    const confirm = screen.getByRole('button', { name: '确认移动' }) as HTMLButtonElement
    expect(confirm.disabled).toBe(true)

    fireEvent.click(screen.getByRole('button', { name: '先检查影响' }))
    await waitFor(() => expect(screen.getByText('检查结论：可以执行。')).toBeTruthy())

    fireEvent.click(screen.getByRole('button', { name: '确认移动' }))
    await waitFor(() => {
      const call = state.calls.find((item) => item.url === '/v1/org/nodes/n2/move')
      expect(call?.body).toEqual({ parent_id: 'n4' })
    })
  })

  test('设负责人会写入真实接口', async () => {
    renderPage()
    await selectNode('研发部')

    fireEvent.click(screen.getByRole('button', { name: '设负责人' }))
    fireEvent.change(await screen.findByLabelText('选择 AI 员工'), { target: { value: '小鎏' } })
    fireEvent.click(screen.getByRole('button', { name: '保存负责人' }))

    await waitFor(() => {
      const call = state.calls.find(
        (item) => item.method === 'PUT' && item.url === '/v1/org/nodes/n2/lead',
      )
      expect(call?.body).toEqual({ subject_type: 'employee', subject_id: '小鎏', title: '' })
    })
  })

  test('移除成员遇到下级汇报时，必须勾选一并解除才可继续', async () => {
    renderPage()
    await selectNode('研发部')

    fireEvent.click(screen.getByRole('button', { name: '移除' }))
    expect(await screen.findByText(/还有 1 条汇报关系/)).toBeTruthy()
    expect(
      state.calls.some((item) => item.method === 'DELETE' && item.url.startsWith('/v1/org/assignments/')),
    ).toBe(false)

    fireEvent.click(screen.getByLabelText(/一并解除其下级的汇报关系/))
    await waitFor(() => expect(state.impactCalls).toBe(2))
    fireEvent.click(await screen.findByRole('button', { name: '确认移除' }))

    await waitFor(() => {
      expect(
        state.calls.some(
          (item) =>
            item.method === 'DELETE' &&
            item.url === '/v1/org/assignments/a2?clear_reports=true',
        ),
      ).toBe(true)
    })
  })

  test('搜索只列出匹配节点，并说明它在谁下面', async () => {
    renderPage('前端')
    expect(await screen.findByText(/1 个节点。/)).toBeTruthy()
    expect(screen.getByText('小组 · 上级：鎏灏科技 / 研发部')).toBeTruthy()
  })

  test('「从模板开始」只打开参考库，不自动预览或套用', async () => {
    renderPage()
    await screen.findByText('鎏灏科技')

    fireEvent.click(screen.getByRole('button', { name: '从模板开始' }))
    expect(await screen.findByText('软件研发')).toBeTruthy()
    expect(screen.getAllByText(/模板只作参考/).length).toBeGreaterThan(0)
    expect(screen.getByText(/内置模板只读/)).toBeTruthy()

    // 只打开列表：没有自动预览、没有自动写入。
    expect(
      state.calls.some(
        (item) => item.url.includes('/preview') || item.url.includes('/apply'),
      ),
    ).toBe(false)
  })
})
