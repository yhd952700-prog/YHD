/**
 * 记忆中心。
 *
 * 真实数据来源：
 *   - GET /v1/memory/entries            长期记忆条目（按账号收敛，可子树、可关键词）
 *   - GET /v1/employees                 员工名册
 *   - GET /v1/employees/{name}/memories 某位员工产生的记忆（只读）
 *   - GET /v1/projects                  项目列表（仅用于说明「按项目看记忆」缺什么）
 *
 * 「按项目看记忆」本地服务**没有**对应能力：记忆条目只有 owner/text/source/tags/
 * created_at，没有 project_id；/v1/memory/entries 也没有项目过滤参数。这一栏因此
 * 如实显示「本地服务还没提供」并列出缺口，不按关键词把项目名硬匹配成归属。
 *
 * 长期记忆与正式业务数据在页面上分两块陈述：记忆只来自 account_memory_entries；
 * 目标、任务、项目、成员、审计各有自己的存储与接口，不会出现在记忆列表里。
 *
 * 路由信息先以导出常量放在本模块，等公共文件（nav.ts / App.tsx）由主线统一接。
 */
import { useState } from 'react'
import { useApi } from '../lib/api'
import type { ApiState } from '../lib/api'
import type { AccountProfile } from '../lib/accountCaps'
import type { EmployeesPayload } from '../lib/contracts'
import { employeeDisplayName, employeesOf } from '../lib/employees'
import type { ProjectSummary, ProjectsResponse } from '../lib/operator'
import { Card, Empty, Failure, Loading } from '../components/ui'
import { AccessDenied, LocalGap } from '../components/LocalGap'
import './MemoryCenter.css'

/** 供 nav.ts 后续统一接入；本轮不改公共文件。 */
export const MEMORY_CENTER_ROUTE = {
  key: 'memory-center',
  label: '记忆中心',
  hint: '按员工、按项目查看长期记忆，并与正式业务数据分开',
} as const

type Tab = 'long-term' | 'employee' | 'project'

interface EntryView {
  id: string
  text: string
  source: string
  tags: string[]
  accountId: string | null
  createdAt: string
}

const SOURCE_LABELS: Record<string, string> = {
  legacymemory_store: '旧版记忆库',
  'agent.turn': '智能体轮次',
  conversation: '对话',
  task: '任务',
  knowledge: '知识库',
  manual: '人工录入',
  user: '用户自述',
  employee: '员工执行',
  memory_store: '旧版记忆库',
  personal_context: '个人上下文',
}

function sourceLabel(value: string): string {
  const text = value.trim()
  if (!text) return '—'
  return SOURCE_LABELS[text] ?? text
}

function entryTime(value: string): string {
  const text = value.trim()
  if (!text) return '—'
  const parsed = new Date(text)
  if (Number.isNaN(parsed.getTime())) return text
  return parsed.toLocaleString('zh-CN', { hour12: false })
}

/** Unix 秒 → 本地时间；0 / 非法值如实显示「—」。 */
function unixTime(seconds: number | null | undefined): string {
  if (!Number.isFinite(seconds) || !seconds || seconds <= 0) return '—'
  const parsed = new Date(seconds * 1000)
  if (Number.isNaN(parsed.getTime())) return String(seconds)
  return parsed.toLocaleString('zh-CN', { hour12: false })
}

function toEntry(raw: unknown): EntryView | null {
  if (!raw || typeof raw !== 'object') return null
  const item = raw as Record<string, unknown>
  const text = typeof item.text === 'string' ? item.text.trim() : ''
  if (!text) return null
  return {
    id: typeof item.id === 'string' ? item.id : '',
    text,
    source: typeof item.source === 'string' ? item.source : '',
    tags: Array.isArray(item.tags)
      ? item.tags.filter((tag): tag is string => typeof tag === 'string')
      : [],
    accountId: typeof item.account_id === 'string' ? item.account_id : null,
    createdAt: typeof item.created_at === 'string' ? item.created_at : '',
  }
}

