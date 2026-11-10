/**
 * 技能中心 —— 列表 / 创建 / 导入 / 编辑 / 删除 / 测试，以及"谁在用、来源是谁"。
 *
 * 事实边界
 * --------
 * 当前主仓本地服务**没有**技能注册表 HTTP 接口（`/v1/skills` 不存在）。因此本页
 * 不用任何示例技能填充列表：GET 返回 404 时整块显示「本地服务还没提供」，写入
 * 动作一并禁用，避免让人点了才发现服务端没有这个能力。
 *
 * 字段级同样诚实：`used_by` / `permissions` / `enabled` 没返回时显示「未提供」，
 * 不把"没返回"说成"没有员工使用"或"无需权限"。
 *
 * 接线（本轮按要求不改公共文件）
 * -----------------------------
 * 路由信息放在 `SKILLS_CENTER_PAGE`，`nav.ts` / `App.tsx` 统一接线时直接引用：
 *   nav.ts  → PRIMARY_NAV.push({ key: SKILLS_CENTER_PAGE.key, label: SKILLS_CENTER_PAGE.navLabel, icon: ... })
 *   App.tsx → PAGE_META[SKILLS_CENTER_PAGE.key] = { title, sub }
 *             {shown === SKILLS_CENTER_PAGE.key && <SkillCenter query={query} />}
 */

import { useCallback, useMemo, useState } from 'react'
import './SkillsTools.css'
import { ApiError, apiFetch, useApi } from '../lib/api'
import { Card, Empty, Failure, Loading } from '../components/ui'
import {
  FIELD_NOT_PROVIDED,
  NOT_PROVIDED,
  NOT_PROVIDED_DETAIL,
  RISK_LABELS,
  probeState,
  readSkills,
  riskTone,
  sourceLabel,
  type SkillRecord,
} from '../lib/skillsTools'

/** 页面登记信息：等 `nav.ts` / `App.tsx` 统一接线时直接引用。 */
export const SKILLS_CENTER_PAGE = {
  key: 'skills-center',
  navLabel: '技能中心',
  title: '技能中心',
  sub: '技能的来源、权限、风险与使用情况；拿不到的数据如实标注',
} as const

interface Feedback {
  tone: 'ok' | 'danger'
  text: string
}

function isMissingEndpoint(status: number): boolean {
  return status === 404 || status === 405 || status === 501
}

/** 测试接口的结论：只认本地服务给的字段，缺了就直说没给结论。 */
function describeTestResult(payload: unknown): string {
  if (!payload || typeof payload !== 'object') return '服务没有返回测试结论。'
  const record = payload as Record<string, unknown>
  const detail =
    typeof record.detail === 'string'
      ? record.detail
      : typeof record.message === 'string'
        ? record.message
        : ''
  if (typeof record.passed === 'boolean') {
    return `${record.passed ? '通过' : '未通过'}${detail ? `：${detail}` : ''}`
  }
  if (typeof record.ok === 'boolean') {
    return `${record.ok ? '通过' : '未通过'}${detail ? `：${detail}` : ''}`
  }
  return detail || '服务没有返回测试结论。'
}

