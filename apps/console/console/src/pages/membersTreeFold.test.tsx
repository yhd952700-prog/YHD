/**
 * 成员页根视图：层级树折叠的行为契约。
 *
 * 钉死五件事：
 * 1. 默认只展开两层，更深的整支不渲染，折叠节点给出"还有 N 个"；
 * 2. 搜索命中时自动展开命中路径，并对命中片段做 `<mark>` 高亮；
 * 3. 折叠状态跨重新挂载保持（模块级记忆，换页 / 换查看范围不重置）；
 * 4. 折叠按钮是原生 button：可聚焦、`aria-expanded` 跟随状态（Enter/Space 由原生语义提供）；
 * 5. 500 节点 / 10 层的树，默认渲染量不随总节点数增长。
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, test, vi } from 'vitest'
import type { AccountNode, AccountsPayload } from '../lib/accounts'
import type { Session } from '../lib/auth'
import { buildProfile, deriveCaps } from '../lib/accountCaps'
import { Members } from './Members'

const PAYLOAD: AccountsPayload = {
  members: [{ principal: 'alice', permissions: ['account:read'], status: 'active' }],
  count: 1,
}

const ROOT_SESSION: Session = {
  token: 't',
  principal: 'root',
  displayName: '根',
  scope: 'L0',
  permissions: ['account:manage_children'],
  expiresAt: 0,
  client: 'web',
  isRoot: true,
}

const ROOT_PROFILE = buildProfile(ROOT_SESSION, PAYLOAD, 'live', null)
const ROOT_CAPS = deriveCaps(ROOT_PROFILE)

function node(principal: string, children: AccountNode[] = []): AccountNode {
  return {
    principal,
    permissions: ['account:read'],
    status: 'active',
    allow_superior_key_use: false,
    children,
  }
}

/**
 * 四层结构：
 *   alice
 *     alice-1
 *       alice-1-1
 *     alice-2
 *   bob
 *     bob-1
 */
function fixtureTree(): AccountNode {
  return node('root', [
    node('alice', [
      node('alice-1', [node('alice-1-1')]),
      node('alice-2'),
    ]),
    node('bob', [node('bob-1')]),
  ])
}

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

function stubFetch(tree: AccountNode) {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input)
    if (url === '/v1/accounts') return json(PAYLOAD)
    if (url === '/v1/accounts/tree') return json({ tree, warnings: [] })
    if (url === '/v1/accounts/roles') {
      return json({ roles: [{ id: 'viewer', permissions: ['account:read'] }], scopes: ['account:read'] })
    }
    return json({ detail: 'Not Found' })
  })
}

afterEach(() => {
  vi.restoreAllMocks()
})

async function renderRoot() {
  const utils = render(
    <Members
      profile={ROOT_PROFILE}
      caps={ROOT_CAPS}
      variant="root"
      readOnly={false}
      onViewAs={vi.fn()}
    />,
  )
  // 根表头 5 列、平铺表 4 列：等到 5 列出现，说明树已经加载并接管渲染。
  await waitFor(() => expect(utils.container.querySelectorAll('thead th').length).toBe(5))
  return utils
}

function bodyRows(container: HTMLElement): number {
  return container.querySelectorAll('tbody tr').length
}

