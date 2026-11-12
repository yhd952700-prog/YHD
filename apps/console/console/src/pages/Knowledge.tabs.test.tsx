/**
 * 知识中心标签：默认标签正确、点另一标签切换内容。
 * 只测标签切换；文档账本本身的回归在 Knowledge.test.tsx。
 *
 * @vitest-environment jsdom
 */

import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import type { AccountCaps, AccountProfile } from '../lib/accountCaps'
import { Knowledge } from './Knowledge'

const STATUS = {
  enabled: true,
  provider: 'openai-compatible',
  model: 'embed-3',
  vectors: 12,
  reason: null,
  message: '知识库已启用',
}

const DOC = {
  document_id: 'd1',
  source: 'q3-retro.md',
  title: null,
  chunks: 45,
  content_hash: 'abc',
  ingested_at: '2026-11-06 12:00:00',
}

const PAGE = { documents: [DOC], total: 1, limit: 20, offset: 0 }

function reply(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

function installFetch() {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      const path = url.split('?')[0]
      if (path === '/v1/knowledge/status') return reply(STATUS)
      if (path === '/documents') return reply(PAGE)
      if (path === '/v1/memory/entries') return reply({ entries: [], count: 0 })
      if (path === '/v1/memory/custody') return reply({ enabled: false })
      if (path === '/v1/employees') return reply({ employees: [] })
      // 企业知识库：fetchCategories 直接读 data.categories，空对象会让
      // flattenCategories 在 effect 回来后崩掉，给真形状。
      if (path === '/v1/knowledge/enterprise/status') {
        return reply({
          documents: 0,
          chunks: 0,
          categories: 0,
          expired: 0,
          expiring: 0,
          store: '',
          knowledge_store: '',
          memory_store: '',
          retrieval: 'lexical',
          supported_types: [],
          memory_scopes: [],
        })
      }
      if (path === '/v1/knowledge/enterprise/categories') return reply({ categories: [] })
      if (path === '/v1/knowledge/enterprise/expiring') {
        return reply({
          days: 30,
          counts: { expired: 0, expiring: 0 },
          documents: [],
          total: 0,
        })
      }
      if (path === '/v1/knowledge/enterprise/documents') {
        return reply({
          documents: [],
          total: 0,
          limit: 20,
          offset: 0,
          query: '',
          retrieval: null,
        })
      }
      return reply({})
    }),
  )
}

const rootProfile: AccountProfile = {
  principal: 'root',
  displayName: null,
  scope: null,
  identityPermissions: [],
  accountPermissions: [],
  isRoot: true,
  isOwner: true,
  canSelfRecover: true,
  members: [],
  memberCount: 0,
  source: 'live',
  loaded: true,
  error: null,
}

const rootCaps: AccountCaps = {
  isRoot: true,
  isOwner: true,
  canMembers: true,
  canMatrix: true,
  canTemplates: true,
  canBackup: true,
  canRecovery: true,
  canSettings: true,
  canSystemStatus: true,
  canAllKeys: true,
  canMyMembers: false,
  canSubtreeMemory: true,
  canViewAs: true,
  canGrant: false,
  canApproveQueue: true,
}

function renderKnowledge(initialTab?: 'knowledge' | 'enterprise' | 'memory') {
  return render(
    <Knowledge
      query=""
      onOpenSettings={() => {}}
      onOpenUsage={() => {}}
      initialTab={initialTab}
      profile={rootProfile}
      caps={rootCaps}
      readOnly={false}
    />,
  )
}

describe('知识中心标签', () => {
  beforeEach(() => {
    installFetch()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  test('默认打开知识中心标签', async () => {
    renderKnowledge()

    expect(screen.getByRole('tab', { name: '知识中心' }).getAttribute('aria-selected')).toBe(
      'true',
    )
    await screen.findByText('知识库状态')
  })

  test('点企业知识库切换内容，知识库状态消失', async () => {
    renderKnowledge()
    await screen.findByText('知识库状态')

    fireEvent.click(screen.getByRole('tab', { name: '企业知识库' }))

    expect(screen.getByRole('tab', { name: '企业知识库' }).getAttribute('aria-selected')).toBe(
      'true',
    )
    await screen.findByText('文档库')
    expect(screen.queryByText('知识库状态')).toBeNull()
  })

  test('点记忆切换内容', async () => {
    renderKnowledge()
    await screen.findByText('知识库状态')

    fireEvent.click(screen.getByRole('tab', { name: '记忆' }))

    expect(screen.getByRole('tab', { name: '记忆' }).getAttribute('aria-selected')).toBe('true')
    await screen.findByText('记忆代管')
    expect(screen.queryByText('知识库状态')).toBeNull()
  })

  test('initialTab=memory 直接打开记忆标签', async () => {
    renderKnowledge('memory')

    expect(screen.getByRole('tab', { name: '记忆' }).getAttribute('aria-selected')).toBe('true')
    await screen.findByText('记忆代管')
  })
})
