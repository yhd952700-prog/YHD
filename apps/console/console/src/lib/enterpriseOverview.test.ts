import { describe, expect, test } from 'vitest'
import {
  approvalRiskLabel,
  buildDepartmentRows,
  completedToday,
  sortPendingApprovals,
  summarizeEmployees,
  summarizeOrg,
} from './enterpriseOverview'

describe('summarizeEmployees', () => {
  test('按真实 employment_status 与 agent pool 汇总', () => {
    const summary = summarizeEmployees({
      employees: [
        { employment_status: 'active', agents: [{ status: 'running' }, { status: 'idle' }] },
        { employment_status: 'active', agents: [{ status: 'idle' }] },
        { employment_status: 'disabled', agents: [] },
      ],
      employee_count: 3,
    })
    expect(summary).not.toBeNull()
    expect(summary).toMatchObject({
      total: 3,
      active: 2,
      disabled: 1,
      other: 0,
      agents: 3,
      runningAgents: 1,
      employeesWithRunningAgents: 1,
    })
  })

  test('响应没有名册也没有总数时返回 null，不把缺字段说成 0 个员工', () => {
    expect(summarizeEmployees({})).toBeNull()
    expect(summarizeEmployees(null)).toBeNull()
  })
})

describe('summarizeOrg', () => {
  test('统计节点、任职与跨节点员工', () => {
    const summary = summarizeOrg({
      roots: [
        {
          node_id: 'a',
          assignments: [{ subject_type: 'employee', subject_id: 'Alice' }],
          children: [
            {
              node_id: 'b',
              assignments: [
                { subject_type: 'employee', subject_id: 'Alice' },
                { subject_type: 'human', subject_id: '张三' },
              ],
            },
          ],
        },
      ],
    })
    expect(summary).not.toBeNull()
    expect(summary).toMatchObject({
      nodeCount: 2,
      assignmentCount: 3,
      employeeMemberCount: 1,
      humanMemberCount: 1,
      duplicateEmployeeCount: 1,
    })
  })
})

describe('completedToday', () => {
  const now = new Date(2026, 10, 11, 15, 0, 0)
  const todayAt = new Date(2026, 10, 11, 9, 30, 0).toISOString()
  const yesterdayAt = new Date(2026, 10, 10, 23, 0, 0).toISOString()

  test('只算本地时区今天完成的，缺完成时间单独计数', () => {
    const summary = completedToday(
      {
        total: 3,
        items: [
          { task_id: 'a', completed_at: todayAt },
          { task_id: 'b', completed_at: yesterdayAt },
          { task_id: 'c', completed_at: null },
        ],
      },
      now.getTime(),
    )
    expect(summary).toMatchObject({
      count: 1,
      scanned: 3,
      total: 3,
      exact: true,
      missingTime: 1,
    })
  })

  test('只扫描了部分已完成任务时标记 exact=false', () => {
    const summary = completedToday(
      { total: 250, items: [{ task_id: 'a', completed_at: todayAt }] },
      now.getTime(),
    )
    expect(summary).toMatchObject({ count: 1, exact: false, scanned: 1, total: 250 })
  })
})

describe('buildDepartmentRows', () => {
  const org = {
    roots: [
      {
        node_id: 'rd',
        name: '研发部',
        kind: 'department',
        assignments: [
          { subject_type: 'employee', subject_id: 'Alice' },
          { subject_type: 'employee', subject_id: 'Bob' },
        ],
        children: [
          {
            node_id: 'ops',
            name: '运营组',
            kind: 'team',
            assignments: [{ subject_type: 'employee', subject_id: 'Carol' }],
          },
        ],
      },
    ],
  }

  const tasks = {
    total: 4,
    items: [
      { task_id: 't1', assignee: { type: 'employee', name: 'Alice' } },
      { task_id: 't2', assignee: { type: 'employee', name: 'Alice、Carol' } },
      { task_id: 't3', assignee: { type: 'employee', name: 'Bob' } },
      { task_id: 't4', assignee: { type: 'human', name: 'Alice' } },
    ],
  }

  test('按节点成员统计，同一任务涉及多个成员时各节点各计一次', () => {
    const rows = buildDepartmentRows(org, tasks)
    expect(rows).toHaveLength(1)
    expect(rows[0]).toMatchObject({
      name: '研发部',
      memberCount: 2,
      directTaskCount: 3,
      subtreeTaskCount: 3,
    })
    expect(rows[0].children[0]).toMatchObject({
      name: '运营组',
      memberCount: 1,
      directTaskCount: 1,
      subtreeTaskCount: 1,
    })
  })

  test('任务没读到时计数是 null，不是 0', () => {
    const rows = buildDepartmentRows(org, null)
    expect(rows[0].directTaskCount).toBeNull()
    expect(rows[0].subtreeTaskCount).toBeNull()
    expect(rows[0].children[0].directTaskCount).toBeNull()
  })
})

describe('sortPendingApprovals', () => {
  test('按风险降序，风险相同按拦截时间升序', () => {
    const items = sortPendingApprovals({
      items: [
        { pending_id: 'low', risk_level: 'LOW', blocked_at: 100 },
        { pending_id: 'high-late', risk_level: 'HIGH', blocked_at: 200 },
        { pending_id: 'high-early', risk_level: 'HIGH', blocked_at: 100 },
      ],
    })
    expect(items?.map((item) => item.pending_id)).toEqual(['high-early', 'high-late', 'low'])
    expect(approvalRiskLabel('HIGH')).toBe('高')
  })

  test('响应没有 items 时返回 null，交给页面写「暂未提供」', () => {
    expect(sortPendingApprovals({})).toBeNull()
  })
})