export function SkillCenter({ query = '' }: { query?: string }) {
  const skills = useApi<unknown>('/v1/skills', 0)
  const state = probeState(skills)
  const records = useMemo(() => readSkills(skills.data), [skills.data])

  const [busy, setBusy] = useState<string | null>(null)
  const [feedback, setFeedback] = useState<Feedback | null>(null)
  const [draft, setDraft] = useState({ name: '', description: '' })
  const [importFile, setImportFile] = useState<File | null>(null)
  const [importNotice, setImportNotice] = useState<string | null>(null)
  const [editing, setEditing] = useState<{ id: string; name: string; description: string } | null>(
    null,
  )

  const writable = state === 'ready'
  const missingHint = writable
    ? undefined
    : `${NOT_PROVIDED}：技能接口不可用，本页不会提交任何写入。`

  /** 执行一次写入动作；返回是否成功，编辑框只在成功时收起。 */
  const run = useCallback(
    async (
      label: string,
      request: () => Promise<unknown>,
      describe?: (result: unknown) => string,
    ): Promise<boolean> => {
      setBusy(label)
      setFeedback(null)
      try {
        const result = await request()
        const extra = describe ? describe(result) : ''
        setFeedback({ tone: 'ok', text: `${label}已完成。${extra}` })
        void skills.reload()
        return true
      } catch (err) {
        const status = err instanceof ApiError ? err.status : 0
        setFeedback({
          tone: 'danger',
          text: isMissingEndpoint(status)
            ? `${NOT_PROVIDED}（${label}）`
            : err instanceof Error
              ? `${label}没有完成：${err.message}`
              : `${label}没有完成，请稍后重试。`,
        })
        return false
      } finally {
        setBusy(null)
      }
    },
    [skills],
  )

  const need = query.trim().toLowerCase()
  const shown = useMemo(() => {
    if (!need) return records
    return records.filter((record) =>
      [record.name, record.id, record.description, record.source]
        .join(' ')
        .toLowerCase()
        .includes(need),
    )
  }, [records, need])

  const highRisk = records.filter((record) => record.risk === 'high')
  const usageMissing = records.some((record) => record.usedBy === null)

  return (
    <div className="os-grid">
      <Card title="说明" sub="这个页面只用真实数据说话" span={12}>
        <ul className="os-list">
          <li>技能清单来自本地服务的技能接口；没有接口就显示「{NOT_PROVIDED}」，不会用示例条目占位。</li>
          <li>每一条都要能回答三件事：来源是谁、需要哪些权限、风险等级是多少。缺哪项就标注哪项。</li>
          <li>高风险技能在列表与启用前检查里都会单独提示；风险未知时不允许当作安全。</li>
          <li>「被哪些员工使用」由本地服务关联数据提供；本地服务没返回时显示「未提供」，不会从员工能力清单里猜。</li>
          <li>写入动作（创建 / 导入 / 编辑 / 删除 / 测试）在接口不可用时保持禁用并说明原因。</li>
        </ul>
      </Card>

      <Card title="技能列表" sub="真实注册的技能；每 30 秒刷新一次" span={12}>
        {state === 'loading' && <Loading />}
        {state === 'not-provided' && (
          <div className="st-unprovided" data-testid="skills-unprovided" role="status">
            <strong>{NOT_PROVIDED}</strong>
            <span>{NOT_PROVIDED_DETAIL}</span>
            <span className="os-hint">已核对接口：GET /v1/skills（返回 404）</span>
            <button className="os-btn os-btn-ghost" type="button" onClick={skills.reload}>
              重新检查
            </button>
          </div>
        )}
        {(state === 'failed' || state === 'forbidden') && (
          <Failure message={skills.error ?? NOT_PROVIDED} onRetry={skills.reload} />
        )}
        {state === 'ready' && shown.length === 0 && (
          <Empty
            title={need ? '没有匹配的技能' : '还没有技能'}
            hint={
              need
                ? `搜索「${query}」没有命中；换个关键词，或清空搜索后再看。`
                : '本地服务返回了空的技能列表。可以先用右侧「新建技能」创建一条，或导入已有技能。'
            }
          />
        )}
        {state === 'ready' && shown.length > 0 && (
          <>
            {highRisk.length > 0 && (
              <p className="st-warning" data-testid="skills-high-risk" role="alert">
                有 {highRisk.length} 个高风险技能。启用前请确认来源可信、权限范围可接受；
                高风险技能不会在国内部默认启用。
              </p>
            )}
            <div className="os-table-wrap">
              <table className="os-table">
                <thead>
                  <tr>
                    <th>技能</th>
                    <th>来源</th>
                    <th>版本</th>
                    <th>风险</th>
                    <th>需要权限</th>
                    <th>被哪些员工使用</th>
                    <th>状态</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {shown.map((record: SkillRecord) => (
                    <tr key={record.id}>
                      <td>
                        <strong>{record.name}</strong>
                        {record.description && (
                          <div className="st-sub">{record.description}</div>
                        )}
                        <div className="st-sub">{record.id}</div>
                      </td>
                      <td>{sourceLabel(record.source)}</td>
                      <td>{record.version || FIELD_NOT_PROVIDED}</td>
                      <td>
                        <span
                          className={`st-chip st-tone-${riskTone(record.risk)}`}
                          title={record.risk === 'unknown' ? '本地服务没有返回风险等级' : undefined}
                        >
                          {RISK_LABELS[record.risk]}
                        </span>
                      </td>
                      <td>
                        {record.permissions === null
                          ? FIELD_NOT_PROVIDED
                          : record.permissions.length === 0
                            ? '无需额外权限'
                            : record.permissions.join('、')}
                      </td>
                      <td>
                        {record.usedBy === null
                          ? FIELD_NOT_PROVIDED
                          : record.usedBy.length === 0
                            ? '暂无员工使用'
                            : record.usedBy.join('、')}
                      </td>
                      <td>
                        {record.enabled === null
                          ? FIELD_NOT_PROVIDED
                          : record.enabled
                            ? '已启用'
                            : '未启用'}
                      </td>
                      <td>
                        <div className="st-actions">
                          <button
                            className="os-btn"
                            type="button"
                            disabled={!writable || busy !== null}
                            title={missingHint}
                            onClick={() =>
                              void run(
                                '测试技能',
                                () =>
                                  apiFetch<unknown>(
                                    `/v1/skills/${encodeURIComponent(record.id)}/test`,
                                    { method: 'POST' },
                                  ),
                                describeTestResult,
                              )
                            }
                          >
                            测试
                          </button>
                          <button
                            className="os-btn"
                            type="button"
                            disabled={!writable || busy !== null}
                            title={missingHint}
                            onClick={() =>
                              setEditing({
                                id: record.id,
                                name: record.name,
                                description: record.description,
                              })
                            }
                          >
                            编辑
                          </button>
                          <button
                            className="os-btn os-btn-ghost"
                            type="button"
                            disabled={!writable || busy !== null}
                            title={missingHint}
                            onClick={() =>
                              void run('删除技能', () =>
                                apiFetch<unknown>(`/v1/skills/${encodeURIComponent(record.id)}`, {
                                  method: 'DELETE',
                                }),
                              )
                            }
                          >
                            删除
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {usageMissing && (
              <p className="os-hint" data-testid="skills-usage-note">
                「被哪些员工使用」有字段未返回：本地服务没有提供技能与员工的关联数据，
                本页不会用员工能力清单推测。
              </p>
            )}
            {need && (
              <p className="os-hint">
                搜索「{query}」过滤后显示 {shown.length} / {records.length} 项。
              </p>
            )}
          </>
        )}
      </Card>

      <Card title="新建技能" sub="来源与权限由服务端记录" span={6}>
        <form
          className="st-form"
          onSubmit={(event) => {
            event.preventDefault()
            void run('创建技能', () =>
              apiFetch<unknown>('/v1/skills', {
                method: 'POST',
                body: JSON.stringify({
                  name: draft.name.trim(),
                  description: draft.description.trim(),
                }),
              }),
            )
          }}
        >
          <label className="st-field">
            <span>技能名称</span>
            <input
              className="st-input"
              value={draft.name}
              onChange={(event) => setDraft((prev) => ({ ...prev, name: event.target.value }))}
              placeholder="例如：周报整理"
              disabled={!writable}
            />
          </label>
          <label className="st-field">
            <span>技能说明</span>
            <textarea
              className="st-input"
              rows={3}
              value={draft.description}
              onChange={(event) =>
                setDraft((prev) => ({ ...prev, description: event.target.value }))
              }
              placeholder="它会做什么、在什么场景下使用"
              disabled={!writable}
            />
          </label>
          <button
            className="os-btn os-btn-primary"
            type="submit"
            disabled={!writable || busy !== null || !draft.name.trim()}
            title={missingHint}
          >
            {busy === '创建技能' ? '创建中…' : '创建技能'}
          </button>
          {!writable && <p className="os-hint">{missingHint}</p>}
        </form>
      </Card>

      <Card title="导入技能" sub="只接受本地技能包；导入后会显示真实来源" span={6}>
        <div className="st-form">
          <label className="st-field">
            <span>技能包文件</span>
            <input
              className="st-input"
              type="file"
              accept=".md,.json"
              disabled={!writable}
              onChange={(event) => {
                const input = event.target
                const file = input.files?.[0] ?? null
                if (file && !/\.(md|json)$/i.test(file.name)) {
                  // 不接受就明确说出来，不能静默忽略。
                  setImportFile(null)
                  setImportNotice(
                    /\.zip$/i.test(file.name)
                      ? '当前版本不支持导入压缩包（.zip）：本地服务出于安全考虑不做解包，避免路径穿越（zip-slip）和「导入即执行」风险。请改用 .md 或 .json 单文件技能包。'
                      : '只支持 .md 或 .json 单文件技能包，请重新选择文件。',
                  )
                  input.value = ''
                  return
                }
                setImportNotice(null)
                setImportFile(file)
              }}
            />
          </label>
          <button
            className="os-btn os-btn-primary"
            type="button"
            disabled={!writable || busy !== null || importFile === null}
            title={missingHint}
            onClick={() => {
              if (!importFile) return
              const body = new FormData()
              body.append('file', importFile)
              void run('导入技能', () =>
                apiFetch<unknown>('/v1/skills/import', { method: 'POST', body }),
              )
            }}
          >
            {busy === '导入技能' ? '导入中…' : '导入技能'}
          </button>
          {importNotice && (
            <p className="st-warning" role="alert" data-testid="skills-import-notice">
              {importNotice}
            </p>
          )}
          <p className="os-hint">
            只支持 .md 或 .json 单文件技能包。当前版本不支持压缩包（.zip）导入：本地服务出于安全考虑不做解包，
            避免路径穿越与「导入即执行」风险。导入前请确认文件来源，风险等级由服务端判定。
          </p>
          {!writable && <p className="os-hint">{missingHint}</p>}
        </div>
      </Card>

      {editing && (
        <Card title={`编辑技能：${editing.name}`} span={12}>
          <form
            className="st-form"
            onSubmit={(event) => {
              event.preventDefault()
              const target = editing
              void (async () => {
                const ok = await run('保存技能', () =>
                  apiFetch<unknown>(`/v1/skills/${encodeURIComponent(target.id)}`, {
                    method: 'PATCH',
                    body: JSON.stringify({
                      name: target.name.trim(),
                      description: target.description.trim(),
                    }),
                  }),
                )
                if (ok) setEditing(null)
              })()
            }}
          >
            <label className="st-field">
              <span>技能名称</span>
              <input
                className="st-input"
                value={editing.name}
                onChange={(event) =>
                  setEditing((prev) => (prev ? { ...prev, name: event.target.value } : prev))
                }
              />
            </label>
            <label className="st-field">
              <span>技能说明</span>
              <textarea
                className="st-input"
                rows={3}
                value={editing.description}
                onChange={(event) =>
                  setEditing((prev) =>
                    prev ? { ...prev, description: event.target.value } : prev,
                  )
                }
              />
            </label>
            <div className="st-actions">
              <button
                className="os-btn os-btn-primary"
                type="submit"
                disabled={busy !== null || !editing.name.trim()}
              >
                保存
              </button>
              <button
                className="os-btn os-btn-ghost"
                type="button"
                onClick={() => setEditing(null)}
              >
                取消
              </button>
            </div>
          </form>
        </Card>
      )}

      {feedback && (
        <Card title="操作结果" span={12}>
          <p
            className={feedback.tone === 'danger' ? 'os-error' : 'os-hint'}
            role={feedback.tone === 'danger' ? 'alert' : 'status'}
            data-testid="skills-feedback"
          >
            {feedback.text}
          </p>
        </Card>
      )}
    </div>
  )
}
