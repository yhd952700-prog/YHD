/**
 * 技能中心 —— 安装（zip / SKILL.md / 本机目录）、启停、绑定、运行、授权、审计。
 *
 * 事实边界
 * --------
 * * 技能来自本地服务 ``/v1/skills``；接口不可用时整块显示「本地服务还没提供」，
 *   写入动作一并禁用，避免让人点了才发现服务端没有这个能力。
 * * 「被哪些员工使用」读的是真实绑定表；后端没返回时显示「未提供」，不会从员工
 *   能力清单里猜。能力交集只以「可能相关（推导）」出现。
 * * 脚本真执行，但只是「受限执行」，不是沙箱：Job Object 限制内存 / CPU / 时长 /
 *   子进程数；读文件和联网拦不住。这句话在安装页和运行前确认里都要显眼出现。
 * * 第一次运行必须由 Boss 确认；可以记住（90 天），并且随时能在本页清除。
 * * 运行输出会写进运行记录，并注入到绑定过该技能的员工上下文（作为不可信数据）。
 * * MCP 配置不是技能：导入会被后端以 415 拒绝，页面提示去工具中心配置。
 *
 * 接线（本轮按要求不改公共文件）
 * -----------------------------
 * 路由信息放在 `SKILLS_CENTER_PAGE`，`nav.ts` / `App.tsx` 统一接线时直接引用：
 *   nav.ts  → PRIMARY_NAV.push({ key: SKILLS_CENTER_PAGE.key, label: SKILLS_CENTER_PAGE.navLabel, icon: ... })
 *   App.tsx → PAGE_META[SKILLS_CENTER_PAGE.key] = { title, sub }
 *             {shown === SKILLS_CENTER_PAGE.key && <SkillCenter query={query} />}
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import './SkillsTools.css'
import { ApiError, apiFetch, useApi } from '../lib/api'
import { numberText } from '../lib/format'
import { Card, Empty, Failure, Loading } from '../components/ui'
import {
  FIELD_NOT_PROVIDED,
  NOT_PROVIDED,
  NOT_PROVIDED_DETAIL,
  RISK_LABELS,
  approveSkillRun,
  downloadSkillPackage,
  formatLabel,
  installSkillFromPath,
  probeState,
  readSkillApprovals,
  readSkillAudit,
  readSkillFiles,
  readSkills,
  rejectSkillRun,
  revokeSkillApproval,
  revokeSkillApprovals,
  riskTone,
  runSkillEntrypoint,
  sourceLabel,
  uploadSkillPackage,
  upgradeSkillPackage,
  type SkillApprovalsView,
  type SkillAuditEvent,
  type SkillFilesView,
  type SkillInstallResult,
  type SkillRecord,
  type SkillRunResponse,
  type SkillRuntimeView,
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

/** 执行边界：安装页与运行前确认都要显眼出现，不许缩成小字免责。 */
const EXECUTION_BOUNDARY =
  '受限执行，不是沙箱：能限制内存、CPU、运行时长和子进程数；' +
  '但拦不住读取你本机的文件，也拦不住联网。脚本仍以你的 Windows 账号身份运行，' +
  '只调用技能包里显式声明过的入口。'

/** 运行时状态的人话；未知状态原样显示，不硬翻译。 */
const RUNTIME_STATE_LABELS: Record<string, string> = {
  no_runtime: '未声明可执行入口（只作为资料注入）',
  disabled: '技能未启用（启用后才能运行）',
  pending_approval: '首次运行待你确认',
  approved: '已授权，可直接运行',
  unavailable: '运行时不可用',
}

function runtimeStateLabel(state: string): string {
  return RUNTIME_STATE_LABELS[state] ?? (state || FIELD_NOT_PROVIDED)
}

const RUN_STATUS_LABELS: Record<string, string> = {
  ok: '成功',
  failed: '失败',
  timeout: '超时被终止',
  error: '运行时错误',
}

function runTone(status: string): 'ok' | 'danger' | 'warn' | 'muted' {
  if (status === 'ok') return 'ok'
  if (status === 'failed' || status === 'error') return 'danger'
  if (status === 'timeout') return 'warn'
  return 'muted'
}

function formatDuration(ms: number | null): string {
  if (ms === null) return FIELD_NOT_PROVIDED
  if (ms < 1000) return `${ms} ms`
  return `${numberText(ms / 1000, 2)} s`
}

