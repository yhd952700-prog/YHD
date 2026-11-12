/**
 * 审批签发回归（设置 → 审批签发，权限线发放的一次性授权令牌）。
 *
 * 这是纯展示型组件：状态与副作用都由 App 层持有，这里只钉住界面自己的诚实边界：
 *   1. 未认证时如实说"未认证"，不展示拦截态势，不假装系统处于某个安全状态；
 *   2. 令牌保存/清除真的调 onTokenChange；
 *   3. 拦截态势、有效凭据来自传入的快照，不自己编数字；
 *   4. 授权对象（target）必填：空值时"记录授权"点不了；通配 "*" 必须二次确认
 *      后才能提交，未确认时点不了；
 *   5. 失败态带可操作的"重试"出口；内置服务账号签发时如实提示不能签发人工授权。
 */

import { describe, expect, test, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { ApprovalCenter, type ApprovalActions, type ApprovalUiState } from './ApprovalCenter'

function snap(overrides: Record<string, unknown> = {}) {
  return {
    env_var: 'LIUHAO_POLICY_ENFORCEMENT',
    spec: 'policy.yaml',
    enabled: true,
    enforced_actions: ['file_write', 'network_post'],
    count: 2,
    exempt_actions: [],
    config_error: null,
    ...overrides,
  }
}

function grant(overrides: Record<string, unknown> = {}) {
  return {
    grant_id: 'grant-abcdef123456',
    principal: 'root',
    actions: ['file_write'],
    target: '/data/report.md',
    is_wildcard: false,
    reason: '发布周报',
    issued_by: 'root',
    granted_at: 1762833600,
    expires_at: 1762833900,
    revoked_at: null,
    revoke_reason: '',
    is_active: true,
    remaining_seconds: 300,
    ...overrides,
  }
}

function state(overrides: Partial<ApprovalUiState> = {}): ApprovalUiState {
  return {
    token: '',
    authed: false,
    snap: null,
    grants: [],
    busy: false,
    error: '',
    machineIdentity: '',
    ...overrides,
  }
}

function actions(overrides: Partial<ApprovalActions> = {}): ApprovalActions & {
  onTokenChange: ReturnType<typeof vi.fn>
  onRefresh: ReturnType<typeof vi.fn>
  onIssue: ReturnType<typeof vi.fn>
  onRevoke: ReturnType<typeof vi.fn>
} {
  return {
    onTokenChange: vi.fn(),
    onRefresh: vi.fn(),
    onIssue: vi.fn(),
    onRevoke: vi.fn(),
    ...overrides,
  }
}

/** jest-dom 未装：用原生 disabled 属性断言。 */
function isDisabled(el: HTMLElement): boolean {
  return (el as HTMLButtonElement).disabled === true
}

function renderCenter(s: ApprovalUiState, a: ApprovalActions) {
  return render(<ApprovalCenter state={s} actions={a} />)
}

describe('未认证', () => {
  test('如实显示未认证与取令牌提示，不展示拦截态势', () => {
    renderCenter(state(), actions())
    expect(screen.getByText('未认证')).toBeTruthy()
    expect(screen.getByText(/未认证。请先获取审批令牌/)).toBeTruthy()
    expect(screen.getByText(/未认证时看不到拦截态势/)).toBeTruthy()
    expect(screen.queryByText('动作拦截态势')).toBeNull()
  })

  test('粘贴令牌点保存真的调 onTokenChange；清除在没有令牌时禁用', () => {
    const a = actions()
    renderCenter(state(), a)

    fireEvent.change(screen.getByPlaceholderText('粘贴审批令牌'), {
      target: { value: 'token-123' },
    })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))
    expect(a.onTokenChange).toHaveBeenCalledWith('token-123')

    expect(isDisabled(screen.getByRole('button', { name: '清除' }))).toBe(true)
  })

  test('有令牌时点清除调 onTokenChange("")', () => {
    const a = actions()
    renderCenter(state({ token: 'token-123' }), a)
    fireEvent.click(screen.getByRole('button', { name: '清除' }))
    expect(a.onTokenChange).toHaveBeenCalledWith('')
  })
})

