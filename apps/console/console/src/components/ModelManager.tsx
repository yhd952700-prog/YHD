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
  /** 真实厂商（provider 只是协议适配器名），列表"服务商"列优先显示它。 */
  vendor?: string | null
  model: string
  base_url: string | null
  timeout: number
  is_default: boolean
  enabled: boolean
  api_key_set: boolean
  api_key_masked: string | null
  /** missing / ok / needs_reentry（密文在但当前密钥解不开）。 */
  api_key_status?: 'missing' | 'ok' | 'needs_reentry'
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

/** `POST /v1/models/discover`：拉上游模型列表，失败也给可行动提示。 */
export interface DiscoverResult {
  ok: boolean
  reason: string
  detail: string
  models: string[]
  endpoint?: string
  /** 恒为 true：列表拉不到时仍允许手填模型名。 */
  manual?: boolean
}

/**
 * 服务商预设（`GET /v1/models/presets`）。
 *
 * 只是"把端点与模型名填好"的快捷方式，不是白名单：保存后就是普通模型记录，
 * 可以继续改端点/模型名，也可以删除。``openai_compatible`` 是通用入口，
 * 端点与模型名完全由用户填写。
 */
export interface ProviderPreset {
  id: string
  provider: string
  label: string
  base_url: string | null
  model: string
  requires_api_key: boolean
  custom?: boolean
}

