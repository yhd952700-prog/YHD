/**
 * 组织模板 —— 数据层接口。
 *
 * 模板只作参考：这里**没有任何自动套用**的调用，预览与套用都必须由页面上的人
 * 明确点按钮触发。内置模板只读，复制成自定义模板后才能改名/改树；内置只允许
 * 隐藏（服务端 409 ORG_TEMPLATE_BUILTIN_HIDE_ONLY 会兜底）。
 */

import { apiFetch } from './api'

const BASE = '/v1/org/templates'

export interface OrgTemplateNode {
  key: string
  name: string
  kind: string
  description: string
  children: OrgTemplateNode[]
}

export interface OrgTemplatePayload {
  kind: 'liuhao.org.template'
  schema_version: 1
  template: {
    key: string
    name: string
    industry: string
    description: string
    version: number
    sort: number
    root: OrgTemplateNode
  }
}

export interface OrgTemplateSummary {
  template_id: string
  name: string
  source: 'builtin' | 'custom'
  industry: string
  description: string
  version: number | null
  revision: number | null
  sort: number
  hidden: boolean
  editable: boolean
  deletable: boolean
  deletable_reason: string
  node_count: number
  position_count: number
  max_depth: number
}

export interface OrgTemplateDetail extends OrgTemplateSummary {
  payload: OrgTemplatePayload
  fingerprint: string
}

export interface OrgTemplateList {
  templates: OrgTemplateSummary[]
  total: number
  hidden_count: number
  note: string
}

export type TemplateMode = 'subtree' | 'merge' | 'replace'

export interface TemplateResolution {
  key: string
  action: 'rename' | 'skip'
  new_name: string
}

export interface TemplateApplyInput {
  mode: TemplateMode
  target_parent_id?: string | null
  target_node_id?: string | null
  selected_keys: string[]
  resolutions: TemplateResolution[]
}

export interface TemplatePlanNode {
  key: string
  name: string
  kind: string
  parent_key: string | null
  depth: number
  status: 'create' | 'merge' | 'skip' | 'conflict'
  existing_node_id: string | null
  reason: string | null
}

export interface TemplateConflict {
  key: string
  code: string
  name: string
  parent_key: string | null
  existing_node_id: string | null
  existing_kind: string | null
  template_kind: string
  resolved: string | null
  options: string[]
}

export interface TemplateWarning {
  label?: string
  count?: number | null
  items?: Array<Record<string, unknown>>
  note?: string
  checked?: boolean
  reason?: string
}

export interface TemplatePreview {
  template: {
    template_id: string
    name: string
    source: string
    version: number | null
    revision: number | null
  }
  mode: TemplateMode
  target: { node_id: string; name: string; path: string } | null
  base_seq: number
  preview_token: string
  selected_keys: string[]
  summary: {
    total: number
    selected: number
    create: number
    merge: number
    skip: number
    conflict: number
  }
  nodes: TemplatePlanNode[]
  conflicts: TemplateConflict[]
  decisions: TemplateResolution[]
  removals: {
    nodes: number
    assignments: number
    unattended_tasks: TemplateWarning
    authority_items: Array<Record<string, unknown>>
  } | null
  warnings: {
    unattended_tasks: TemplateWarning
    authority_note: string
  }
  rollback: { available: boolean; previous_seq: number | null }
  note: string
}

export interface TemplateApplyResult {
  template: TemplatePreview['template']
  mode: TemplateMode
  target: TemplatePreview['target']
  version: { version_id: string; seq: number; action: string }
  summary: {
    created_nodes: number
    merged_nodes: number
    renamed_nodes: number
    skipped_nodes: number
    removed_nodes: number
    removed_assignments: number
  }
  rollback: { available: boolean; previous_seq: number | null; note: string }
  audit_written: boolean
  note: string
}

async function send<T>(path: string, method: string, body?: unknown): Promise<T> {
  return apiFetch<T>(`${BASE}${path}`, {
    method,
    body: body === undefined ? undefined : JSON.stringify(body),
  })
}

export function loadOrgTemplates(includeHidden = false): Promise<OrgTemplateList> {
  return send<OrgTemplateList>(`?include_hidden=${includeHidden ? 'true' : 'false'}`, 'GET')
}

export function loadOrgTemplate(templateId: string): Promise<OrgTemplateDetail> {
  return send(`/${encodeURIComponent(templateId)}`, 'GET')
}

export function createOrgTemplate(input: {
  name: string
  description?: string
  industry?: string
}): Promise<{ template_id: string; name: string }> {
  return send('/', 'POST', {
    name: input.name,
    description: input.description ?? '',
    industry: input.industry ?? '',
  })
}

export function copyOrgTemplate(
  templateId: string,
  name?: string,
): Promise<{ template_id: string; name: string }> {
  return send(`/${encodeURIComponent(templateId)}/copy`, 'POST', { name: name ?? null })
}

export function patchOrgTemplate(
  templateId: string,
  patch: {
    name?: string
    description?: string
    industry?: string
    root?: OrgTemplateNode
    revision?: number | null
  },
): Promise<{ template_id: string; revision: number }> {
  return send(`/${encodeURIComponent(templateId)}`, 'PATCH', patch)
}

export function deleteOrgTemplate(templateId: string): Promise<{ deleted: boolean }> {
  return send(`/${encodeURIComponent(templateId)}`, 'DELETE')
}

export function hideOrgTemplate(templateId: string): Promise<{ hidden: boolean }> {
  return send(`/${encodeURIComponent(templateId)}/hide`, 'POST', {})
}

export function unhideOrgTemplate(templateId: string): Promise<{ hidden: boolean }> {
  return send(`/${encodeURIComponent(templateId)}/unhide`, 'POST', {})
}

export function exportOrgTemplate(templateId: string): Promise<OrgTemplatePayload> {
  return send(`/${encodeURIComponent(templateId)}/export`, 'GET')
}

export function importOrgTemplate(
  payload: OrgTemplatePayload,
  name?: string,
): Promise<{ template_id: string; name: string }> {
  const suffix = name ? `?name=${encodeURIComponent(name)}` : ''
  return send(`/import${suffix}`, 'POST', payload)
}

export function previewOrgTemplate(
  templateId: string,
  input: TemplateApplyInput,
): Promise<TemplatePreview> {
  return send(`/${encodeURIComponent(templateId)}/preview`, 'POST', input)
}

export function applyOrgTemplate(
  templateId: string,
  input: TemplateApplyInput,
  previewToken: string,
): Promise<TemplateApplyResult> {
  return send(`/${encodeURIComponent(templateId)}/apply`, 'POST', {
    ...input,
    preview_token: previewToken,
  })
}

/** 模板树的先序展开，供勾选与预览共用。 */
export function flattenTemplate(
  root: OrgTemplateNode,
  parentKey: string | null = null,
  depth = 0,
): Array<{ node: OrgTemplateNode; parentKey: string | null; depth: number }> {
  const rows: Array<{ node: OrgTemplateNode; parentKey: string | null; depth: number }> = [
    { node: root, parentKey, depth },
  ]
  for (const child of root.children ?? []) {
    rows.push(...flattenTemplate(child, root.key, depth + 1))
  }
  return rows
}
