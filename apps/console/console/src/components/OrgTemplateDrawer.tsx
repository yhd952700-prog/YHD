/**
 * 「从模板开始」抽屉 —— 预览、勾选、试算、确认套用。
 *
 * 硬约束
 * ------
 * 1. 打开抽屉**不预选模板、不预选模式、不自动试算、不自动写入**。模板只是参考，
 *    最终由人在组织架构页决定。
 * 2. 内置模板只读：要改先复制成自定义；内置只提供「隐藏」，不提供删除。
 * 3. 套用前必须试算；试算令牌失效（组织或模板被改过）时，确认按钮禁用，要求重新试算。
 * 4. 「任务无人接管」未检查就原样显示「未检查」，不写成「无风险」。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ImpactWarning, OrgRow } from '../lib/org'
import { kindLabel, orgFailure, taskWarning } from '../lib/org'
import {
  applyOrgTemplate,
  copyOrgTemplate,
  createOrgTemplate,
  deleteOrgTemplate,
  exportOrgTemplate,
  flattenTemplate,
  hideOrgTemplate,
  importOrgTemplate,
  loadOrgTemplate,
  loadOrgTemplates,
  patchOrgTemplate,
  previewOrgTemplate,
  unhideOrgTemplate,
} from '../lib/orgTemplates'
import type {
  OrgTemplateDetail,
  OrgTemplateList,
  OrgTemplateNode,
  OrgTemplateSummary,
  TemplateApplyInput,
  TemplateMode,
  TemplatePreview,
} from '../lib/orgTemplates'

interface Props {
  open: boolean
  rows: OrgRow[]
  onClose: () => void
  onApplied: (message: string) => void
}

const MODE_LABELS: Record<TemplateMode, string> = {
  subtree: '建在新节点下',
  merge: '合并进现有树',
  replace: '替换整棵树',
}

const MODE_HINTS: Record<TemplateMode, string> = {
  subtree: '新建一个模板根节点，挂在你选的上级下面；现有节点不动。',
  merge: '把模板根映射到你选的锚点，子节点按名称递归匹配；同名同类型合并，同名不同类型要先处理冲突。',
  replace: '删除当前全部组织节点与任职，再按模板整树重建。会记一版，可回滚；没有历史版本时不能回滚。',
}

export function OrgTemplateDrawer({ open, rows, onClose, onApplied }: Props) {
  const [list, setList] = useState<OrgTemplateList | null>(null)
  const [includeHidden, setIncludeHidden] = useState(false)
  const [detail, setDetail] = useState<OrgTemplateDetail | null>(null)
  const [mode, setMode] = useState<TemplateMode | ''>('')
  const [targetParentId, setTargetParentId] = useState('')
  const [targetNodeId, setTargetNodeId] = useState('')
  const [selected, setSelected] = useState<Set<string>>(() => new Set())
  const [resolutions, setResolutions] = useState<
    Record<string, { action: 'rename' | 'skip'; new_name: string }>
  >({})
  const [preview, setPreview] = useState<TemplatePreview | null>(null)
  const [stale, setStale] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  const reloadList = useCallback(async () => {
    try {
      const data = await loadOrgTemplates(includeHidden)
      setList(data)
    } catch (err) {
      setError(orgFailure(err).message)
    }
  }, [includeHidden])

  useEffect(() => {
    if (!open) return
    void reloadList()
  }, [open, reloadList])

  // 关闭时清掉临时选择，下一次打开仍然是「什么都没选」。
  useEffect(() => {
    if (open) return
    setDetail(null)
    setMode('')
    setTargetParentId('')
    setTargetNodeId('')
    setSelected(new Set())
    setResolutions({})
    setPreview(null)
    setStale(false)
    setError(null)
    setNotice(null)
  }, [open])

  const flat = useMemo(
    () => (detail ? flattenTemplate(detail.payload.template.root) : []),
    [detail],
  )
  const parentOf = useMemo(() => {
    const map = new Map<string, string | null>()
    for (const row of flat) map.set(row.node.key, row.parentKey)
    return map
  }, [flat])

  const invalidate = () => {
    setPreview(null)
    setStale(false)
  }

  const openDetail = async (templateId: string) => {
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      const data = await loadOrgTemplate(templateId)
      setDetail(data)
      setMode('')
      setTargetParentId('')
      setTargetNodeId('')
      setSelected(new Set())
      setResolutions({})
      setPreview(null)
      setStale(false)
    } catch (err) {
      setError(orgFailure(err).message)
    } finally {
      setBusy(false)
    }
  }

  const toggleNode = (key: string, checked: boolean) => {
    const next = new Set(selected)
    if (checked) {
      let cursor: string | null = key
      while (cursor) {
        next.add(cursor)
        cursor = parentOf.get(cursor) ?? null
      }
    } else {
      const drop = (nodeKey: string) => {
        next.delete(nodeKey)
        for (const row of flat) {
          if (row.parentKey === nodeKey) drop(row.node.key)
        }
      }
      drop(key)
    }
    if (mode === 'merge' && detail) next.add(detail.payload.template.root.key)
    setSelected(next)
    invalidate()
  }

  const changeMode = (next: TemplateMode) => {
    setMode(next)
    if (detail) {
      const rootKey = detail.payload.template.root.key
      if (next === 'merge') {
        setSelected((prev) => new Set(prev).add(rootKey))
      }
    }
    invalidate()
  }

  const setResolution = (key: string, action: 'rename' | 'skip', newName = '') => {
    setResolutions((prev) => ({ ...prev, [key]: { action, new_name: newName } }))
    setStale(true)
  }

  const buildInput = (): TemplateApplyInput => ({
    mode: (mode || 'subtree') as TemplateMode,
    target_parent_id: mode === 'subtree' ? targetParentId || null : null,
    target_node_id: mode === 'merge' ? targetNodeId || null : null,
    selected_keys: [...selected],
    resolutions: Object.entries(resolutions).map(([key, value]) => ({
      key,
      action: value.action,
      new_name: value.new_name,
    })),
  })

  const runPreview = async () => {
    if (!detail || !mode) return
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      const data = await previewOrgTemplate(detail.template_id, buildInput())
      setPreview(data)
      setStale(false)
    } catch (err) {
      setPreview(null)
      setError(orgFailure(err).message)
    } finally {
      setBusy(false)
    }
  }

  const runApply = async () => {
    if (!detail || !preview || stale) return
    const unresolved = preview.conflicts.filter((item) => {
      const local = resolutions[item.key]
      return !item.resolved && !local
    })
    if (unresolved.length > 0) {
      setError('还有冲突未处理；请先选择改名或跳过，再重新试算。')
      return
    }
    setBusy(true)
    setError(null)
    try {
      const result = await applyOrgTemplate(detail.template_id, buildInput(), preview.preview_token)
      setPreview(null)
      setSelected(new Set())
      setResolutions({})
      setNotice(result.note)
      onApplied(result.note)
    } catch (err) {
      const failure = orgFailure(err)
      setError(failure.message)
      setStale(true)
    } finally {
      setBusy(false)
    }
  }

  const runListAction = async (task: () => Promise<unknown>, message: string) => {
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      await task()
      setNotice(message)
      await reloadList()
    } catch (err) {
      setError(orgFailure(err).message)
    } finally {
      setBusy(false)
    }
  }

  const onCreateBlank = () => {
    const name = window.prompt('新自定义模板名称', '我的组织模板')
    if (!name) return
    void runListAction(async () => {
      const created = await createOrgTemplate({ name })
      await openDetail(created.template_id)
    }, '已创建空白模板；可以改名，或导入 JSON 覆盖节点树。')
  }

  const onCopy = (item: OrgTemplateSummary) => {
    const name = window.prompt('复制后的模板名称', `${item.name} 副本`)
    if (!name) return
    void runListAction(async () => {
      const created = await copyOrgTemplate(item.template_id, name)
      await openDetail(created.template_id)
    }, '已复制为自定义模板；内置模板本身没有被修改。')
  }

  const onRename = () => {
    if (!detail || detail.source === 'builtin') return
    const name = window.prompt('模板名称', detail.name)
    if (!name || name === detail.name) return
    setBusy(true)
    setError(null)
    patchOrgTemplate(detail.template_id, { name, revision: detail.revision })
      .then(async () => {
        setNotice('模板已改名。')
        await reloadList()
        await openDetail(detail.template_id)
      })
      .catch((err) => setError(orgFailure(err).message))
      .finally(() => setBusy(false))
  }

  const onRenameItem = (item: OrgTemplateSummary) => {
    if (item.source !== 'custom') return
    const name = window.prompt('模板名称', item.name)
    if (!name || name === item.name) return
    void runListAction(
      () => patchOrgTemplate(item.template_id, { name, revision: item.revision }),
      '模板已改名。',
    )
  }

  const onDelete = (item: OrgTemplateSummary) => {
    if (!item.deletable) {
      setError(item.deletable_reason || '内置模板只允许隐藏，不允许删除。')
      return
    }
    if (!window.confirm(`删除自定义模板「${item.name}」？已套用到组织树的节点不受影响。`)) return
    void runListAction(async () => {
      await deleteOrgTemplate(item.template_id)
      if (detail?.template_id === item.template_id) setDetail(null)
    }, '自定义模板已删除。')
  }

  const onToggleHidden = (item: OrgTemplateSummary) => {
    void runListAction(
      () =>
        item.hidden ? unhideOrgTemplate(item.template_id) : hideOrgTemplate(item.template_id),
      item.hidden ? '模板已恢复显示。' : '模板已隐藏；可在「显示已隐藏」里恢复。',
    )
  }

  const onExport = async (item: OrgTemplateSummary) => {
    setError(null)
    try {
      const payload = await exportOrgTemplate(item.template_id)
      const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `${item.name}.template.json`
      document.body.appendChild(link)
      link.click()
      link.remove()
      URL.revokeObjectURL(url)
    } catch (err) {
      setError(orgFailure(err).message)
    }
  }

  const onImportFile = async (file: File | null) => {
    if (!file) return
    setBusy(true)
    setError(null)
    try {
      const payload = JSON.parse(await file.text())
      const created = await importOrgTemplate(payload)
      setNotice(`已导入《${created.name}》；在预览前不会写入组织树。`)
      await reloadList()
      await openDetail(created.template_id)
    } catch (err) {
      setError(orgFailure(err).message)
    } finally {
      setBusy(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  if (!open) return null

  const conflictRows = preview?.conflicts ?? []
  const unresolved = conflictRows.filter((item) => !item.resolved && !resolutions[item.key])
  const taskState = preview
    ? taskWarning(preview.warnings.unattended_tasks as ImpactWarning)
    : null

  return (
    <>
      <div className="os-drawer-backdrop" onClick={onClose} />
      <aside className="os-drawer" aria-label="从模板开始">
        <header className="os-drawer-head">
          <div>
            <div className="os-drawer-title">
              {detail ? `模板：${detail.name}` : '从模板开始'}
            </div>
            <div className="os-drawer-sub">
              模板只作参考；不点「确认套用」不会写入组织树。
            </div>
          </div>
          <div className="os-drawer-actions">
            {detail && (
              <button type="button" className="os-btn os-btn-ghost" onClick={() => setDetail(null)}>
                返回模板库
              </button>
            )}
            <button type="button" className="os-btn os-btn-ghost" onClick={onClose}>
              关闭
            </button>
          </div>
        </header>

        <div className="os-drawer-body">
          {error && (
            <div className="os-alert" role="alert">
              {error}
            </div>
          )}
          {notice && (
            <div className="os-note" role="status">
              {notice}
            </div>
          )}

          {!detail && (
            <>
              <div className="org-template-tools">
                <label className="org-check">
                  <input
                    type="checkbox"
                    checked={includeHidden}
                    onChange={(event) => setIncludeHidden(event.target.checked)}
                  />
                  显示已隐藏模板
                </label>
                <button type="button" className="os-btn os-btn-ghost" onClick={onCreateBlank}>
                  新建空白模板
                </button>
                <button
                  type="button"
                  className="os-btn os-btn-ghost"
                  onClick={() => fileRef.current?.click()}
                >
                  导入 JSON
                </button>
                <input
                  ref={fileRef}
                  type="file"
                  accept="application/json,.json"
                  hidden
                  onChange={(event) => void onImportFile(event.target.files?.[0] ?? null)}
                />
              </div>
              <p className="org-template-note">
                {list?.note ?? '正在读取模板库…'}
                {list ? ` 当前 ${list.total} 套可用，${list.hidden_count} 套已隐藏。` : ''}
              </p>
              <div className="org-template-list">
                {(list?.templates ?? []).map((item) => (
                  <article className="org-template-card" key={item.template_id}>
                    <div className="org-template-card-head">
                      <strong>{item.name}</strong>
                      <span className="org-kind">
                        {item.source === 'builtin' ? '内置参考' : '自定义'}
                      </span>
                      {item.hidden && <span className="org-kind">已隐藏</span>}
                      <span className="org-template-meta">
                        {item.node_count} 节点 · {item.position_count} 岗位 · 最深 {item.max_depth} 层
                      </span>
                    </div>
                    <p className="org-template-desc">{item.description || '没有填写说明。'}</p>
                    <div className="org-template-actions">
                      <button
                        type="button"
                        className="os-btn"
                        onClick={() => void openDetail(item.template_id)}
                      >
                        预览与套用
                      </button>
                      <button
                        type="button"
                        className="os-btn os-btn-ghost"
                        onClick={() => onCopy(item)}
                      >
                        复制
                      </button>
                      {item.source === 'custom' && (
                        <button
                          type="button"
                          className="os-btn os-btn-ghost"
                          onClick={() => onRenameItem(item)}
                        >
                          改名
                        </button>
                      )}
                      <button
                        type="button"
                        className="os-btn os-btn-ghost"
                        onClick={() => void onExport(item)}
                      >
                        导出
                      </button>
                      <button
                        type="button"
                        className="os-btn os-btn-ghost"
                        onClick={() => onToggleHidden(item)}
                      >
                        {item.hidden ? '恢复显示' : '隐藏'}
                      </button>
                      {item.source === 'custom' && (
                        <button
                          type="button"
                          className="os-btn org-btn-danger"
                          onClick={() => onDelete(item)}
                        >
                          删除
                        </button>
                      )}
                    </div>
                    {item.source === 'builtin' && (
                      <p className="org-template-hint">
                        内置模板只读：要修改请先复制；删除请用隐藏代替，避免升级后又被塞回来。
                      </p>
                    )}
                  </article>
                ))}
              </div>
            </>
          )}

          {detail && (
            <>
              <p className="org-template-note">
                {detail.industry ? `${detail.industry} · ` : ''}
                {detail.description || '没有填写说明。'}
                {detail.source === 'builtin' ? ' 内置模板只读，复制后可修改。' : ''}
              </p>
              <div className="org-template-tools">
                <button type="button" className="os-btn os-btn-ghost" onClick={() => onCopy(detail)}>
                  复制为自定义
                </button>
                {detail.source === 'custom' && (
                  <>
                    <button type="button" className="os-btn os-btn-ghost" onClick={onRename}>
                      改名
                    </button>
                    <button
                      type="button"
                      className="os-btn org-btn-danger"
                      onClick={() => onDelete(detail)}
                    >
                      删除模板
                    </button>
                  </>
                )}
                <button type="button" className="os-btn os-btn-ghost" onClick={() => void onExport(detail)}>
                  导出 JSON
                </button>
              </div>

              <div className="org-template-section">
                <h4>套用方式</h4>
                <div className="org-template-modes">
                  {(Object.keys(MODE_LABELS) as TemplateMode[]).map((item) => (
                    <label key={item} className="org-check">
                      <input
                        type="radio"
                        name="org-template-mode"
                        checked={mode === item}
                        onChange={() => changeMode(item)}
                      />
                      {MODE_LABELS[item]}
                    </label>
                  ))}
                </div>
                <p className="org-template-hint">
                  {mode ? MODE_HINTS[mode] : '先选一种方式；不选不会预演，也不会写入。'}
                </p>
              </div>

              {mode === 'subtree' && (
                <label className="org-inline-select">
                  建在谁下面
                  <select
                    value={targetParentId}
                    onChange={(event) => {
                      setTargetParentId(event.target.value)
                      invalidate()
                    }}
                  >
                    <option value="">顶层（没有上级）</option>
                    {rows.map((row) => (
                      <option key={row.node.node_id} value={row.node.node_id}>
                        {'　'.repeat(row.depth)}
                        {row.node.name}
                      </option>
                    ))}
                  </select>
                </label>
              )}

              {mode === 'merge' && (
                <label className="org-inline-select">
                  合并到哪个锚点
                  <select
                    value={targetNodeId}
                    onChange={(event) => {
                      setTargetNodeId(event.target.value)
                      invalidate()
                    }}
                  >
                    <option value="">请选择锚点节点</option>
                    {rows.map((row) => (
                      <option key={row.node.node_id} value={row.node.node_id}>
                        {'　'.repeat(row.depth)}
                        {row.node.name}
                      </option>
                    ))}
                  </select>
                </label>
              )}

              {mode === 'replace' && (
                <div className="os-alert">
                  会删除当前 {rows.length} 个节点及其全部任职，再按模板整树重建。套用会记一版，可在版本历史回滚。
                </div>
              )}

              <div className="org-template-section">
                <div className="org-template-section-head">
                  <h4>选择要套用的节点</h4>
                  <div className="org-template-actions">
                    <button
                      type="button"
                      className="os-btn os-btn-ghost"
                      onClick={() => {
                        setSelected(new Set(flat.map((row) => row.node.key)))
                        invalidate()
                      }}
                    >
                      全选
                    </button>
                    <button
                      type="button"
                      className="os-btn os-btn-ghost"
                      onClick={() => {
                        setSelected(
                          mode === 'merge' && detail
                            ? new Set([detail.payload.template.root.key])
                            : new Set(),
                        )
                        invalidate()
                      }}
                    >
                      清空
                    </button>
                  </div>
                </div>
                <div className="org-template-tree">
                  {flat.map((row) => {
                    const isRoot = row.parentKey === null
                    const locked = mode === 'merge' && isRoot
                    return (
                      <label
                        key={row.node.key}
                        className="org-check org-template-row"
                        style={{ paddingLeft: 8 + row.depth * 16 }}
                      >
                        <input
                          type="checkbox"
                          checked={selected.has(row.node.key)}
                          disabled={locked}
                          onChange={(event) => toggleNode(row.node.key, event.target.checked)}
                        />
                        <span className="org-kind">{kindLabel(row.node.kind)}</span>
                        <span>{row.node.name}</span>
                        {locked && <span className="org-template-hint">合并根固定</span>}
                      </label>
                    )
                  })}
                </div>
              </div>

              <div className="org-template-tools">
                <button
                  type="button"
                  className="os-btn"
                  disabled={busy || !mode || selected.size === 0}
                  onClick={() => void runPreview()}
                >
                  {busy ? '正在试算…' : '试算（只读，不写入）'}
                </button>
                {stale && <span className="org-template-hint">冲突处理已变，请重新试算。</span>}
              </div>

              {preview && (
                <div className="org-template-section">
                  <h4>试算结果：不会自动写入</h4>
                  <div className="org-template-summary">
                    <span>新建 {preview.summary.create}</span>
                    <span>合并 {preview.summary.merge}</span>
                    <span>跳过 {preview.summary.skip}</span>
                    <span className={preview.summary.conflict ? 'org-template-danger' : ''}>
                      冲突 {preview.summary.conflict}
                    </span>
                    <span>选择 {preview.summary.selected} / {preview.summary.total}</span>
                  </div>

                  {preview.removals && (
                    <p className="org-template-hint">
                      替换会删除 {preview.removals.nodes} 个节点、{preview.removals.assignments} 条任职。
                      {taskState ? `任务接管：${taskState.text}` : ''}
                    </p>
                  )}
                  {!preview.removals && taskState && (
                    <p className="org-template-hint">任务接管：{taskState.text}</p>
                  )}

                  {conflictRows.length > 0 && (
                    <div className="org-template-conflicts">
                      {conflictRows.map((item) => {
                        const local = resolutions[item.key]
                        return (
                          <div key={item.key} className="org-template-conflict">
                            <div>
                              <strong>{item.name}</strong>
                              <span className="org-template-hint">
                                {' '}
                                {item.code === 'ORG_TEMPLATE_KIND_CONFLICT'
                                  ? `现有「${kindLabel(item.existing_kind ?? '')}」与模板「${kindLabel(item.template_kind)}」类型不同`
                                  : '目标上级下已有同名节点'}
                              </span>
                            </div>
                            <div className="org-template-actions">
                              <label className="org-check">
                                <input
                                  type="radio"
                                  name={`res-${item.key}`}
                                  checked={local?.action === 'rename'}
                                  onChange={() => setResolution(item.key, 'rename', item.name)}
                                />
                                改名
                              </label>
                              {local?.action === 'rename' && (
                                <input
                                  className="org-template-input"
                                  defaultValue={local.new_name || item.name}
                                  onChange={(event) =>
                                    setResolution(item.key, 'rename', event.target.value)
                                  }
                                  aria-label={`${item.name} 的新名称`}
                                />
                              )}
                              <label className="org-check">
                                <input
                                  type="radio"
                                  name={`res-${item.key}`}
                                  checked={local?.action === 'skip'}
                                  onChange={() => setResolution(item.key, 'skip')}
                                />
                                跳过
                              </label>
                            </div>
                          </div>
                        )
                      })}
                    </div>
                  )}

                  <p className="org-template-hint">{preview.note}</p>
                  <div className="org-template-tools">
                    <button
                      type="button"
                      className="os-btn"
                      disabled={busy || stale || unresolved.length > 0}
                      onClick={() => void runApply()}
                    >
                      {busy ? '正在写入…' : '确认套用'}
                    </button>
                    {unresolved.length > 0 && (
                      <span className="org-template-hint">冲突未处理完，不能套用。</span>
                    )}
                    {stale && <span className="org-template-hint">请重新试算后再确认。</span>}
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </aside>
    </>
  )
}

/** 模板树先序展开的只读预览（列表卡片里不展开，保留给后续详情页复用）。 */
export function templateTreeRows(root: OrgTemplateNode) {
  return flattenTemplate(root)
}