export interface PresetsResponse {
  presets: ProviderPreset[]
  note?: string
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
 * 未知原因不猜归因；上游原文（英文报错）只留在服务端日志与诊断包里，
 * 不铺到界面上。
 */
export function mapProbeReason(reason: string, latencyMs: number, _detail: string): string {
  switch (reason) {
    case '':
      return `连接成功（${latencyMs} ms）`
    case 'invalid_key':
      return 'API Key 无效或没有权限。请检查 Key 是否填错、是否复制完整；建议重新生成后再试。'
    case 'key_revoked':
      return 'API Key 已被提供方停用或吊销。请到提供方控制台重新生成 Key，再填到这里。'
    case 'quota_exhausted':
      return '账户额度已用完。请到提供方控制台充值或提升额度后重试。'
    case 'model_not_found':
      return '模型名不存在或该端点不支持这个模型。请核对模型名称后重试。'
    case 'unreachable':
      return '端点不可达。请检查端点地址、端口与网络连接是否正常。'
    case 'timeout':
      return '连接超时。请检查网络，或稍后再试。'
    case 'rate_limited':
      return '请求被上游限流（429）。请稍后重试。'
    case 'upstream_error':
      return '模型服务返回了错误，连接没有成功。请稍后重试；持续出现时可导出诊断包。'
    default:
      // `reason` 是机器码，不直接上屏；能给出的最确定的一句话就是"连接失败"。
      return '连接失败：请检查服务商、模型名与端点设置后重试。'
  }
}

/**
 * 服务商标识 → 界面名。选项里不能只出现 `ollama` 这种机器名，
 * 否则用户无法判断"本地"还是"云端"。
 */
const PROVIDER_LABELS: Record<string, string> = {
  openai: 'OpenAI（云端）',
  anthropic: 'Anthropic（云端）',
  claude: 'Anthropic（云端）',
  deepseek: 'DeepSeek（云端）',
  azure: 'Azure OpenAI（云端）',
  ollama: '本地 Ollama',
  local: '本地模型',
  moonshot: '月之暗面 Kimi',
  google: 'Google Gemini',
  mock: '内置演示（不调用真实模型）',
}

export function providerLabel(type: string): string {
  const key = String(type ?? '').toLowerCase()
  return PROVIDER_LABELS[key] ?? (type || '未指定')
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

/**
 * 套用预设只改"协议/端点/模型名"三项，不动名称、Key、超时与默认开关。
 * 纯函数便于单测：预设不该覆盖用户已经填好的 Key。
 */
export function presetFormPatch(
  preset: ProviderPreset,
): Pick<FormState, 'provider' | 'baseUrl' | 'model'> {
  return {
    provider: preset.provider,
    baseUrl: preset.base_url ?? '',
    model: preset.model ?? '',
  }
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
  const presets = useApi<PresetsResponse>('/v1/models/presets', 0)

  const [form, setForm] = useState<FormState>(EMPTY_FORM)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [rowBusy, setRowBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [probe, setProbe] = useState<ProbeState>({ state: 'idle' })
  // 最近一次连通测试的结论（按 provider|model|base_url 记指纹）。
  // 保存前如果"同一份参数"刚测过且失败，直接拦下 —— 绝不把已知失败画成"已保存"。
  const [lastTest, setLastTest] = useState<{
    ok: boolean
    message: string
    signature: string
  } | null>(null)
  // 「拉取可用模型」的结果：成功给可点选的模型名，失败给可行动提示。
  const [modelList, setModelList] = useState<
    { ok: true; models: string[] } | { ok: false; detail: string } | null
  >(null)
  const [discovering, setDiscovering] = useState(false)

  const providers = list.data?.supported_providers ?? []
  const busy = saving || rowBusy !== null

  const probeResult = probe.state === 'done' ? probe.result : null
  const probeMessage = probeResult
    ? mapProbeReason(probeResult.reason, probeResult.latency_ms, probeResult.detail)
    : ''

  function update<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((prev) => ({ ...prev, [key]: value }))
  }

  function resetForm() {
    // apiKey 只存在于这份临时状态；重置即清空，避免残留到下一次提交。
    setForm(EMPTY_FORM)
    setEditingId(null)
    setLastTest(null)
    setModelList(null)
  }

  function applyPreset(preset: ProviderPreset) {
    setForm((prev) => ({ ...prev, ...presetFormPatch(preset) }))
    setModelList(null)
    setNotice(
      preset.custom
        ? '已切到 OpenAI 兼容模式：请填写你自己的端点地址与模型名。'
        : `已套用预设「${preset.label}」，端点与模型名仍可自由修改。`,
    )
    setError(null)
  }

  function beginEdit(item: ModelRecord) {
    setForm(formFromModel(item))
    setEditingId(item.id)
    setError(null)
    setNotice(null)
    setProbe({ state: 'idle' })
    setLastTest(null)
    setModelList(null)
  }

  function fail(err: unknown, fallback: string) {
    setError(err instanceof Error ? err.message : fallback)
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!canWrite || busy) return
    if (!form.provider) {
      setError('请选择服务商')
      return
    }
    // 同一份参数刚测过且失败：先修好再保存，避免"保存成功、调用失败"的两套状态。
    const signature = `${form.provider}|${form.model.trim()}|${form.baseUrl.trim()}`
    if (lastTest && lastTest.signature === signature && !lastTest.ok) {
      setError(`未保存：连通测试未通过（${lastTest.message}）。请修正后重试。`)
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

  /** 用当前端点 + Key 拉一次上游模型列表，点一下就填进「模型名」。 */
  async function discoverModels() {
    if (!canWrite || busy || discovering) return
    if (!form.provider) {
      setError('请先选择服务商，再拉取可用模型')
      return
    }
    setDiscovering(true)
    setError(null)
    setNotice(null)
    setModelList(null)
    try {
      const payload: Record<string, unknown> = {
        provider: form.provider,
        base_url: form.baseUrl.trim() || null,
        timeout: Number(form.timeout) || 10,
      }
      if (form.apiKey) payload.api_key = form.apiKey
      // 编辑态带 model_id：Key 留空时后端用已保存的真实 Key。
      if (editingId) payload.model_id = editingId
      const result = await apiFetch<DiscoverResult>('/v1/models/discover', {
        method: 'POST',
        body: JSON.stringify(payload),
      })
      if (result.ok) {
        setModelList({ ok: true, models: result.models })
        setNotice(`已拉取到 ${result.models.length} 个模型，点一下即可填入模型名。`)
      } else {
        // 失败也是"可以继续"的状态：提示里已说明可以手填模型名。
        setModelList({ ok: false, detail: result.detail })
      }
    } catch (err) {
      fail(err, '拉取模型列表失败，可以直接手填模型名')
    } finally {
      setDiscovering(false)
    }
  }

  async function testForm() {
    if (!canWrite || busy) return
    if (!form.provider || !form.model.trim()) {
      setError('请先选择服务商并填写模型名再测试')
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
      setLastTest({
        ok: result.ok,
        message: mapProbeReason(result.reason, result.latency_ms, result.detail),
        signature: `${form.provider}|${form.model.trim()}|${form.baseUrl.trim()}`,
      })
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
      setLastTest({
        ok: result.ok,
        message: mapProbeReason(result.reason, result.latency_ms, result.detail),
        signature: `${item.provider}|${item.model}|${item.base_url ?? ''}`,
      })
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
          当前账号没有模型管理权限。下面的列表只读；添加、编辑、删除与测试连接都会被服务端拒绝。
        </p>
      )}

      {canWrite && (
        <>
          {/* 让"填 Key 就能用"这条路径在界面上可见，而不是靠人猜。 */}
          <div className="os-onboard" role="note" aria-label="模型接入路径">
            <div className="os-onboard-title">从填写密钥到生效，共四步</div>
            <ol className="os-onboard-steps">
              <li>
                <strong>填写密钥</strong>：选择服务商、填写模型名；本地模型可以留空。
              </li>
              <li>
                <strong>测试连通</strong>：失败会当场显示可读原因，不会先报"已保存"。
              </li>
              <li>
                <strong>保存并设为默认</strong>：勾选"设为默认模型"再保存。
              </li>
              <li>
                <strong>下一条消息立即生效</strong>，无需重启。
              </li>
            </ol>
          </div>

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

            <div className="os-field os-field-wide">
              <label id="model-presets-label">服务商预设（只是快捷填充，可改可删）</label>
              <div
                className="os-model-actions"
                role="group"
                aria-labelledby="model-presets-label"
              >
                {(presets.data?.presets ?? []).map((preset) => (
                  <button
                    key={preset.id}
                    className="os-btn os-btn-ghost"
                    type="button"
                    disabled={busy}
                    title="套用后仍可修改端点与模型名"
                    onClick={() => applyPreset(preset)}
                  >
                    {preset.label}
                  </button>
                ))}
              </div>
              <p className="os-note">
                内置服务商不再写死：套用预设只是替你填好协议、端点与模型名；
                任何 OpenAI 兼容服务都可以用「OpenAI 兼容（自定义端点）」接入，
                模型名不做白名单校验。
              </p>
            </div>

            <div className="os-field">
              <label htmlFor="model-form-provider">服务商（必填）</label>
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
                      {providerLabel(provider)}
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
              <p className="os-note">
                按服务商实际名称填写（如 deepseek-chat、qwen2.5:3b、gemini-2.0-flash），
                不做白名单校验；服务商新增模型后无需改代码。
              </p>
              <div className="os-model-form-foot">
                <button
                  className="os-btn os-btn-ghost"
                  type="button"
                  disabled={busy || discovering}
                  onClick={() => void discoverModels()}
                >
                  {discovering ? '拉取中…' : '拉取可用模型'}
                </button>
                <span className="os-note">
                  用当前端点与 Key 调一次上游模型列表（Ollama 走 /api/tags），点模型名即可填入。
                </span>
              </div>
              {modelList?.ok && (
                <div className="os-model-form-foot" role="list" aria-label="可用模型">
                  {modelList.models.map((modelName) => (
                    <button
                      key={modelName}
                      className="os-btn os-btn-ghost"
                      type="button"
                      role="listitem"
                      onClick={() => update('model', modelName)}
                    >
                      {modelName}
                    </button>
                  ))}
                </div>
              )}
              {modelList && !modelList.ok && (
                <p className="os-note" role="status">
                  {modelList.detail}
                </p>
              )}
            </div>

            <div className="os-field os-field-wide">
              <label htmlFor="model-form-base-url">端点地址（可选，自定义服务地址）</label>
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
                  placeholder={editingId ? '留空表示不修改' : '可留空（本地模型无需密钥）'}
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
        </div>
      )}

      <h3 className="os-model-form-title">已配置的模型</h3>
      <Guard
        loading={list.loading}
        error={list.error}
        data={list.data}
        isEmpty={(data) => (data.models ?? []).length === 0}
        emptyTitle="尚未添加模型"
        emptyHint={
          canWrite
            ? '用上方表单添加第一个模型；首个模型会自动成为默认。'
            : '有管理权限的账号添加模型后会显示在这里。'
        }
        onRetry={list.reload}
      >
        {(data) => (
          <div className="os-model-scroll">
            <table className="os-model-table">
              <thead>
                <tr>
                  <th scope="col">名称</th>
                  <th scope="col">服务商</th>
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
                    <td data-label="服务商">{providerLabel(item.vendor || item.provider)}</td>
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
