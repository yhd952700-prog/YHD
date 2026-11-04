/**
 * 模型管理 —— 模型配置的读写入口（`/v1/models*`）。
 *
 * 三条硬约束：
 * 1. **写操作只对管理员开放**：后端要求 `admin` 权限或 L0 人类主权；这里用
 *    `session.permissions` / `session.scope` 做同一判断，非管理员只读。
 * 2. **密钥永不明文上屏**：列表只渲染后端给的 `api_key_masked`；表单里的明文只
 *    存在于受控 input 的临时值，提交后立刻清空，绝不打日志。
 * 3. **错误只显示后端原文**：HTTP 失败用 `ApiError.message`（后端 `detail`），
 *    连通性测试把 `reason` 映射成可执行的中文建议。
 */

import { useState } from 'react'
import type { FormEvent } from 'react'
import type { Session } from '../lib/auth'
import { apiFetch, useApi } from '../lib/api'
import { Dot, Guard } from './ui'

/** 与 `GET /v1/models` 的单条模型一一对应（密钥字段只有脱敏串）。 */
export interface ModelRecord {
  id: string
  name: string
  provider: string
  model: string
  base_url: string | null
  timeout: number
  is_default: boolean
  enabled: boolean
  api_key_set: boolean
  api_key_masked: string | null
  created_at: string
  updated_at: string
}

export interface ModelsResponse {
  models: ModelRecord[]
  default_id: string | null
  count: number
  supported_providers: string[]
  encryption: { algorithm: string; key_source: string }
}

/** `POST /v1/models/test` 与 `POST /v1/models/{id}/test` 的响应。 */
export interface ProbeResult {
  ok: boolean
  reason: string
  detail: string
  latency_ms: number
  provider: string
  model: string
}

/**
 * 连通性失败原因 → 可执行的中文。
 *
 * 纯函数，便于单测：后端只给机器可读的 `reason`，界面必须回答"我该改什么"。
 * `upstream_error` 及未知原因一律回落到后端 `detail` —— 不替后端编造归因。
 */
export function mapProbeReason(reason: string, latencyMs: number, detail: string): string {
  switch (reason) {
    case '':
      return `连接成功（${latencyMs} ms）`
    case 'invalid_key':
      return 'API Key 无效或没有权限（401/403）'
    case 'model_not_found':
      return '模型名不存在或该端点不支持这个模型（404）'
    case 'unreachable':
      return '端点不可达：检查 base_url、端口与网络（连接被拒绝/域名解析失败）'
    case 'timeout':
      return '连接超时：端点响应太慢，或 base_url 不可达'
    case 'rate_limited':
      return '被上游限流（429），请稍后重试'
    case 'upstream_error':
      return detail || '上游返回错误'
    default:
      return detail || `上游返回错误（${reason || '未知原因'}）`
  }
}

interface FormState {
  name: string
  provider: string
  model: string
  baseUrl: string
  apiKey: string
  timeout: string
  enabled: boolean
  isDefault: boolean
}

const EMPTY_FORM: FormState = {
  name: '',
  provider: '',
  model: '',
  baseUrl: '',
  apiKey: '',
  timeout: '30',
  enabled: true,
  isDefault: false,
}

type ProbeState =
  | { state: 'idle' }
  | { state: 'testing'; source: string }
  | { state: 'done'; source: string; result: ProbeResult }

function formFromModel(item: ModelRecord): FormState {
  return {
    name: item.name,
    provider: item.provider,
    model: item.model,
    baseUrl: item.base_url ?? '',
    apiKey: '',
    timeout: String(item.timeout),
    enabled: item.enabled,
    isDefault: item.is_default,
  }
}