describe('已认证：态势与凭据', () => {
  test('快照原样渲染：武装态、已拦截动作数、豁免动作', () => {
    renderCenter(
      state({ token: 't', authed: true, snap: snap({ exempt_actions: ['file_read'] }) }),
      actions(),
    )
    expect(screen.getByText('已认证')).toBeTruthy()
    expect(screen.getByText('已武装')).toBeTruthy()
    expect(screen.getByText('2 个')).toBeTruthy()
    // 两个动作同时出现在态势 chips 与授权表单下拉里，各出现两次。
    expect(screen.getAllByText('file_write').length).toBe(2)
    expect(screen.getAllByText('network_post').length).toBe(2)
    expect(screen.getByText(/豁免.*file_read/)).toBeTruthy()
  })

  test('配置错误时如实标配置错误', () => {
    renderCenter(
      state({ token: 't', authed: true, snap: snap({ config_error: 'bad yaml' }) }),
      actions(),
    )
    expect(screen.getByText('配置错误')).toBeTruthy()
    expect(screen.getByText(/拦截配置有误/)).toBeTruthy()
  })

  test('没有有效凭据时说没有，不编一条', () => {
    renderCenter(state({ token: 't', authed: true, snap: snap() }), actions())
    expect(screen.getByText('当前没有有效凭据')).toBeTruthy()
  })

  test('凭据列表显示主体、剩余时间与理由；撤销调 onRevoke', () => {
    const a = actions()
    renderCenter(
      state({ token: 't', authed: true, snap: snap(), grants: [grant()] }),
      a,
    )
    expect(screen.getByText('root')).toBeTruthy()
    expect(screen.getByText(/剩余 5 分/)).toBeTruthy()
    expect(screen.getByText(/理由：发布周报/)).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '撤销' }))
    expect(a.onRevoke).toHaveBeenCalledWith('grant-abcdef123456')
  })

  test('已失效凭据显示"已失效"，撤销按钮禁用', () => {
    renderCenter(
      state({
        token: 't',
        authed: true,
        snap: snap(),
        grants: [grant({ is_active: false, remaining_seconds: 0 })],
      }),
      actions(),
    )
    expect(screen.getByText('已失效')).toBeTruthy()
    expect(isDisabled(screen.getByRole('button', { name: '撤销' }))).toBe(true)
  })

  test('内置服务账号如实提示不能签发人工授权', () => {
    renderCenter(
      state({ token: 't', authed: true, snap: snap(), machineIdentity: 'svc-agent' }),
      actions(),
    )
    expect(screen.getByText(/当前使用的是内置服务账号/)).toBeTruthy()
    expect(screen.getByText('svc-agent')).toBeTruthy()
  })
})

describe('记录授权表单', () => {
  test('授权对象为空：记录授权点不了', () => {
    renderCenter(state({ token: 't', authed: true, snap: snap() }), actions())
    expect(isDisabled(screen.getByRole('button', { name: '记录授权' }))).toBe(true)
  })

  test('填好对象后提交，参数原样传给 onIssue', () => {
    const a = actions()
    renderCenter(state({ token: 't', authed: true, snap: snap() }), a)

    fireEvent.change(screen.getByPlaceholderText('授权对象（必填）'), {
      target: { value: '/data/report.md' },
    })
    fireEvent.change(screen.getByPlaceholderText('授权理由（记入审计）'), {
      target: { value: '发布周报' },
    })
    fireEvent.click(screen.getByRole('button', { name: '记录授权' }))
    expect(a.onIssue).toHaveBeenCalledWith('file_write', '/data/report.md', '发布周报', 300, false)
  })

  test('通配 "*" 未二次确认时点不了；确认后才能提交且带确认位', () => {
    const a = actions()
    renderCenter(state({ token: 't', authed: true, snap: snap() }), a)

    fireEvent.change(screen.getByPlaceholderText('授权对象（必填）'), {
      target: { value: '*' },
    })
    // 标签文案被 <b>全部对象</b> 拆成多段文本节点：直接取唯一的 checkbox。
    const confirmBox = screen.getByRole('checkbox')
    expect(confirmBox.closest('label')?.textContent).toContain('确认对')
    expect(isDisabled(screen.getByRole('button', { name: '记录授权' }))).toBe(true)

    fireEvent.click(confirmBox)
    fireEvent.click(screen.getByRole('button', { name: '记录授权' }))
    expect(a.onIssue).toHaveBeenCalledWith('file_write', '*', '', 300, true)
  })
})

describe('失败态', () => {
  test('错误带"重试"出口，点重试调 onRefresh', () => {
    const a = actions()
    renderCenter(
      state({ token: 't', authed: false, snap: null, error: '令牌已被吊销' }),
      a,
    )
    expect(screen.getByText(/令牌已被吊销/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /重试/ }))
    expect(a.onRefresh).toHaveBeenCalled()
  })
})