function toEntries(raw: unknown): EntryView[] {
  return Array.isArray(raw)
    ? raw.map(toEntry).filter((entry): entry is EntryView => entry !== null)
    : []
}

function toMemoryPage(raw: unknown): { entries: EntryView[]; hasMore: boolean; scope: string } {
  if (!raw || typeof raw !== 'object') return { entries: [], hasMore: false, scope: 'self' }
  const page = raw as Record<string, unknown>
  return {
    entries: toEntries(page.entries),
    hasMore: page.has_more === true,
    scope: typeof page.scope === 'string' ? page.scope : 'self',
  }
}

function EntryList({
  entries,
  emptyTitle,
  emptyHint,
}: {
  entries: EntryView[]
  emptyTitle: string
  emptyHint: string
}) {
  if (entries.length === 0) return <Empty title={emptyTitle} hint={emptyHint} />
  return (
    <ol className="mc-entries">
      {entries.map((entry, index) => (
        <li key={entry.id || `${index}-${entry.createdAt}`} className="mc-entry">
          <p className="mc-entry-text">{entry.text}</p>
          <div className="mc-entry-meta">
            <span>来源：{sourceLabel(entry.source)}</span>
            <span title={entry.createdAt}>时间：{entryTime(entry.createdAt)}</span>
            {entry.accountId && <span>账号：{entry.accountId}</span>}
            {entry.tags.map((tag) => (
              <span key={tag} className="mc-tag">
                {tag}
              </span>
            ))}
          </div>
          {entry.id && <code className="mc-entry-id">条目 {entry.id}</code>}
        </li>
      ))}
    </ol>
  )
}

function PermissionAwareList({
  state,
  what,
  emptyTitle,
  emptyHint,
  entries,
}: {
  state: ApiState<unknown>
  what: string
  emptyTitle: string
  emptyHint: string
  entries: EntryView[]
}) {
  if (state.status === 403 || state.status === 404) {
    return (
      <AccessDenied
        what={what}
        detail="本地服务对越权与不存在返回同一个中性响应，因此这里不显示「0 条记忆」。"
      />
    )
  }
  if (state.error) return <Failure message={state.error} onRetry={state.reload} />
  if (!state.data) return <Loading rows={3} />
  return <EntryList entries={entries} emptyTitle={emptyTitle} emptyHint={emptyHint} />
}