function formatBytes(value: number | null): string {
  if (value === null) return FIELD_NOT_PROVIDED
  return `${numberText(value / (1024 * 1024), 1)} MB`
}

/** 后端声明的上限；缺失就不写，不编默认值。 */
function describeLimits(limits: Record<string, number>): string {
  const parts: string[] = []
  if (limits.timeout_seconds) parts.push(`时长 ${limits.timeout_seconds}s`)
  if (limits.cpu_seconds) parts.push(`CPU ${limits.cpu_seconds}s`)
  if (limits.memory_mb) parts.push(`单进程内存 ${limits.memory_mb}MB`)
  if (limits.job_memory_mb) parts.push(`整个任务内存 ${limits.job_memory_mb}MB`)
  if (limits.max_processes) parts.push(`子进程 ${limits.max_processes} 个`)
  if (limits.max_output_bytes) parts.push(`输出 ${limits.max_output_bytes} 字节`)
  return parts.length > 0 ? parts.join(' / ') : FIELD_NOT_PROVIDED
}

/** 隔离方式按后端真实值说，不把「job_only」说成沙箱。 */
function describeIsolation(isolation: string): string {
  if (isolation === 'low_integrity+job') return '低完整性令牌 + Job Object（受限执行）'
  if (isolation === 'job_only') return '仅 Job Object（低完整性不可用，受限执行）'
  if (isolation === 'unavailable') return '未启动（运行时不可用）'
  return isolation || FIELD_NOT_PROVIDED
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
  const [importResult, setImportResult] = useState<SkillInstallResult | null>(null)
  const [localPath, setLocalPath] = useState('')
  const [runPanelId, setRunPanelId] = useState<string | null>(null)
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

  /** 安装结果统一落到报告卡片；默认未启用，由用户显式启用。 */
  const applyInstall = useCallback(
    (result: SkillInstallResult) => {
      setImportResult(result)
      const runtimeHint = result.runtime.declared
        ? `已声明 ${result.runtime.entrypoints.length} 个运行入口，启用后可运行（首次运行仍需你确认）。`
        : '未声明运行入口，只作为资料注入。'
      setFeedback({
        tone: 'ok',
        text: `${result.upgradeNote ? '升级完成' : '安装完成'}：${result.name}（默认未启用）。${runtimeHint}`,
      })
      void skills.reload()
    },
    [skills],
  )

  const doImport = useCallback(async () => {
    if (!importFile) return
    setBusy('安装技能')
    setFeedback(null)
    setImportResult(null)
    setImportNotice(null)
    try {
      applyInstall(await uploadSkillPackage(importFile.file, importFile.name))
    } catch (err) {
      const status = err instanceof ApiError ? err.status : 0
      const message = err instanceof Error ? err.message : '安装没有完成，请稍后重试。'
      setImportNotice(
        isMissingEndpoint(status)
          ? `${NOT_PROVIDED}（安装技能）`
          : status === 415
            ? `该文件不是技能：${message}`
            : `安装没有完成：${message}`,
      )
    } finally {
      setBusy(null)
    }
  }, [applyInstall, importFile])

  const doLocalInstall = useCallback(async () => {
    const path = localPath.trim()
    if (!path) return
    setBusy('从本机路径安装')
    setFeedback(null)
    setImportResult(null)
    setImportNotice(null)
    try {
      applyInstall(await installSkillFromPath(path))
      setLocalPath('')
    } catch (err) {
      const status = err instanceof ApiError ? err.status : 0
      const message = err instanceof Error ? err.message : '安装没有完成，请稍后重试。'
      setImportNotice(
        isMissingEndpoint(status)
          ? `${NOT_PROVIDED}（从本机路径安装）`
          : `安装没有完成：${message}`,
      )
    } finally {
      setBusy(null)
    }
  }, [applyInstall, localPath])

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
  const runPanelRecord = useMemo(
    () => records.find((record) => record.id === runPanelId) ?? null,
    [records, runPanelId],
  )

  return (
    <div className="os-grid">
      <Card title="说明" sub="这个页面只用真实数据说话" span={12}>
        <ul className="os-list">
          <li>技能清单来自本地服务的技能接口；没有接口就显示「{NOT_PROVIDED}」，不会用示例条目占位。</li>
          <li>每一条都要能回答三件事：来源是谁、需要哪些权限、风险等级是多少。缺哪项就标注哪项。</li>
          <li>高风险技能在列表与启用前检查里都会单独提示；风险未知时不允许当作安全。</li>
          <li>「绑定员工」读真实绑定表；读不出来显示「未提供」，不会把能力交集说成绑定。能力交集只以「可能相关（推导）」出现。</li>
          <li>安装支持 .zip、SKILL.md、本机 JSON 清单和本机目录；安装后默认未启用，由你显式启用。</li>
          <li>
            脚本会真执行，但只是「受限执行」不是沙箱：内存 / CPU / 时长 / 子进程数有限制，
            读文件与联网拦不住。第一次运行必须你确认，可以记住 90 天，也能随时清除。
          </li>
          <li>写入动作（创建 / 安装 / 编辑 / 删除 / 运行）在接口不可用时保持禁用并说明原因。</li>
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
                        {record.runtime.declared ? (
                          <div className="st-sub" title={record.runtime.detail}>
                            可运行：{runtimeStateLabel(record.runtime.state)}
                          </div>
                        ) : (
                          record.hasScripts && (
                            <div className="st-sub" title={record.scriptRefs.join('、')}>
                              正文提到脚本，但未声明 runtime.entrypoints（不会被执行）
                            </div>
                          )
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
                          {record.runtime.declared && (
                            <button
                              className="os-btn"
                              type="button"
                              disabled={!writable || busy !== null}
                              title={missingHint}
                              onClick={() =>
                                setRunPanelId((prev) => (prev === record.id ? null : record.id))
                              }
                            >
                              {runPanelId === record.id ? '收起运行' : '运行 / 详情'}
                            </button>
                          )}
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

      <Card
        title="安装技能"
        sub=".zip 技能包 / SKILL.md / 本机 JSON 清单 / 本机目录；安装后默认未启用"
        span={6}
      >
        <div className="st-form">
          <div className="st-warning" role="alert" data-testid="skills-execution-boundary">
            <strong>安装前须知：</strong>
            {EXECUTION_BOUNDARY}
          </div>
          <label className="st-field">
            <span>技能包文件（.zip / .md / .json）</span>
            <input
              className="st-input"
              type="file"
              accept=".zip,.md,.json"
              disabled={!writable}
              onChange={(event) => {
                const input = event.target
                const file = input.files?.[0] ?? null
                if (file && !/\.(zip|md|json)$/i.test(file.name)) {
                  // 不接受就明确说出来，不能静默忽略。
                  setImportFile(null)
                  setImportNotice('只支持 .zip / .md / .json 技能包，请重新选择。')
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
            <span>或选择纯资料技能的 SKILL.md（目录里的脚本不会被上传）</span>
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
            {busy === '安装技能' ? '安装中…' : '安装技能'}
          </button>

          <label className="st-field">
            <span>从本机路径安装（目录 / .zip / .md / .json）</span>
            <input
              className="st-input"
              value={localPath}
              disabled={!writable || busy !== null}
              placeholder="例如 D:\skills\weekly-report"
              onChange={(event) => setLocalPath(event.target.value)}
            />
          </label>
          <button
            className="os-btn"
            type="button"
            disabled={!writable || busy !== null || !localPath.trim()}
            title={missingHint}
            onClick={() => void doLocalInstall()}
          >
            {busy === '从本机路径安装' ? '安装中…' : '从本机路径安装'}
          </button>

          {importNotice && (
            <p className="st-warning" role="alert" data-testid="skills-import-notice">
              {importNotice}
            </p>
          )}
          <p className="os-hint">
            .zip 会先解到临时目录做安全校验：拒绝 <code>..</code>、绝对路径、盘符、符号链接和
            可执行文件，并限制条目数、单文件大小与解压总量；校验通过才整体移入技能目录，
            失败会回滚到安装前的状态，不留半装好的技能。
          </p>
          <p className="os-hint">
            含脚本的技能请用 .zip 或本机目录安装（浏览器无法上传整个目录）。脚本只在
            SKILL.md 声明了 <code>runtime.entrypoints</code> 时才会执行，且第一次运行要你确认。
            MCP server 配置请到工具中心配置，技能中心会以 415 拒绝。
          </p>
          {!writable && <p className="os-hint">{missingHint}</p>}
        </div>
      </Card>

      {runPanelRecord && (
        <Card
          title={`运行技能：${runPanelRecord.name}`}
          sub="脚本真执行；第一次运行需要你确认"
          span={12}
        >
          <SkillRuntimePanel
            key={runPanelRecord.id}
            record={runPanelRecord}
            writable={writable}
            onClose={() => setRunPanelId(null)}
            onChanged={() => void skills.reload()}
          />
        </Card>
      )}

      {importResult && (
        <Card title={`导入报告：${importResult.name || '未命名技能'}`} span={6}>
          {importResult.note && <p className="os-hint">{importResult.note}</p>}
          {importResult.upgradeNote && <p className="os-hint">{importResult.upgradeNote}</p>}
          {importResult.runtime.declared && (
            <p className="os-hint">
              运行入口：
              {importResult.runtime.entrypoints
                .map((entry) => `${entry.id}（${entry.interpreter}，${entry.script}）`)
                .join('、')}
            </p>
          )}
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

function formatJson(value: unknown): string {
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value, null, 2) ?? ''
  } catch {
    return String(value)
  }
}

/**
 * 运行面板：入口选择 / 参数 / 首次授权 / 运行结果 / 记住的授权 / 文件与审计。
 *
 * 这里不做任何乐观推断：运行状态、隔离方式、退出码全部来自后端；没返回就写「未提供」。
 */
function SkillRuntimePanel({
  record,
  writable,
  onClose,
  onChanged,
}: {
  record: SkillRecord
  writable: boolean
  onClose: () => void
  onChanged: () => void
}) {
  const runtime: SkillRuntimeView = record.runtime
  const [entrypoint, setEntrypoint] = useState(() => runtime.entrypoints[0]?.id ?? '')
  const [paramsText, setParamsText] = useState('{}')
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [result, setResult] = useState<SkillRunResponse | null>(null)
  const [approvals, setApprovals] = useState<SkillApprovalsView | null>(null)
  const [files, setFiles] = useState<SkillFilesView | null>(null)
  const [audit, setAudit] = useState<SkillAuditEvent[] | null>(null)
  const [upgradeFile, setUpgradeFile] = useState<File | null>(null)
  const [showFiles, setShowFiles] = useState(false)
  const [showAudit, setShowAudit] = useState(false)

  const refresh = useCallback(async () => {
    try {
      const [nextApprovals, nextFiles, nextAudit] = await Promise.all([
        readSkillApprovals(record.id),
        readSkillFiles(record.id),
        readSkillAudit(record.id),
      ])
      setApprovals(nextApprovals)
      setFiles(nextFiles)
      setAudit(nextAudit)
    } catch (err) {
      setError(err instanceof Error ? err.message : '运行时信息没有读出来。')
    }
  }, [record.id])

  useEffect(() => {
    void refresh()
  }, [refresh])

  /** 参数必须是 JSON 对象；不是就明确报错，不把字符串拼进命令行。 */
  const params = useMemo<Record<string, unknown> | null>(() => {
    try {
      const parsed: unknown = JSON.parse(paramsText.trim() || '{}')
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return null
      return parsed as Record<string, unknown>
    } catch {
      return null
    }
  }, [paramsText])

  const runNow = useCallback(async () => {
    if (!entrypoint || params === null) return
    setBusy('运行脚本')
    setError(null)
    setNotice(null)
    try {
      const response = await runSkillEntrypoint(record.id, entrypoint, params)
      setResult(response)
      if (response.state === 'finished') {
        setNotice('脚本已执行。输出已写入运行记录，并会注入到绑定过该技能的员工上下文。')
      }
      await refresh()
      onChanged()
    } catch (err) {
      setError(err instanceof Error ? err.message : '运行没有完成。')
    } finally {
      setBusy(null)
    }
  }, [entrypoint, onChanged, params, record.id, refresh])

  const allowAndRun = useCallback(
    async (mode: 'once' | 'always') => {
      const ticket = result?.approval
      if (!ticket || params === null) return
      setBusy(mode === 'once' ? '授权并运行' : '记住并运行')
      setError(null)
      setNotice(null)
      try {
        await approveSkillRun(ticket.approvalId, mode)
        const response = await runSkillEntrypoint(record.id, ticket.entrypoint, params)
        setResult(response)
        setNotice(
          response.state === 'finished'
            ? mode === 'once'
              ? '已允许本次运行；这次授权只生效一次。'
              : '已记住授权（90 天），可在本面板随时清除。'
            : '授权已提交，但运行没有返回结果，请查看审计记录。',
        )
        await refresh()
        onChanged()
      } catch (err) {
        setError(err instanceof Error ? err.message : '授权后运行没有完成。')
      } finally {
        setBusy(null)
      }
    },
    [onChanged, params, record.id, refresh, result],
  )

  const deny = useCallback(async () => {
    const ticket = result?.approval
    if (!ticket) return
    setBusy('拒绝运行')
    setError(null)
    try {
      await rejectSkillRun(ticket.approvalId)
      setResult(null)
      setNotice('已拒绝这次运行申请，没有启动任何进程。')
      await refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : '拒绝没有完成。')
    } finally {
      setBusy(null)
    }
  }, [refresh, result])

  const revokeOne = useCallback(
    async (approvalId: string) => {
      setBusy('清除授权')
      setError(null)
      try {
        await revokeSkillApproval(approvalId)
        setNotice('已清除这条记住的授权；下次运行会重新问你。')
        await refresh()
        onChanged()
      } catch (err) {
        setError(err instanceof Error ? err.message : '清除授权没有完成。')
      } finally {
        setBusy(null)
      }
    },
    [onChanged, refresh],
  )

  const revokeAll = useCallback(async () => {
    setBusy('清除授权')
    setError(null)
    try {
      const cleared = await revokeSkillApprovals(record.id)
      setNotice(`已清除 ${cleared} 条记住的授权。`)
      await refresh()
      onChanged()
    } catch (err) {
      setError(err instanceof Error ? err.message : '清除授权没有完成。')
    } finally {
      setBusy(null)
    }
  }, [onChanged, record.id, refresh])

  const doExport = useCallback(async () => {
    setBusy('导出技能包')
    setError(null)
    try {
      await downloadSkillPackage(record.id)
      setNotice('已开始下载技能包 .zip。')
    } catch (err) {
      setError(err instanceof Error ? err.message : '导出没有完成。')
    } finally {
      setBusy(null)
    }
  }, [record.id])

  const doUpgrade = useCallback(async () => {
    if (!upgradeFile) return
    setBusy('升级技能包')
    setError(null)
    setNotice(null)
    try {
      const upgraded = await upgradeSkillPackage(record.id, upgradeFile, upgradeFile.name)
      setUpgradeFile(null)
      setResult(null)
      setNotice(upgraded.upgradeNote || '升级完成。')
      await refresh()
      onChanged()
    } catch (err) {
      setError(err instanceof Error ? err.message : '升级没有完成。')
    } finally {
      setBusy(null)
    }
  }, [onChanged, record.id, refresh, upgradeFile])

  const disabledHint = writable ? undefined : '技能接口不可用，本面板不会提交任何运行请求。'

  return (
    <div className="st-form">
      <p className="st-warning" role="alert" data-testid="skills-run-boundary">
        {EXECUTION_BOUNDARY}
      </p>

      <dl className="st-kv">
        <dt>运行状态</dt>
        <dd>{runtimeStateLabel(runtime.state)}</dd>
        <dt>安装包</dt>
        <dd>
          {runtime.installed ? '已安装' : '没有安装包'}
          {runtime.packageHash ? ` · 包哈希 ${runtime.packageHash.slice(0, 16)}…` : ''}
        </dd>
        <dt>资源上限</dt>
        <dd>{describeLimits(runtime.limits)}</dd>
      </dl>
      {runtime.detail && <p className="os-hint">{runtime.detail}</p>}

      <label className="st-field">
        <span>运行入口</span>
        <select
          className="st-input"
          value={entrypoint}
          disabled={busy !== null}
          onChange={(event) => setEntrypoint(event.target.value)}
        >
          {runtime.entrypoints.map((entry) => (
            <option key={entry.id} value={entry.id}>
              {entry.label}（{entry.interpreter} · {entry.script}
              {entry.network ? ' · 声明需要联网' : ''}）
            </option>
          ))}
        </select>
      </label>

      <label className="st-field">
        <span>参数（JSON 对象，经 stdin 交给脚本，不拼接命令行）</span>
        <textarea
          className="st-input"
          rows={3}
          value={paramsText}
          spellCheck={false}
          disabled={busy !== null}
          onChange={(event) => setParamsText(event.target.value)}
        />
      </label>
      {params === null && (
        <p className="os-error" role="alert">
          参数不是合法的 JSON 对象，例如 {'{"topic": "周报"}'}。
        </p>
      )}

      <div className="st-actions">
        <button
          className="os-btn os-btn-primary"
          type="button"
          disabled={!writable || busy !== null || !entrypoint || params === null || !record.enabled}
          title={disabledHint}
          onClick={() => void runNow()}
        >
          {busy === '运行脚本'
            ? '运行中…'
            : runtime.allowed
              ? '运行脚本'
              : '请求运行（首次需确认）'}
        </button>
        <button
          className="os-btn os-btn-ghost"
          type="button"
          disabled={busy !== null}
          onClick={() => void refresh()}
        >
          刷新运行时信息
        </button>
        <button className="os-btn os-btn-ghost" type="button" onClick={onClose}>
          收起
        </button>
      </div>
      {!record.enabled && (
        <p className="os-hint">技能还没启用：先在列表里点「启用」，运行接口才会执行。</p>
      )}
      {disabledHint && <p className="os-hint">{disabledHint}</p>}

      {result?.state === 'pending_approval' && result.approval && (
        <div className="st-warning" role="alert" data-testid="skills-approval-request">
          <strong>确认运行「{result.approval.entrypoint}」？确认前不会启动任何进程。</strong>
          <div>解释器：{result.approval.interpreter}；脚本：{result.approval.script}</div>
          <div>资源上限：{describeLimits(result.approval.limits)}</div>
          <div>{EXECUTION_BOUNDARY}</div>
          <div className="st-actions">
            <button
              className="os-btn os-btn-primary"
              type="button"
              disabled={busy !== null}
              onClick={() => void allowAndRun('once')}
            >
              仅本次允许并运行
            </button>
            <button
              className="os-btn"
              type="button"
              disabled={busy !== null}
              onClick={() => void allowAndRun('always')}
            >
              记住（90 天）并运行
            </button>
            <button
              className="os-btn os-btn-ghost"
              type="button"
              disabled={busy !== null}
              onClick={() => void deny()}
            >
              拒绝
            </button>
          </div>
        </div>
      )}

      {result?.run && (
        <div data-testid="skills-run-result">
          <div className="st-sub">最近一次运行：{result.run.runId || FIELD_NOT_PROVIDED}</div>
          <dl className="st-kv">
            <dt>结果</dt>
            <dd>
              <span className={`st-chip st-tone-${runTone(result.run.status)}`}>
                {RUN_STATUS_LABELS[result.run.status] ?? result.run.status}
              </span>
              {result.run.timedOut ? ' · 超时被终止' : ''}
            </dd>
            <dt>退出码</dt>
            <dd>{result.run.exitCode ?? FIELD_NOT_PROVIDED}</dd>
            <dt>耗时</dt>
            <dd>{formatDuration(result.run.durationMs)}</dd>
            <dt>峰值内存</dt>
            <dd>{formatBytes(result.run.peakMemoryBytes)}</dd>
            <dt>隔离方式</dt>
            <dd>{describeIsolation(result.run.isolation)}</dd>
            <dt>摘要</dt>
            <dd>{result.run.summary || FIELD_NOT_PROVIDED}</dd>
          </dl>
          {result.run.result !== null && (
            <pre
              className="st-input"
              style={{ whiteSpace: 'pre-wrap', maxHeight: 240, overflow: 'auto' }}
            >
              {formatJson(result.run.result)}
            </pre>
          )}
          {result.run.stdout && (
            <>
              <div className="st-sub">stdout</div>
              <pre
                className="st-input"
                style={{ whiteSpace: 'pre-wrap', maxHeight: 240, overflow: 'auto' }}
              >
                {result.run.stdout}
              </pre>
            </>
          )}
          {result.run.stderr && (
            <>
              <div className="st-sub">stderr</div>
              <pre
                className="st-input"
                style={{ whiteSpace: 'pre-wrap', maxHeight: 240, overflow: 'auto' }}
              >
                {result.run.stderr}
              </pre>
            </>
          )}
          {result.run.truncatedBytes > 0 && (
            <p className="os-hint">输出超过上限，已截断 {result.run.truncatedBytes} 字节。</p>
          )}
          <p className="os-hint">
            这次输出已写入运行记录，并会注入到「绑定过该技能的员工」上下文里（作为不可信数据，
            不是指令）。没绑定这个技能的员工拿不到。
          </p>
        </div>
      )}

      <div>
        <div className="st-sub">已记住的授权（可随时清除；包或脚本一变自动失效）</div>
        {approvals === null ? (
          <p className="os-hint">还没有读到授权信息。</p>
        ) : approvals.remembered.length === 0 ? (
          <p className="os-hint">没有记住的授权：每次运行都会先问你一次。</p>
        ) : (
          <>
            <div className="os-table-wrap">
              <table className="os-table">
                <thead>
                  <tr>
                    <th>入口</th>
                    <th>脚本</th>
                    <th>到期</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {approvals.remembered.map((row) => (
                    <tr key={row.approvalId}>
                      <td>{row.entrypoint}</td>
                      <td>{row.script}</td>
                      <td>{row.expiresAt || FIELD_NOT_PROVIDED}</td>
                      <td>
                        <button
                          className="os-btn os-btn-ghost"
                          type="button"
                          disabled={busy !== null}
                          onClick={() => void revokeOne(row.approvalId)}
                        >
                          清除这条
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <button
              className="os-btn os-btn-ghost"
              type="button"
              disabled={busy !== null}
              onClick={() => void revokeAll()}
            >
              清除本技能全部授权
            </button>
          </>
        )}
      </div>

      <div className="st-actions">
        <button
          className="os-btn os-btn-ghost"
          type="button"
          onClick={() => setShowFiles((value) => !value)}
        >
          {showFiles ? '收起文件清单' : '查看已安装文件'}
        </button>
        <button
          className="os-btn os-btn-ghost"
          type="button"
          onClick={() => setShowAudit((value) => !value)}
        >
          {showAudit ? '收起审计记录' : '查看审计记录'}
        </button>
        <button
          className="os-btn"
          type="button"
          disabled={busy !== null || !files?.installed}
          onClick={() => void doExport()}
        >
          {busy === '导出技能包' ? '导出中…' : '导出为 .zip'}
        </button>
      </div>

      {showFiles && (
        files === null ? (
          <p className="os-hint">文件清单没有读出来。</p>
        ) : files.files.length === 0 ? (
          <p className="os-hint">没有安装文件。</p>
        ) : (
          <ul className="os-list">
            {files.files.map((file) => (
              <li key={file.path}>
                <code>{file.path}</code>
                <span className="st-sub">sha256 {file.sha256.slice(0, 16)}…</span>
              </li>
            ))}
          </ul>
        )
      )}

      {showAudit && (
        audit === null || audit.length === 0 ? (
          <p className="os-hint">还没有审计记录。</p>
        ) : (
          <div className="os-table-wrap">
            <table className="os-table">
              <thead>
                <tr>
                  <th>时间</th>
                  <th>事件</th>
                  <th>入口</th>
                  <th>退出码</th>
                  <th>耗时</th>
                </tr>
              </thead>
              <tbody>
                {audit.slice(0, 20).map((event, index) => (
                  <tr key={`${event.runId}-${event.event}-${index}`}>
                    <td>{event.at || FIELD_NOT_PROVIDED}</td>
                    <td>{event.event}</td>
                    <td>{event.entrypoint}</td>
                    <td>{event.exitCode ?? FIELD_NOT_PROVIDED}</td>
                    <td>{event.timedOut ? '超时' : formatDuration(event.durationMs)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      )}

      <label className="st-field">
        <span>升级：上传新的 .zip / .md / .json（保留员工绑定；旧授权自动失效）</span>
        <input
          className="st-input"
          type="file"
          accept=".zip,.md,.json"
          disabled={busy !== null}
          onChange={(event) => setUpgradeFile(event.target.files?.[0] ?? null)}
        />
      </label>
      <div className="st-actions">
        <button
          className="os-btn"
          type="button"
          disabled={busy !== null || upgradeFile === null}
          onClick={() => void doUpgrade()}
        >
          {busy === '升级技能包' ? '升级中…' : '升级技能包'}
        </button>
      </div>

      {notice && (
        <p className="os-hint" role="status" data-testid="skills-run-notice">
          {notice}
        </p>
      )}
      {error && (
        <p className="os-error" role="alert" data-testid="skills-run-error">
          {error}
        </p>
      )}
    </div>
  )
}
