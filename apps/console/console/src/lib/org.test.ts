/**
 * 组织架构的纯展示计算。
 *
 * 这几条是页面"说得对不对"的地基：树的行序、可移动目标、影响面结论、
 * 回滚差异。它们不依赖渲染，坏掉时定位成本最低。
 */

import { describe, expect, test } from 'vitest'
import {
  diffSnapshots,
  duplicateNameHint,
  flattenOrg,
  kindLabel,
  moveCandidates,
  orphanReason,
  taskWarning,
} from './org'
import type { OrgAssignment, OrgNode, OrgTreeNode } from './org'

function node(partial: Partial<OrgTreeNode> & { node_id: string; name: string }): OrgTreeNode {
  return {
    account_id: 'acct',
    parent_id: null,
    kind: 'department',
    description: '',
    sort_order: 0,
    created_at: 0,
    updated_at: 0,
    created_by: 'me',
    lead: null,
    assignments: [],
    depth: 0,
    children: [],
    ...partial,
  }
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
    created_at: 0,
    updated_at: 0,
    ...partial,
  }
}

const TREE: OrgTreeNode[] = [
  node({
    node_id: 'n1',
    name: '鎏灏科技',
    kind: 'company',
    children: [
      node({
        node_id: 'n2',
        name: '研发部',
        parent_id: 'n1',
        children: [node({ node_id: 'n3', name: '前端组', parent_id: 'n2' })],
      }),
      node({ node_id: 'n4', name: '市场部', parent_id: 'n1' }),
    ],
  }),
]

describe('flattenOrg', () => {
  test('按视觉顺序展开，并给出深度与上级路径', () => {
    const rows = flattenOrg(TREE)
    expect(rows.map((row) => row.node.name)).toEqual(['鎏灏科技', '研发部', '前端组', '市场部'])
    expect(rows.map((row) => row.depth)).toEqual([0, 1, 2, 1])
    expect(rows[2].path).toBe('鎏灏科技 / 研发部')
    expect(rows[0].path).toBe('')
  })
})

describe('moveCandidates', () => {
  test('排除自己与全部后代，顶层永远在第一个', () => {
    const rows = flattenOrg(TREE)
    const options = moveCandidates(rows, 'n2')
    expect(options[0]).toMatchObject({ id: '', label: '顶层（没有上级）' })
    expect(options.map((item) => item.id)).toEqual(['', 'n1', 'n4'])
    expect(options.some((item) => item.id === 'n3')).toBe(false)
  })

  test('找不到节点时只允许移到顶层，不把整棵树当候选', () => {
    const options = moveCandidates(flattenOrg(TREE), 'missing')
    expect(options.map((item) => item.id)).toEqual([''])
  })
})

describe('taskWarning', () => {
  test('没有数据时是未检查，绝不冒充无风险', () => {
    const view = taskWarning(undefined)
    expect(view.state).toBe('unchecked')
    expect(view.text).toContain('未检查')
    expect(view.text).not.toContain('无风险')
  })

  test('服务端明确没查时，原样带出原因', () => {
    const view = taskWarning({ checked: false, reason: '任务库暂时读不到（连接超时）。' })
    expect(view.state).toBe('unchecked')
    expect(view.text).toBe('未检查 —— 任务库暂时读不到（连接超时）。')
  })

  test('查过且为 0 才说没有未结束任务', () => {
    expect(taskWarning({ checked: true, count: 0 }).text).toBe('已检查：没有未结束的任务。')
    expect(taskWarning({ checked: true, count: 3 }).text).toBe('已检查：3 个任务仍在进行。')
  })
})

describe('diffSnapshots', () => {
  const currentNodes: OrgNode[] = [
    TREE[0],
    TREE[0].children[0],
    TREE[0].children[0].children[0],
  ]
  const currentAssignments: OrgAssignment[] = [
    assignment({ assignment_id: 'a1', node_id: 'n1', is_lead: true }),
    assignment({ assignment_id: 'a2', node_id: 'n2' }),
  ]

  test('列出会删除的节点、会改回的名字与任职变化', () => {
    const diff = diffSnapshots(
      {
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
        assignments: [
          {
            assignment_id: 'a1',
            node_id: 'n1',
            subject_type: 'employee',
            subject_id: 'a1',
            subject_label: 'a1',
            title: '',
            is_lead: 1,
            is_primary: 0,
            reports_to_assignment_id: '',
            created_at: 0,
            updated_at: 0,
          },
        ],
      },
      currentNodes,
      currentAssignments,
    )

    expect(diff.identical).toBe(false)
    expect(diff.removedNodes.map((item) => item.name)).toEqual(['前端组'])
    expect(diff.addedNodes).toEqual([])
    expect(diff.renamedNodes).toEqual([{ before: '研发部', after: '研发中心' }])
    expect(diff.assignmentsRemoved).toBe(1)
    expect(diff.assignmentsAdded).toBe(0)
    expect(diff.assignmentsChanged).toBe(0)
  })

  test('结构一致时标记 identical，界面据此禁用回滚', () => {
    const snapshot = {
      nodes: currentNodes.map((item) => ({
        node_id: item.node_id,
        parent_id: item.parent_id ?? '',
        kind: item.kind,
        name: item.name,
        description: item.description,
        sort_order: item.sort_order,
        created_at: item.created_at,
        updated_at: item.updated_at,
        created_by: item.created_by,
      })),
      assignments: currentAssignments.map((item) => ({
        assignment_id: item.assignment_id,
        node_id: item.node_id,
        subject_type: item.subject_type,
        subject_id: item.subject_id,
        subject_label: item.subject_label,
        title: item.title,
        is_lead: item.is_lead ? 1 : 0,
        is_primary: item.is_primary ? 1 : 0,
        reports_to_assignment_id: item.reports_to_assignment_id ?? '',
        created_at: item.created_at,
        updated_at: item.updated_at,
      })),
    }
    expect(diffSnapshots(snapshot, currentNodes, currentAssignments).identical).toBe(true)
  })
})

describe('文案翻译', () => {
  test('同级重名只认服务端的稳定错误码', () => {
    expect(duplicateNameHint('ORG_DUP_NAME')).toContain('同名')
    expect(duplicateNameHint('ORG_CYCLE')).toBeNull()
    expect(duplicateNameHint(null)).toBeNull()
  })

  test('未知节点类型原样显示，不编造类别', () => {
    expect(kindLabel('department')).toBe('部门')
    expect(kindLabel('guild')).toBe('guild')
  })

  test('失去负责人的原因翻成人话', () => {
    expect(orphanReason('lead_removed')).toBe('负责人被移除')
    expect(orphanReason('something_new')).toBe('something_new')
    expect(orphanReason(undefined)).toBe('失去负责人')
  })
})