export function MemoryCenter({
  profile,
  readOnly = true,
}: {
  profile: AccountProfile
  /** 本页只做查看；保留参数是为了和现有记忆页的接线方式保持一致。 */
  readOnly?: boolean
}) {
  const [tab, setTab] = useState<Tab>('long-term')
  const [query, setQuery] = useState('')
  const [applied, setApplied] = useState('')
  const [scope, setScope] = useState<'self' | 'subtree'>('self')
  const [employeeName, setEmployeeName] = useState('')

  const effectiveScope = profile.isRoot ? scope : 'self'
  const memoryPath =
    tab === 'long-term'
      ? `/v1/memory/entries?scope=${effectiveScope}&q=${encodeURIComponent(applied)}&limit=100`
      : null
  const memoryState = useApi<unknown>(memoryPath, 0)
  const memoryPage = toMemoryPage(memoryState.data)

  const roster = useApi<EmployeesPayload>(tab === 'employee' ? '/v1/employees' : null, 0)
  const employeeList = employeesOf(roster.data)

  const employeeMemoryState = useApi<unknown>(
    tab === 'employee' && employeeName
      ? `/v1/employees/${encodeURIComponent(employeeName)}/memories`
      : null,
    0,
  )
  const employeeMemoryRaw = employeeMemoryState.data as
    | { memories?: unknown; owner_account_id?: unknown; employee?: { display_name?: string } }
    | null
  const employeeEntries = toEntries(employeeMemoryRaw?.memories)

  const projects = useApi<ProjectsResponse>(tab === 'project' ? '/v1/projects' : null, 0)

  return (
    <div className="os-grid mc-page">
      <Card
        title="记忆中心"
        sub="长期记忆只读展示；每一条都能看到来源、时间与条目 id"
        span={12}
      >
        <div className="mc-tabs" role="tablist" aria-label="记忆视图">
          <button
            type="button"
            role="tab"
            aria-selected={tab === 'long-term'}
            className={`mc-tab${tab === 'long-term' ? ' mc-tab-active' : ''}`}
            onClick={() => setTab('long-term')}
          >
            长期记忆
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === 'employee'}
            className={`mc-tab${tab === 'employee' ? ' mc-tab-active' : ''}`}
            onClick={() => setTab('employee')}
          >
            按员工
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === 'project'}
            className={`mc-tab${tab === 'project' ? ' mc-tab-active' : ''}`}
            onClick={() => setTab('project')}
          >
            按项目
          </button>
        </div>
        {readOnly && (
          <p className="mc-hint">
            本页只读：不在界面里改写或删除记忆。写入与导出仍走原有的记忆页。
          </p>
        )}
      </Card>

      {tab === 'long-term' && (
        <Card
          title="长期记忆"
          sub="来自 GET /v1/memory/entries，按账号隔离；不是正式业务数据库"
          span={12}
        >
          <div className="mc-filters">
            <input
              className="os-input-plain"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') setApplied(query.trim())
              }}
              placeholder="按正文、来源或标签检索记忆"
              aria-label="记忆检索输入"
            />
            <button
              type="button"
              className="os-btn"
              onClick={() => setApplied(query.trim())}
            >
              检索
            </button>
            {profile.isRoot && (
              <select
                className="os-input-plain mc-scope"
                value={scope}
                aria-label="记忆范围"
                onChange={(event) => setScope(event.target.value === 'subtree' ? 'subtree' : 'self')}
              >
                <option value="self">我的记忆</option>
                <option value="subtree">整棵子树</option>
              </select>
            )}
          </div>

          {memoryState.status === 403 || memoryState.status === 404 ? (
            <AccessDenied
              what="长期记忆"
              detail="本地服务对越权与不存在返回同一个中性响应，因此这里不显示「0 条记忆」。"
            />
          ) : memoryState.error ? (
            <Failure message={memoryState.error} onRetry={memoryState.reload} />
          ) : !memoryState.data ? (
            <Loading rows={4} />
          ) : (
            <>
              <p className="mc-hint">
                {memoryPage.scope === 'subtree' ? '整棵子树' : '我的记忆'} ·{' '}
                {memoryPage.entries.length} 条
                {memoryPage.hasMore ? ' · 还有更多，请用检索缩小范围' : ''}
                {applied ? ` · 关键词「${applied}」` : ''}
              </p>
              <EntryList
                entries={memoryPage.entries}
                emptyTitle="没有读到记忆条目"
                emptyHint="这张表只显示记忆库里的真实条目；换一个关键词，或确认这个账号还没有产生记忆。"
              />
            </>
          )}
        </Card>
      )}

      {tab === 'employee' && (
        <>
          <Card title="员工名册" sub="只列当前账号可见的员工" span={4}>
            {roster.status === 403 || roster.status === 404 ? (
              <AccessDenied what="员工名册" />
            ) : roster.error ? (
              <Failure message={roster.error} onRetry={roster.reload} />
            ) : !roster.data ? (
              <Loading rows={4} />
            ) : employeeList.length === 0 ? (
              <Empty title="没有可见的员工" hint="先到「我的员工」创建员工，或确认当前账号的可见范围。" />
            ) : (
              <ul className="mc-employees">
                {employeeList.map((employee) => (
                  <li key={employee.name}>
                    <button
                      type="button"
                      className={`mc-employee${employeeName === employee.name ? ' mc-employee-active' : ''}`}
                      aria-pressed={employeeName === employee.name}
                      onClick={() => setEmployeeName(employee.name)}
                    >
                      {employeeDisplayName(employee)}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <Card
            title={employeeName ? `员工记忆 · ${employeeName}` : '员工记忆'}
            sub="GET /v1/employees/{name}/memories，只读"
            span={8}
          >
            {!employeeName ? (
              <Empty title="先选择一位员工" hint="左侧点名册里的员工后，这里显示他产生的记忆条目。" />
            ) : (
              <PermissionAwareList
                state={employeeMemoryState}
                what={`「${employeeName}」的记忆`}
                emptyTitle="这位员工还没有记忆条目"
                emptyHint="员工执行任务时产生的记忆会带 employee 来源与该员工的稳定标记；没有就是没有。"
                entries={employeeEntries}
              />
            )}
          </Card>
        </>
      )}

      {tab === 'project' && (
        <>
          <Card title="按项目查看记忆" sub="项目列表是真实数据；项目记忆视图本地服务还没有" span={12}>
            <LocalGap
              title="「按项目看记忆」还没有接口"
              summary="长期记忆条目只有归属账号、正文、来源、标签与创建时间，没有项目归属；因此无法把条目可靠地归到某个项目下。"
              missing={[
                '缺少项目维度：记忆条目表没有项目标识字段，写入记忆也不接收项目标识。',
                '缺少项目过滤：长期记忆列表只有权限范围、关键词与分页参数，没有项目标识过滤。',
                '缺少项目记忆端点：本地服务不存在按项目返回记忆的接口。',
              ]}
              note="下面列出的项目本身是真实数据；每个项目的「记忆」列只能如实标注未提供。"
            />

            {projects.status === 403 || projects.status === 404 ? (
              <AccessDenied what="项目列表" />
            ) : projects.error ? (
              <Failure message={projects.error} onRetry={projects.reload} />
            ) : !projects.data ? (
              <Loading rows={4} />
            ) : projects.data.projects.length === 0 ? (
              <Empty title="还没有项目" hint="先到「项目」创建项目；记忆与项目的关联能力仍需本地服务补齐。" />
            ) : (
              <div className="os-table-wrap mc-project-table">
                <table className="os-table">
                  <thead>
                    <tr>
                      <th>项目</th>
                      <th>负责人</th>
                      <th>目标数</th>
                      <th>最近活动</th>
                      <th>项目记忆</th>
                    </tr>
                  </thead>
                  <tbody>
                    {projects.data.projects.map((project: ProjectSummary) => (
                      <tr key={project.project_id}>
                        <td>
                          <strong>{project.name}</strong>
                          <code className="mc-entry-id">{project.project_id}</code>
                        </td>
                        <td>{project.creator ?? '—'}</td>
                        <td>{project.goal_count}</td>
                        <td>{unixTime(project.last_activity_at)}</td>
                        <td>
                          <span className="os-badge" data-tone="warn">
                            本地服务还没提供
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </>
      )}

      <Card
        title="数据边界：长期记忆 ≠ 正式业务数据"
        sub="两块数据分开存放、分开读取，页面上也不混在一起"
        span={12}
      >
        <div className="mc-boundary">
          <section className="mc-boundary-side">
            <h3>长期记忆</h3>
            <p>
              存放位置：<code>account_memory_entries</code>，通过{' '}
              <code>GET /v1/memory/entries</code> 读取。
            </p>
            <ul>
              <li>字段：条目 id、归属账号、正文、来源、标签与创建时间。</li>
              <li>按账号隔离；主账号可只读查看整棵子树。</li>
              <li>员工记忆是同一张表里带员工来源与员工标记的条目。</li>
            </ul>
          </section>
          <section className="mc-boundary-side">
            <h3>正式业务数据</h3>
            <p>
              目标与任务、项目、账号与权限、审计各自有独立的存储与接口（如{' '}
              <code>/v1/goals</code>、<code>/v1/projects</code>、<code>/v1/accounts</code>、
              <code>/v1/audit</code>）。
            </p>
            <ul>
              <li>不会出现在记忆列表里，也不能在记忆中心改写。</li>
              <li>核对或修改它们，请到对应页面操作。</li>
              <li>本页只用项目名说明「按项目看记忆」的缺口，不把项目数据当成记忆。</li>
            </ul>
          </section>
        </div>
      </Card>
    </div>
  )
}
