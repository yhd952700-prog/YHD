/**
 * 技能中心 —— 列表 / 创建 / 导入 / 编辑 / 启停 / 绑定 / 删除 / 测试。
 *
 * 事实边界
 * --------
 * * 技能来自本地服务 ``/v1/skills``；接口不可用时整块显示「本地服务还没提供」，
 *   写入动作一并禁用，避免让人点了才发现服务端没有这个能力。
 * * 「被哪些员工使用」读的是真实绑定表；后端没返回时显示「未提供」，不会从员工
 *   能力清单里猜。能力交集只以「可能相关（推导）」出现。
 * * 导入不自动启用：导入成功后显示 ``import_report``（哪些字段映射了、哪些只存了
 *   原文、哪些不支持），由用户显式启用。
 * * 带脚本的技能会标「含脚本，本机未启用」：本版不执行任何脚本。
 * * MCP 配置不是技能：导入会被后端以 415 拒绝，页面提示去工具中心配置。
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
  formatLabel,
  probeState,
  readSkills,
  riskTone,
  sourceLabel,
  type SkillImportReportRow,
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

interface BindingRow {
  skill_id: string
  employee: string
  created_at?: string
  created_by?: string
}

/** 导入报告里的处理方式；未知状态原样显示，不硬翻译。 */
const IMPORT_STATUS_LABELS: Record<string, string> = {
  mapped: '已映射到内部字段',
  stored_raw: '只保存原文（本机未支持）',
  unsupported: '不支持，仅存原文',
  defaulted: '缺失，已兜底',
  absent: '未提供',
  empty: '为空',
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
  const bindingsApi = useApi<unknown>('/v1/skills/bindings', 0)
  const bindings = useMemo<BindingRow[]>(() => {
    const payload = bindingsApi.data as { bindings?: unknown } | null
    const list = Array.isArray(payload?.bindings) ? (payload?.bindings as BindingRow[]) : []
    return list.filter((row) => Boolean(row) && typeof row === 'object')
  }, [bindingsApi.data])

  const [busy, setBusy] = useState<string | null>(null)
  const [feedback, setFeedback] = useState<Feedback | null>(null)
  const [draft, setDraft] = useState({ name: '', description: '' })
  const [importFile, setImportFile] = useState<{ file: File; name: string } | null>(null)
  const [importNotice, setImportNotice] = useState<string | null>(null)
  const [importResult, setImportResult] = useState<{
    name: string
    note: string
    report: SkillImportReportRow[]
  } | null>(null)
  const [bindForm, setBindForm] = useState({ skillId: '', employee: '' })
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

  /** 启用/停用：高风险或风险未知时必须显式确认，后端也会再校验一次。 */
  const toggleEnabled = useCallback(
    (record: SkillRecord) => {
      const next = !record.enabled
      if (next && (record.risk === 'high' || record.risk === 'unknown')) {
        const ok = window.confirm(
          `「${record.name}」的风险为「${RISK_LABELS[record.risk]}」，确认启用？`,
        )
        if (!ok) return
      }
      void run(next ? '启用技能' : '停用技能', () =>
        apiFetch<unknown>(
          `/v1/skills/${encodeURIComponent(record.id)}/${next ? 'enable' : 'disable'}`,
          {
            method: 'POST',
            body: next
              ? JSON.stringify({
                  confirm: true,
                  acknowledge_risk: record.risk,
                })
              : undefined,
          },
        ),
      )
    },
    [run],
  )

  const bindEmployee = useCallback(() => {
    const skillId = bindForm.skillId.trim()
    const employee = bindForm.employee.trim()
    if (!skillId || !employee) return
    void (async () => {
      const ok = await run('绑定技能', () =>
        apiFetch<unknown>(`/v1/skills/${encodeURIComponent(skillId)}/bind`, {
          method: 'POST',
          body: JSON.stringify({ employee }),
        }),
      )
      if (ok) void bindingsApi.reload()
    })()
  }, [bindForm, bindingsApi, run])

  const unbindEmployee = useCallback(
    (skillId: string, employee: string) => {
      void (async () => {
        const ok = await run('解除绑定', () =>
          apiFetch<unknown>(
            `/v1/skills/${encodeURIComponent(skillId)}/bind/${encodeURIComponent(employee)}`,
            { method: 'DELETE' },
          ),
        )
        if (ok) void bindingsApi.reload()
      })()
    },
    [bindingsApi, run],
  )

  const doImport = useCallback(async () => {
    if (!importFile) return
    setBusy('导入技能')
    setFeedback(null)
    setImportResult(null)
    setImportNotice(null)
    try {
      const body = new FormData()
      // 目录导入时保留相对路径，后端才能按目录名兜底技能名。
      body.append('file', importFile.file, importFile.name)
      const result = (await apiFetch<Record<string, unknown>>('/v1/skills/import', {
        method: 'POST',
        body,
      })) as Record<string, unknown>
      setImportResult({
        name: String(result.name ?? ''),
        note: String(result.import_note ?? ''),
        report: Array.isArray(result.import_report)
          ? (result.import_report as SkillImportReportRow[])
          : [],
      })
      setFeedback({
        tone: 'ok',
        text: `导入技能已完成：${String(result.name ?? '')}（默认未启用，请检查导入报告后手动启用）。`,
      })
      void skills.reload()
    } catch (err) {
      const status = err instanceof ApiError ? err.status : 0
      const message = err instanceof Error ? err.message : '导入没有完成，请稍后重试。'
      setImportNotice(
        isMissingEndpoint(status)
          ? `${NOT_PROVIDED}（导入技能）`
          : status === 415
            ? `该文件不是技能：${message}`
            : `导入没有完成：${message}`,
      )
    } finally {
      setBusy(null)
    }
  }, [importFile, skills])

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
          <li>「绑定员工」读真实绑定表；读不出来显示「未提供」，不会把能力交集说成绑定。能力交集只以「可能相关（推导）」出现。</li>
          <li>导入的技能默认未启用，由你显式启用；含脚本的技能可以登记，但本机不执行脚本。</li>
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
                    <th>来源 / 格式</th>
                    <th>风险</th>
                    <th>需要权限</th>
                    <th>绑定员工</th>
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
                      <td>
                        <div>{sourceLabel(record.source)}</div>
                        <div className="st-sub">{formatLabel(record.format)}</div>
                        {record.version && <div className="st-sub">版本：{record.version}</div>}
                        {record.license && <div className="st-sub">许可：{record.license}</div>}
                        {record.allowedTools.length > 0 && (
                          <div className="st-sub" title="已登记；本版不执行脚本，不产生权限">
                            声明工具：{record.allowedTools.join('、')}（未启用）
                          </div>
                        )}
                      </td>
                      <td>
                        <span
                          className={`st-chip st-tone-${riskTone(record.risk)}`}
                          title={record.risk === 'unknown' ? '风险无法核对（能力目录缺失或声明了不存在的能力）' : undefined}
                        >
                          {RISK_LABELS[record.risk]}
                        </span>
                        {record.missingCapabilities.length > 0 && (
                          <div className="st-sub">
                            本机缺少能力：{record.missingCapabilities.join('、')}
                          </div>
                        )}
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
                            ? '暂无员工绑定'
                            : record.usedBy.join('、')}
                        {record.relatedEmployees && record.relatedEmployees.length > 0 && (
                          <div className="st-sub" title="由员工默认能力与技能声明能力求交集推导，仅表示可能相关，不是绑定">
                            可能相关（推导）：{record.relatedEmployees.join('、')}
                          </div>
                        )}
                      </td>
                      <td>
                        <div>
                          {record.enabled === null
                            ? FIELD_NOT_PROVIDED
                            : record.enabled
                              ? '已启用'
                              : '未启用'}
                        </div>
                        {record.hasScripts && (
                          <div className="st-sub" title={record.scriptRefs.join('、')}>
                            含脚本，本机未启用
                          </div>
                        )}
                      </td>
                      <td>
                        <div className="st-actions">
                          <button
                            className="os-btn"
                            type="button"
                            disabled={!writable || busy !== null}
                            title={missingHint}
                            onClick={() => toggleEnabled(record)}
                          >
                            {record.enabled ? '停用' : '启用'}
                          </button>
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
                有技能的「绑定员工」没读出来：本地服务返回了 null（绑定表可能被占用或损坏），
                这不等于「没有员工绑定」，本页也不会用能力清单推测成绑定关系。
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

      <Card title="导入技能" sub="Agent Skills（SKILL.md）或本机 JSON 清单；导入后默认未启用" span={6}>
        <div className="st-form">
          <label className="st-field">
            <span>技能文件</span>
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
                      : '只支持 .md 或 .json 技能文件，请重新选择。',
                  )
                  input.value = ''
                  return
                }
                setImportNotice(null)
                setImportResult(null)
                setImportFile(file ? { file, name: file.name } : null)
              }}
            />
          </label>
          <label className="st-field">
            <span>或选择整个技能目录（用于按目录名兜底技能名）</span>
            <input
              className="st-input"
              type="file"
              accept=".md,.json"
              disabled={!writable}
              // React 没有 webkitdirectory 的类型；用展开属性表达"目录选择"。
              {...({ webkitdirectory: '', directory: '' } as Record<string, string>)}
              onChange={(event) => {
                const input = event.target
                const file = input.files?.[0] ?? null
                if (!file || !/\.(md|json)$/i.test(file.name)) {
                  setImportFile(null)
                  setImportNotice('目录里没有可导入的 .md 或 .json 技能文件。')
                  input.value = ''
                  return
                }
                const relative =
                  (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name
                setImportNotice(null)
                setImportResult(null)
                setImportFile({ file, name: relative })
              }}
            />
          </label>
          <button
            className="os-btn os-btn-primary"
            type="button"
            disabled={!writable || busy !== null || importFile === null}
            title={missingHint}
            onClick={() => void doImport()}
          >
            {busy === '导入技能' ? '导入中…' : '导入技能'}
          </button>
          {importNotice && (
            <p className="st-warning" role="alert" data-testid="skills-import-notice">
              {importNotice}
            </p>
          )}
          <p className="os-hint">
            只支持 Agent Skills 的 SKILL.md（读 YAML frontmatter 的 name / description /
            license / allowed-tools / metadata）与本机 JSON 清单。压缩包不导入：本地服务不做解包，
            避免路径穿越与「导入即执行」风险。MCP server 配置请到工具中心配置，技能中心会以 415 拒绝。
          </p>
          {!writable && <p className="os-hint">{missingHint}</p>}
        </div>
      </Card>

      {importResult && (
        <Card title={`导入报告：${importResult.name || '未命名技能'}`} span={6}>
          {importResult.note && <p className="os-hint">{importResult.note}</p>}
          {importResult.report.length === 0 ? (
            <p className="os-hint">服务没有返回字段级导入报告。</p>
          ) : (
            <div className="os-table-wrap">
              <table className="os-table">
                <thead>
                  <tr>
                    <th>字段</th>
                    <th>处理方式</th>
                    <th>说明</th>
                  </tr>
                </thead>
                <tbody>
                  {importResult.report.map((row, index) => (
                    <tr key={`${row.field}-${index}`}>
                      <td>{row.field}</td>
                      <td>{IMPORT_STATUS_LABELS[row.status] ?? row.status}</td>
                      <td>{row.note}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      )}

      <Card title="员工绑定" sub="真实绑定表：员工 ↔ 技能，多对多" span={6}>
        <div className="st-form">
          <label className="st-field">
            <span>选择技能</span>
            <select
              className="st-input"
              value={bindForm.skillId}
              disabled={!writable}
              onChange={(event) =>
                setBindForm((prev) => ({ ...prev, skillId: event.target.value }))
              }
            >
              <option value="">请选择</option>
              {records.map((record) => (
                <option key={record.id} value={record.id}>
                  {record.name}
                </option>
              ))}
            </select>
          </label>
          <label className="st-field">
            <span>员工名</span>
            <input
              className="st-input"
              value={bindForm.employee}
              disabled={!writable}
              placeholder="必须与员工库里的名字一致"
              onChange={(event) =>
                setBindForm((prev) => ({ ...prev, employee: event.target.value }))
              }
            />
          </label>
          <button
            className="os-btn os-btn-primary"
            type="button"
            disabled={!writable || busy !== null || !bindForm.skillId || !bindForm.employee.trim()}
            title={missingHint}
            onClick={bindEmployee}
          >
            绑定
          </button>
          <p className="os-hint">
            绑定关系会记录创建时间与操作者。列表里每行的「绑定员工」即来自这张表；
            删除技能前必须先解除全部绑定。
          </p>
          {probeState(bindingsApi) === 'failed' && (
            <p className="os-error" role="alert">
              绑定表读取失败：{bindingsApi.error ?? '未知错误'}。本页不会把它当成「没有绑定」。
            </p>
          )}
          {bindings.length === 0 ? (
            <p className="os-hint">还没有任何员工绑定技能。</p>
          ) : (
            <div className="os-table-wrap">
              <table className="os-table">
                <thead>
                  <tr>
                    <th>员工</th>
                    <th>技能</th>
                    <th>绑定时间</th>
                    <th>操作者</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {bindings.map((row) => (
                    <tr key={`${row.skill_id}-${row.employee}`}>
                      <td>{row.employee}</td>
                      <td>
                        {records.find((record) => record.id === row.skill_id)?.name ?? row.skill_id}
                      </td>
                      <td>{row.created_at ?? FIELD_NOT_PROVIDED}</td>
                      <td>{row.created_by ?? FIELD_NOT_PROVIDED}</td>
                      <td>
                        <button
                          className="os-btn os-btn-ghost"
                          type="button"
                          disabled={!writable || busy !== null}
                          title={missingHint}
                          onClick={() => unbindEmployee(row.skill_id, row.employee)}
                        >
                          解除绑定
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
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