export function ModelManager({ session }: { session: Session }) {
  const canWrite = session.permissions.includes('admin') || session.scope === 'L0'
  const list = useApi<ModelsResponse>('/v1/models', 0)

  const [form, setForm] = useState<FormState>(EMPTY_FORM)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [rowBusy, setRowBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [probe, setProbe] = useState<ProbeState>({ state: 'idle' })

  const providers = list.data?.supported_providers ?? []
  const busy = saving || rowBusy !== null

  const probeResult = probe.state === 'done' ? probe.result : null
  const probeMessage = probeResult
    ? mapProbeReason(probeResult.reason, probeResult.latency_ms, probeResult.detail)
    : ''
  const probeDetail =
    probeResult && !probeResult.ok && probeResult.detail && probeResult.detail !== probeMessage
      ? probeResult.detail
      : ''

  function update<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((prev) => ({ ...prev, [key]: value }))
  }

  function resetForm() {
    // apiKey 只存在于这份临时状态；重置即清空，避免残留到下一次提交。
    setForm(EMPTY_FORM)
    setEditingId(null)
  }

  function beginEdit(item: ModelRecord) {
    setForm(formFromModel(item))
    setEditingId(item.id)
    setError(null)
    setNotice(null)
    setProbe({ state: 'idle' })
  }

  function fail(err: unknown, fallback: string) {
    setError(err instanceof Error ? err.message : fallback)
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!canWrite || busy) return
    if (!form.provider) {
      setError('请选择 provider')
      return
    }
    setSaving(true)
    setError(null)
    setNotice(null)
    try {
      const payload: Record<string, unknown> = {
        name: form.name.trim(),
        provider: form.provider,
        model: form.model.trim(),
        base_url: form.baseUrl.trim() || null,
        timeout: Number(form.timeout),
        enabled: form.enabled,
        is_default: form.isDefault,
      }
      // 编辑时留空 = 不修改；新增时留空 = 不设置密钥。两种情况都不发送该字段。
      if (form.apiKey) payload.api_key = form.apiKey

      if (editingId) {
        await apiFetch<ModelRecord>(`/v1/models/${encodeURIComponent(editingId)}`, {
          method: 'PUT',
          body: JSON.stringify(payload),
        })
        setNotice('模型配置已更新')
      } else {
        await apiFetch<ModelRecord>('/v1/models', {
          method: 'POST',
          body: JSON.stringify(payload),
        })
        setNotice('模型配置已添加')
      }
      resetForm()
      setProbe({ state: 'idle' })
      list.reload()
    } catch (err) {
      fail(err, editingId ? '更新失败' : '添加失败')
    } finally {
      setSaving(false)
    }
  }

  async function testForm() {
    if (!canWrite || busy) return
    if (!form.provider || !form.model.trim()) {
      setError('请先填写 provider 与模型名再测试')
      return
    }
    setRowBusy('test-form')
    setError(null)
    setNotice(null)
    setProbe({ state: 'testing', source: editingId ? '当前编辑配置' : '表单草稿' })
    try {
      // 编辑状态下带 model_id：key 留空时后端会用已保存的密钥，而不是当作无密钥。
      const payload: Record<string, unknown> = {
        provider: form.provider,
        model: form.model.trim(),
        base_url: form.baseUrl.trim() || null,
        timeout: Number(form.timeout),
      }
      if (form.apiKey) payload.api_key = form.apiKey
      if (editingId) payload.model_id = editingId
      const result = await apiFetch<ProbeResult>('/v1/models/test', {
        method: 'POST',
        body: JSON.stringify(payload),
      })
      setProbe({ state: 'done', source: editingId ? '当前编辑配置' : '表单草稿', result })
    } catch (err) {
      setProbe({ state: 'idle' })
      fail(err, '测试连接失败')
    } finally {
      setRowBusy(null)
    }
  }

  async function testSaved(item: ModelRecord) {
    if (!canWrite || busy) return
    const key = `test:${item.id}`
    setRowBusy(key)
    setError(null)
    setNotice(null)
    setProbe({ state: 'testing', source: `已保存配置「${item.name}」` })
    try {
      const result = await apiFetch<ProbeResult>(
        `/v1/models/${encodeURIComponent(item.id)}/test`,
        { method: 'POST' },
      )
      setProbe({ state: 'done', source: `已保存配置「${item.name}」`, result })
    } catch (err) {
      setProbe({ state: 'idle' })
      fail(err, '测试连接失败')
    } finally {
      setRowBusy(null)
    }
  }

  async function setDefault(item: ModelRecord) {
    if (!canWrite || busy) return
    setRowBusy(`default:${item.id}`)
    setError(null)
    setNotice(null)
    try {
      await apiFetch<ModelRecord>(`/v1/models/${encodeURIComponent(item.id)}/default`, {
        method: 'POST',
      })
      setNotice(`已把「${item.name}」设为默认模型`)
      list.reload()
    } catch (err) {
      fail(err, '设为默认失败')
    } finally {
      setRowBusy(null)
    }
  }

  async function remove(item: ModelRecord) {
    if (!canWrite || busy) return
    if (!window.confirm(`确认删除模型「${item.name}」？该操作不可撤销。`)) return
    setRowBusy(`delete:${item.id}`)
    setError(null)
    setNotice(null)
    try {
      await apiFetch<{ deleted: string; default_id: string | null }>(
        `/v1/models/${encodeURIComponent(item.id)}`,
        { method: 'DELETE' },
      )
      if (editingId === item.id) resetForm()
      setNotice(`已删除模型「${item.name}」`)
      list.reload()
    } catch (err) {
      fail(err, '删除失败')
    } finally {
      setRowBusy(null)
    }
  }

  return (
    <div className="os-model">
      {!canWrite && (
        <p className="os-note" role="note">
          当前账号没有管理员权限（需要 admin 权限或 L0 人类主权）。模型列表只读；
          添加、编辑、删除、测试连接与设为默认均由后端拒绝非管理员。
        </p>
      )}

      {canWrite && (
        <>
          <h3 className="os-model-form-title">
            {editingId ? '编辑模型' : '添加模型'}
          </h3>
          <form className="os-operator-form" onSubmit={(event) => void save(event)}>
            <div className="os-field">
              <label htmlFor="model-form-name">名称（必填）</label>
              <div className="os-input">
                <input
                  id="model-form-name"
                  required
                  aria-required="true"
                  maxLength={64}
                  value={form.name}
                  placeholder="例如 qwen-local"
                  onChange={(event) => update('name', event.target.value)}
                />
              </div>
            </div>

            <div className="os-field">
              <label htmlFor="model-form-provider">provider（必填）</label>
              <div className="os-input">
                <select
                  id="model-form-provider"
                  required
                  aria-required="true"
                  value={form.provider}
                  onChange={(event) => update('provider', event.target.value)}
                >
                  <option value="">请选择</option>
                  {providers.map((provider) => (
                    <option key={provider} value={provider}>
                      {provider}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            <div className="os-field">
              <label htmlFor="model-form-model">模型名（必填）</label>
              <div className="os-input">
                <input
                  id="model-form-model"
                  required
                  aria-required="true"
                  maxLength={128}
                  value={form.model}
                  placeholder="例如 qwen2.5:3b"
                  onChange={(event) => update('model', event.target.value)}
                />
              </div>
            </div>

            <div className="os-field os-field-wide">
              <label htmlFor="model-form-base-url">base_url（可选，自定义端点）</label>
              <div className="os-input">
                <input
                  id="model-form-base-url"
                  maxLength={512}
                  value={form.baseUrl}
                  placeholder="https://api.example.com/v1"
                  onChange={(event) => update('baseUrl', event.target.value)}
                />
              </div>
            </div>

            <div className="os-field os-field-wide">
              <label htmlFor="model-form-key">API Key</label>
              <div className="os-input">
                <input
                  id="model-form-key"
                  type="password"
                  autoComplete="new-password"
                  maxLength={512}
                  value={form.apiKey}
                  placeholder={editingId ? '留空表示不修改' : '可留空（mock / ollama 无需密钥）'}
                  onChange={(event) => update('apiKey', event.target.value)}
                />
              </div>
            </div>

            <div className="os-field">
              <label htmlFor="model-form-timeout">超时（秒，必填）</label>
              <div className="os-input">
                <input
                  id="model-form-timeout"
                  type="number"
                  required
                  aria-required="true"
                  min={1}
                  max={600}
                  step={1}
                  value={form.timeout}
                  onChange={(event) => update('timeout', event.target.value)}
                />
              </div>
            </div>

            <div className="os-field">
              <label className="os-check" htmlFor="model-form-enabled">
                <input
                  id="model-form-enabled"
                  type="checkbox"
                  checked={form.enabled}
                  onChange={(event) => update('enabled', event.target.checked)}
                />
                启用（保存后立即生效）
              </label>
            </div>

            <div className="os-field">
              <label className="os-check" htmlFor="model-form-default">
                <input
                  id="model-form-default"
                  type="checkbox"
                  checked={form.isDefault}
                  onChange={(event) => update('isDefault', event.target.checked)}
                />
                设为默认模型
              </label>
            </div>

            <div className="os-model-form-foot">
              <button className="os-btn" type="submit" disabled={busy}>
                {saving ? '保存中…' : editingId ? '保存修改' : '添加模型'}
              </button>
              <button
                className="os-btn os-btn-ghost"
                type="button"
                disabled={busy}
                onClick={() => void testForm()}
              >
                {rowBusy === 'test-form' ? '测试中…' : '测试连接'}
              </button>
              {editingId && (
                <button
                  className="os-btn os-btn-ghost"
                  type="button"
                  disabled={busy}
                  onClick={resetForm}
                >
                  取消编辑
                </button>
              )}
            </div>
          </form>
        </>
      )}

      {error && (
        <div className="os-alert" role="alert">
          <span className="os-model-error">{error}</span>
        </div>
      )}
      {notice && !error && (
        <div className="os-note" role="status" aria-live="polite">
          <span>{notice}</span>
        </div>
      )}

      {probe.state === 'testing' && (
        <p className="os-note" role="status" aria-live="polite">
          <span>{probe.source}：正在测试连接…</span>
        </p>
      )}
      {probe.state === 'done' && probeResult && (
        <div
          className={probeResult.ok ? 'os-note os-model-probe' : 'os-alert os-model-probe'}
          role={probeResult.ok ? 'status' : 'alert'}
          aria-live="polite"
        >
          <span>
            <strong>{probe.source}：</strong>
            {probeMessage}
          </span>
          {probeDetail && <span className="os-model-probe-detail">后端详情：{probeDetail}</span>}
        </div>
      )}

      <h3 className="os-model-form-title">已保存模型</h3>
      <Guard
        loading={list.loading}
        error={list.error}
        data={list.data}
        isEmpty={(data) => (data.models ?? []).length === 0}
        emptyTitle="尚未添加模型"
        emptyHint={
          canWrite
            ? '用上方表单添加第一个模型；首个模型会自动成为默认。'
            : '管理员添加模型后会显示在这里。'
        }
        onRetry={list.reload}
      >
        {(data) => (
          <div className="os-model-scroll">
            <table className="os-model-table">
              <thead>
                <tr>
                  <th scope="col">名称</th>
                  <th scope="col">provider</th>
                  <th scope="col">模型</th>
                  <th scope="col">端点</th>
                  <th scope="col">超时</th>
                  <th scope="col">状态</th>
                  <th scope="col">密钥</th>
                  {canWrite && <th scope="col">操作</th>}
                </tr>
              </thead>
              <tbody>
                {(data.models ?? []).map((item) => (
                  <tr key={item.id}>
                    <td data-label="名称">
                      <span className="os-model-name">{item.name}</span>
                    </td>
                    <td data-label="provider">{item.provider}</td>
                    <td data-label="模型" className="os-model-mono">
                      {item.model}
                    </td>
                    <td data-label="端点" className="os-model-mono">
                      {item.base_url || '—'}
                    </td>
                    <td data-label="超时">{item.timeout} 秒</td>
                    <td data-label="状态">
                      <span className="os-model-status">
                        <Dot tone={item.enabled ? 'ok' : 'muted'} label={item.enabled ? '启用' : '禁用'} />
                        {item.is_default && (
                          <span className="os-badge" data-tone="accent">
                            默认
                          </span>
                        )}
                      </span>
                    </td>
                    <td data-label="密钥">
                      {item.api_key_set ? item.api_key_masked || '已设置' : '未设置'}
                    </td>
                    {canWrite && (
                      <td data-label="操作">
                        <span className="os-model-actions">
                          <button
                            className="os-btn os-btn-ghost"
                            type="button"
                            disabled={busy}
                            aria-label={`编辑模型「${item.name}」`}
                            onClick={() => beginEdit(item)}
                          >
                            编辑
                          </button>
                          <button
                            className="os-btn os-btn-ghost"
                            type="button"
                            disabled={busy}
                            aria-label={`测试连接模型「${item.name}」`}
                            onClick={() => void testSaved(item)}
                          >
                            {rowBusy === `test:${item.id}` ? '测试中…' : '测试连接'}
                          </button>
                          <button
                            className="os-btn os-btn-ghost"
                            type="button"
                            disabled={busy || item.is_default || !item.enabled}
                            aria-label={`把模型「${item.name}」设为默认`}
                            onClick={() => void setDefault(item)}
                          >
                            {rowBusy === `default:${item.id}` ? '设置中…' : '设为默认'}
                          </button>
                          <button
                            className="os-btn os-btn-ghost"
                            type="button"
                            disabled={busy}
                            aria-label={`删除模型「${item.name}」`}
                            onClick={() => void remove(item)}
                          >
                            {rowBusy === `delete:${item.id}` ? '删除中…' : '删除'}
                          </button>
                        </span>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Guard>
    </div>
  )
}