describe('成员页根视图：层级树折叠', () => {
  test('默认只展开两层，更深的节点不渲染，折叠节点给出数量', async () => {
    stubFetch(fixtureTree())
    const { container } = await renderRoot()

    // depth0: alice、bob；depth1: alice-1、alice-2、bob-1。alice-1-1（depth2）必须不可见。
    expect(bodyRows(container)).toBe(5)
    expect(document.body.textContent).not.toContain('alice-1-1')

    // alice-1 有 1 个后代被折叠。
    expect(screen.getByText('（还有 1 个）')).toBeTruthy()

    const toggles = container.querySelectorAll('button[aria-expanded]')
    expect(toggles.length).toBe(3)
    expect(container.querySelectorAll('button[aria-expanded="true"]').length).toBe(2)
    expect(container.querySelectorAll('button[aria-expanded="false"]').length).toBe(1)
  })

  test('点击折叠按钮展开子树，再点收起；按钮是原生 button 且 aria-expanded 跟随', async () => {
    stubFetch(fixtureTree())
    const { container } = await renderRoot()

    const toggle = screen.getByRole('button', { name: '展开 alice-1' })
    expect(toggle.tagName).toBe('BUTTON')
    expect(toggle.getAttribute('type')).toBe('button')
    expect((toggle as HTMLButtonElement).disabled).toBe(false)

    // 原生 button 天然可 Tab 聚焦；Enter/Space 的激活也是原生行为（合成 click）。
    toggle.focus()
    expect(document.activeElement).toBe(toggle)

    fireEvent.click(toggle)
    expect(bodyRows(container)).toBe(6)
    expect(document.body.textContent).toContain('alice-1-1')
    expect(screen.queryByText('（还有 1 个）')).toBeNull()
    expect(screen.getByRole('button', { name: '收起 alice-1' }).getAttribute('aria-expanded')).toBe(
      'true',
    )

    fireEvent.click(screen.getByRole('button', { name: '收起 alice-1' }))
    expect(bodyRows(container)).toBe(5)
    expect(document.body.textContent).not.toContain('alice-1-1')
  })

  test('搜索命中时自动展开命中路径并高亮命中片段', async () => {
    stubFetch(fixtureTree())
    const { container } = await renderRoot()

    fireEvent.change(screen.getByLabelText('搜索账号'), { target: { value: 'alice-1-1' } })

    // 路径 alice → alice-1 → alice-1-1 全部可见；路径之外的 bob 整支消失。
    expect(bodyRows(container)).toBe(3)
    expect(document.body.textContent).toContain('alice')
    expect(document.body.textContent).toContain('alice-1')
    expect(document.body.textContent).not.toContain('bob')

    const marks = container.querySelectorAll('mark')
    expect(marks.length).toBe(1)
    expect(marks[0].textContent).toBe('alice-1-1')

    // 搜索态是强制展开，折叠按钮不出现（避免点了也不生效的死按钮）。
    expect(container.querySelectorAll('button[aria-expanded]').length).toBe(0)
  })

  test('搜索无命中时给中性说明，而不是一张没有任何解释的空表', async () => {
    stubFetch(fixtureTree())
    const { container } = await renderRoot()

    fireEvent.change(screen.getByLabelText('搜索账号'), { target: { value: 'zzz' } })
    expect(container.querySelectorAll('tbody tr').length).toBe(0)
    expect(container.querySelectorAll('caption').length).toBe(1)
    expect(document.body.textContent).toContain('没有命中的账号')
  })

  test('500 节点 / 10 层的树，默认只渲染前两层', async () => {
    stubFetch(wideTree())
    const { container } = await renderRoot()

    // 10 层、每层规模 1/2/4/8/16/32/64/128/122/123，共 500 个节点；
    // 默认可见 depth0（1）+ depth1（2），与总节点数无关。
    expect(bodyRows(container)).toBe(3)
  })

  test('折叠状态在重新挂载后保持（本次会话内不重置）', async () => {
    stubFetch(fixtureTree())
    const first = await renderRoot()
    fireEvent.click(screen.getByRole('button', { name: '展开 alice-1' }))
    expect(bodyRows(first.container)).toBe(6)
    first.unmount()

    const second = await renderRoot()
    expect(bodyRows(second.container)).toBe(6)
    expect(document.body.textContent).toContain('alice-1-1')
    expect(screen.getByRole('button', { name: '收起 alice-1' }).getAttribute('aria-expanded')).toBe(
      'true',
    )
  })
})

/** 500 节点、10 层的构造树（每层规模 1/2/4/8/16/32/64/128/122/123）。 */
function wideTree(): AccountNode {
  const sizes = [1, 2, 4, 8, 16, 32, 64, 128, 122, 123]
  const levels = sizes.map((size, depth) =>
    Array.from({ length: size }, (_, index) => node(`n${depth}-${index}`)),
  )
  for (let depth = 1; depth < levels.length; depth += 1) {
    levels[depth - 1].forEach((parent, index) => {
      parent.children = levels[depth].slice(index * 2, index * 2 + 2)
    })
  }
  return node('root', levels[0])
}
