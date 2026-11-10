/**
 * 模型管理 —— 模型配置的读写入口（`/v1/models*`）。
 *
 * 三条硬约束：
 * 1. **写入口与后端同一口径**：后端 `require_model_manager` 接受管理员、L0
 *    人类主权，或账号节点上的 `model:manage`；这里用 `canManageModels()`
 *    做同一判断。成员凭 `model:manage` 只能维护**自己名下**的 Key，归属
 *    仍由后端 `_guard_owner_assign` 二次校验；没有该作用域时保持只读。
 * 2. **密钥永不明文上屏**：列表只渲染后端给的 `api_key_masked`；表单里的明文只
 *    存在于受控 input 的临时值，提交后立刻清空，绝不打日志。
 * 3. **错误只显示后端原文**：HTTP 失败用 `ApiError.message`（后端 `detail`），
 *    连通性测试把 `reason` 映射成可执行的中文建议。
 */

import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import type { Session } from '../lib/auth'
import { ApiError, apiFetch, useApi } from '../lib/api'
import { canManageModels } from '../lib/modelAccess'
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
  /** 是否通过过「测试连接」：未通过的模型不允许设为默认。 */
  verified?: boolean
  last_verified_at?: string | null
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
  /** 套用后必须知道的一句话（例如方舟要填接入点 ID 而不是模型名）。 */
  hint?: string
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
  /** 本次测试过程中是否发生过自动重试（服务端在重试前生成）。 */
  notice?: string
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
export function mapProbeReason(
  reason: string,
  latencyMs: number,
  _detail: string,
  notice = '',
): string {
  const base = probeReasonText(reason, latencyMs)
  // 测试页是单次请求，没有中途通道：重试提示随结果一起显示，避免用户
  // 对着一块空白等两次超时却不知道发生过重试。
  return notice ? `${base}（本次已自动重试一次：首次尝试较慢）` : base
}

function probeReasonText(reason: string, latencyMs: number): string {
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
      return '模型名不存在，或该接口地址不支持这个模型。请核对模型名称后重试。'
    case 'unreachable':
      return '接口地址不可达。请检查地址、端口与网络连接是否正常。'
    case 'address_rejected':
      return '接口地址指向内网或本机，已被安全策略拒绝。如需连接局域网自建服务，请改用 Ollama 类型或显式放行该地址。'
    case 'dns_failed':
      return '接口地址当前无法解析。请检查主机名拼写、DNS 与网络连接。'
    case 'endpoint_not_found':
      return '接口路径不存在（404）。请核对接口地址是否完整：OpenAI 兼容端点通常以 /v1 结尾；Anthropic 官方地址不带 /v1。'
    case 'server_error':
      return '模型服务端出错（HTTP 5xx）。请稍后重试；持续出现请联系服务提供方。'
    case 'bad_request':
      return '请求不合法（HTTP 400）。请检查模型名与参数设置。'
    case 'proxy_auth':
      return '代理要求认证（HTTP 407）。请检查代理设置，或暂时关闭代理后重试。'
    case 'redirect_blocked':
      return '请求被网关或登录页拦截，返回的不是模型接口。请核对接口地址是否为真实 API 端点。'
    case 'invalid_response':
      return '模型服务返回了无法解析的数据（可能是网关或登录页）。请核对接口地址后重试。'
    case 'timeout':
      return '连接超时。请检查网络，或稍后再试。'
    case 'rate_limited':
      return '请求被上游限流（429）。请稍后重试。'
    case 'upstream_error':
      return '模型服务返回了错误，连接没有成功。请稍后重试；持续出现时可导出诊断包。'
    default:
      // `reason` 是机器码，不直接上屏；能给出的最确定的一句话就是"连接失败"。
      return '连接失败：请检查服务商、模型名与接口地址设置后重试。'
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
  moonshot: '月之暗面 Kimi（云端）',
  kimi: '月之暗面 Kimi（云端）',
  google: 'Google Gemini（云端）',
  gemini: 'Google Gemini（云端）',
  mock: '内置演示（不调用真实模型）',
}

/**
 * 云端厂商 → 用户能认出的公司名。凡是会出网的提供者都必须在这里，
 * 否则界面就无法回答「我的对话到底发给了谁」。
 */
const CLOUD_VENDORS: Record<string, string> = {
  openai: 'OpenAI',
  anthropic: 'Anthropic',
  claude: 'Anthropic',
  deepseek: 'DeepSeek',
  azure: 'Microsoft Azure OpenAI',
  moonshot: '月之暗面（Kimi）',
  kimi: '月之暗面（Kimi）',
  google: 'Google',
  gemini: 'Google',
}

export function providerLabel(type: string): string {
  const key = String(type ?? '').toLowerCase()
  return PROVIDER_LABELS[key] ?? (type || '未指定')
}

export function isCloudProvider(type: string): boolean {
  return Object.prototype.hasOwnProperty.call(CLOUD_VENDORS, String(type ?? '').toLowerCase())
}

/** 云端提供者的公司名；本地/演示返回空串。 */
export function cloudVendor(type: string): string {
  return CLOUD_VENDORS[String(type ?? '').toLowerCase()] ?? ''
}

/** 从端点里取主机名；解析失败时返回空串（不猜）。 */
function hostOf(url: string): string {
  try {
    return new URL(url).host
  } catch {
    return ''
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

/**
 * 只有已保存且「测试连接」通过的模型才允许成为默认。
 *
 * 与后端的 409 校验同一条规则：聊天会优先调用默认模型，没测过就设为默认，
 * 等于把用户往一个可能不可用的模型上引。新建（`undefined`）一律不允许。
 */
export function canBecomeDefault(model: { verified?: boolean } | undefined): boolean {
  return model?.verified === true
}

/**
 * 只认「版本冲突」这一种 409。
 *
 * 写接口还有别的 409（例如改端点时 `MODEL_KEY_TARGET_CHANGED` 要求重新输入
 * Key），那些是普通的业务错误；误当成"另一个窗口改过"会让用户去刷新，而真正
 * 要做的是把 Key 补上。
 */
function isVersionConflict(err: unknown): err is ApiError {
  return (
    err instanceof ApiError &&
    err.status === 409 &&
    err.code === 'MODEL_VERSION_CONFLICT'
  )
}

/**
 * 另一个窗口保存后的版本 vs 打开表单时的快照 → 可读的差异说明。
 *
 * 只陈述"变了什么"，不替用户决定：覆盖还是放弃由用户看完后自己点。
 */
export function describeModelDiff(base: ModelRecord, latest: ModelRecord): string {
  const changes: string[] = []
  if (base.name !== latest.name) {
    changes.push(`名称「${base.name}」→「${latest.name}」`)
  }
  if (base.provider !== latest.provider) {
    changes.push(`服务商 ${providerLabel(base.provider)} → ${providerLabel(latest.provider)}`)
  }
  if (base.model !== latest.model) {
    changes.push(`模型名 ${base.model} → ${latest.model}`)
  }
  if ((base.base_url ?? '') !== (latest.base_url ?? '')) {
    changes.push(`端点 ${base.base_url || '（默认）'} → ${latest.base_url || '（默认）'}`)
  }
  if (base.timeout !== latest.timeout) {
    changes.push(`超时 ${base.timeout} 秒 → ${latest.timeout} 秒`)
  }
  if (base.enabled !== latest.enabled) {
    changes.push(`状态 ${base.enabled ? '启用' : '禁用'} → ${latest.enabled ? '启用' : '禁用'}`)
  }
  if (base.is_default !== latest.is_default) {
    changes.push(`默认 ${base.is_default ? '是' : '否'} → ${latest.is_default ? '是' : '否'}`)
  }
  if (
    base.api_key_set !== latest.api_key_set ||
    (base.api_key_masked ?? '') !== (latest.api_key_masked ?? '')
  ) {
    changes.push('API Key 已更新')
  }
  return changes.join('；')
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

export interface ModelManagerProps {
  session: Session
  /**
   * 深链 `?p=settings&model=<id|名称|模型名>` 带来的目标模型。
   *
   * 旧构建只认 `?p=`，会忽略这个参数并停在设置页；那种情况由桌面端提示
   * 「请点编辑」，本组件不假装已经展开。
   */
  focusModelId?: string | null
}

export function ModelManager({ session, focusModelId }: ModelManagerProps) {
  const canWrite = canManageModels(session)
  // 20 秒轮询：真实对话成功后 verified 由后端回写，停在模型管理页时应自动
  // 从「未测试」变成「已验证」，不能只改数据库、等用户手动刷新才看得见。
  const list = useApi<ModelsResponse>('/v1/models', 20_000)
  const presets = useApi<PresetsResponse>('/v1/models/presets', 0)

  const [form, setForm] = useState<FormState>(EMPTY_FORM)
  const [editingId, setEditingId] = useState<string | null>(null)
  /**
   * 打开编辑时服务端那一行的 `updated_at`。
   * 保存（PUT）时作为 `base_updated_at` 带回，服务端据此判断是否被别的窗口改过。
   */
  const [editingUpdatedAt, setEditingUpdatedAt] = useState<string | null>(null)
  /** 打开编辑时那一行的快照：冲突后用来告诉用户"对方改了什么"。 */
  const [editingBase, setEditingBase] = useState<ModelRecord | null>(null)
  /** 版本冲突（409 `MODEL_VERSION_CONFLICT`）：保留填写内容，等用户决定覆盖还是放弃。 */
  const [conflict, setConflict] = useState<{
    message: string
    base: ModelRecord
  } | null>(null)
  const [reloadingLatest, setReloadingLatest] = useState(false)
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
  const keyInputRef = useRef<HTMLInputElement | null>(null)
  /** 深链已处理过的值：同一次打开只展开一次，之后用户手动切换不被打断。 */
  const handledFocusRef = useRef<string | null>(null)
  /** 等编辑表单提交到 DOM 后再聚焦，避免在 render 阶段操作节点。 */
  const pendingFocusRef = useRef<string | null>(null)

  const models = list.data?.models ?? []

  /**
   * 深链落地：把 `?p=settings&model=<目标>` 变成"展开这一条 + 光标在 Key 上"。
   *
   * 匹配顺序是 id → 名称 → 模型名。桌面端手里只有本地注册表的名称/模型名，
   * 不一定知道前端记录的 uuid；三种都认才不会出现"参数传了但没反应"。
   * 找不到时给出可执行提示，不静默停在列表页。
   */
  useEffect(() => {
    const wanted = (focusModelId ?? '').trim()
    if (!wanted || !canWrite) return
    if (handledFocusRef.current === wanted) return
    if (models.length === 0) return
    handledFocusRef.current = wanted
    const match =
      models.find((item) => item.id === wanted) ||
      models.find((item) => item.name === wanted) ||
      models.find((item) => item.model === wanted)
    if (!match) {
      setNotice(`没有找到「${wanted}」这条模型配置，请在下方列表里点「编辑」后填写 Key。`)
      return
    }
    setForm(formFromModel(match))
    setEditingId(match.id)
    setEditingUpdatedAt(match.updated_at || null)
    setEditingBase(match)
    setConflict(null)
    setError(null)
    setProbe({ state: 'idle' })
    setLastTest(null)
    setModelList(null)
    setNotice(`已定位到「${match.name}」，请填写 API Key 后保存。`)
    pendingFocusRef.current = match.id
  }, [focusModelId, canWrite, models])

  /** 编辑态生效后再滚动与聚焦 —— 此时 `#model-form-key` 才真的在页面里。 */
  useEffect(() => {
    if (!pendingFocusRef.current || editingId !== pendingFocusRef.current) return
    pendingFocusRef.current = null
    document.getElementById('settings-models')?.scrollIntoView?.({
      behavior: 'smooth',
      block: 'start',
    })
    // 等一帧：滚动可能改变焦点，聚焦放在它之后。
    window.setTimeout(() => {
      keyInputRef.current?.focus()
      keyInputRef.current?.select()
    }, 0)
  }, [editingId])

  const providers = list.data?.supported_providers ?? []
  const busy = saving || rowBusy !== null
  // 「设为默认」只对**已保存且测试通过**的模型开放：新建的模型还没测过，
  // 后端会 409，所以这里也不给它勾选的机会（勾了也只能失败）。
  const editingRecord = editingId
    ? (list.data?.models ?? []).find((item) => item.id === editingId)
    : undefined
  const canMarkDefault = canBecomeDefault(editingRecord)

  const probeResult = probe.state === 'done' ? probe.result : null
  const probeMessage = probeResult
    ? mapProbeReason(
        probeResult.reason,
        probeResult.latency_ms,
        probeResult.detail,
        probeResult.notice,
      )
    : ''
  // 云端才需要"对话会发给谁"的说明；本地提供者不显示，避免噪音。
  const cloudName = cloudVendor(form.provider)
  const cloudHost = form.baseUrl.trim() ? hostOf(form.baseUrl.trim()) : ''

  function update<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((prev) => ({ ...prev, [key]: value }))
  }

  function resetForm() {
    // apiKey 只存在于这份临时状态；重置即清空，避免残留到下一次提交。
    setForm(EMPTY_FORM)
    setEditingId(null)
    setEditingUpdatedAt(null)
    setEditingBase(null)
    setConflict(null)
    setLastTest(null)
    setModelList(null)
  }

  function applyPreset(preset: ProviderPreset) {
    setForm((prev) => ({ ...prev, ...presetFormPatch(preset) }))
    setModelList(null)
    setNotice(
      preset.hint
        ? `已套用预设「${preset.label}」：${preset.hint}`
        : preset.custom
          ? '已切到 OpenAI 兼容模式：请填写你自己的端点地址与模型名。'
          : `已套用预设「${preset.label}」，端点与模型名仍可自由修改。`,
    )
    setError(null)
  }

  function beginEdit(item: ModelRecord) {
    setForm(formFromModel(item))
    setEditingId(item.id)
    setEditingUpdatedAt(item.updated_at || null)
    setEditingBase(item)
    setConflict(null)
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
    setConflict(null)
    setNotice(null)
    try {
      const payload: Record<string, unknown> = {
        name: form.name.trim(),
        provider: form.provider,
        model: form.model.trim(),
        base_url: form.baseUrl.trim() || null,
        timeout: Number(form.timeout),
        enabled: form.enabled,
      }
      // 只有已保存且测试通过的模型才允许提交 "设为默认"：后端对未验证/新建
      // 模型一律 409，前端不再发送一个必然失败的字段。
      if (canMarkDefault) payload.is_default = form.isDefault
      // 编辑时留空 = 不修改；新增时留空 = 不设置密钥。两种情况都不发送该字段。
      if (form.apiKey) payload.api_key = form.apiKey

      if (editingId) {
        // 带上打开表单时读到的版本号：另一窗口先保存过就返回 409，而不是
        // 用整表单静默覆盖对方的字段。新建（POST）不带这个字段。
        if (editingUpdatedAt) payload.base_updated_at = editingUpdatedAt
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
      const base = editingId
        ? editingBase ?? (list.data?.models ?? []).find((item) => item.id === editingId)
        : undefined
      if (editingId && base && isVersionConflict(err)) {
        // 冲突不是"更新失败"：用户填的内容还在，先给可操作的下一步，
        // 看完对方改了什么再决定覆盖还是放弃。
        setConflict({ message: err.message, base })
      } else {
        fail(err, editingId ? '更新失败' : '添加失败')
      }
    } finally {
      setSaving(false)
    }
  }

  /**
   * 冲突后重新读取这一条的最新配置。
   *
   * 只更新"用于判断冲突的版本号"与列表，**不覆盖用户正在填的内容** ——
   * 对方的值通过差异说明展示，用不用它（覆盖还是放弃）由用户自己决定。
   */
  async function reloadLatest() {
    if (!conflict || !editingId) return
    setReloadingLatest(true)
    setError(null)
    try {
      const data = await apiFetch<ModelsResponse>('/v1/models')
      const latest = (data.models ?? []).find((item) => item.id === editingId)
      if (!latest) {
        setNotice(`「${conflict.base.name}」已被另一个窗口删除，已退出编辑。`)
        resetForm()
        list.reload()
        return
      }
      const diff = describeModelDiff(conflict.base, latest)
      setEditingUpdatedAt(latest.updated_at || null)
      setEditingBase(latest)
      setConflict(null)
      setNotice(
        diff
          ? `已加载最新配置，另一个窗口改动了：${diff}。你填写的内容仍在：再点「保存修改」会用你的版本覆盖，点「取消编辑」则放弃。`
          : '已加载最新配置：字段与你打开表单时相同（只有保存时间变了）。再点「保存修改」即可覆盖。',
      )
      list.reload()
    } catch (err) {
      fail(err, '重新加载最新配置失败')
    } finally {
      setReloadingLatest(false)
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
        message: mapProbeReason(result.reason, result.latency_ms, result.detail, result.notice),
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
        message: mapProbeReason(result.reason, result.latency_ms, result.detail, result.notice),
        signature: `${item.provider}|${item.model}|${item.base_url ?? ''}`,
      })
      // 测试结果会落库（verified）：不刷新列表的话"设为默认"按钮与
      // 「未测试」徽标会停留在旧状态，看起来像测了没用。
      list.reload()
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
                <strong>保存配置</strong>：保存后点行尾的「测试连接」，结果会记到该模型上。
              </li>
              <li>
                <strong>设为默认</strong>：测试通过后才能设为默认；下一条消息立即优先使用它，失败会按顺序回落。
              </li>
            </ol>
          </div>

          {!session.isRoot && (
            <p className="os-note" role="note">
              这里配置的模型只属于你自己的账号：Key 归属与用量都记在你名下，其他账号看不到、也用不到你的 Key。
            </p>
          )}

          <h3 className="os-model-form-title">
            {editingId ? '编辑模型' : '添加模型'}
          </h3>
          {list.error ? (
            <div className="os-alert" role="alert">
              <span>
                服务商与已有模型没读到：{list.error}。下面的服务商下拉暂时是空的，读回来再选。
              </span>
              <button
                className="os-btn os-btn-ghost"
                type="button"
                style={{ width: 'auto', flex: 'none' }}
                onClick={list.reload}
              >
                重试
              </button>
            </div>
          ) : null}
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
                {(presets.data?.presets ?? []).map((preset, index) => (
                  <button
                    // 旧版运行实例的 /v1/models/presets 没有 id 字段：兜底到 label/序号，
                    // 避免 React "missing unique key" 警告。
                    key={preset.id ?? preset.label ?? index}
                    className="os-btn os-btn-ghost"
                    type="button"
                    disabled={busy}
                    title={preset.hint ?? '套用后仍可修改端点与模型名'}
                    onClick={() => applyPreset(preset)}
                  >
                    {preset.label}
                  </button>
                ))}
              </div>
              {presets.loading ? (
                <p className="os-note" role="status">
                  正在读取服务商预设…
                </p>
              ) : presets.error ? (
                <p className="os-note" role="status">
                  服务商预设没读到：{presets.error}。不影响手动填写。
                  <button
                    className="os-link"
                    type="button"
                    style={{ flex: 'none' }}
                    onClick={presets.reload}
                  >
                    重试
                  </button>
                </p>
              ) : (presets.data?.presets ?? []).length === 0 ? (
                <p className="os-note" role="status">
                  这次没有内置预设，直接在下面手动填写服务商、端点与模型名即可。
                </p>
              ) : null}
              <p className="os-note">
                内置服务商不再写死：套用预设只是替你填好协议、端点与模型名；
                任何 OpenAI 兼容服务都可以用「OpenAI 兼容（自定义端点）」接入，
                模型名不做白名单校验。
              </p>
              <p className="os-note">
                Azure OpenAI 的原生端点（*.openai.azure.com）不在通用路径里：
                <strong>需用兼容代理接入，或等后续适配器</strong>；
                直接填原生端点会连不通，不用反复试。
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
                      {isCloudProvider(provider)
                        ? ` · 你的对话会发给${cloudVendor(provider)}`
                        : ''}
                    </option>
                  ))}
                </select>
              </div>
              {providers.length === 0 ? (
                <p className="os-note" role="status">
                  {list.loading
                    ? '正在读取可用服务商…'
                    : list.error
                      ? '可用服务商没读到，所以下拉是空的；先点上面的「重试」，读回来再选。'
                      : '这次没有读到任何可用服务商，下拉暂时选不了；可以先去「诊断」页看看模型服务是否正常。'}
                </p>
              ) : null}
              {cloudName && (
                <p className="os-note" role="note">
                  你选择的是<strong>云端模型</strong>：你发送的对话（问题与上下文）
                  会发给
                  <strong>{cloudHost || cloudName}</strong>
                  {cloudHost
                    ? '（你自己填写的端点地址）。'
                    : ' 的服务器，不是本机。'}
                </p>
              )}
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
                // 这些是"点一下填入"的按钮，不是列表项：用 role="group" 包住，
                // 保留每个 button 的按钮语义（若改成列表项角色，读屏只念"列表项"，
                // 不再提示可点击，键盘用户不知道能按）。
                <div className="os-model-form-foot" role="group" aria-label="可用模型">
                  {modelList.models.map((modelName) => (
                    <button
                      key={modelName}
                      className="os-btn os-btn-ghost"
                      type="button"
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
                  ref={keyInputRef}
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
                  disabled={!canMarkDefault || busy}
                  checked={form.isDefault}
                  onChange={(event) => update('isDefault', event.target.checked)}
                />
                设为默认模型
              </label>
              <p className="os-note" role="note">
                {canMarkDefault
                  ? '聊天会优先使用这个模型；它失败时按顺序尝试其他已启用的模型。'
                  : '只有「测试连接」通过的模型才能设为默认：新模型请先保存，点「测试连接」通过后再回来勾选。'}
              </p>
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

      {conflict && (
        <div className="os-alert" role="alert">
          <span className="os-model-error">
            {conflict.message}
            {' '}
            你填写的内容还没有提交：先点「重新加载最新配置」看清另一个窗口改了什么，再决定用你的版本覆盖，或点「取消编辑」放弃。
          </span>
          <button
            className="os-btn os-btn-ghost"
            type="button"
            disabled={busy || reloadingLatest}
            style={{ width: 'auto', flex: 'none' }}
            onClick={() => void reloadLatest()}
          >
            {reloadingLatest ? '加载中…' : '重新加载最新配置'}
          </button>
        </div>
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
                        {item.verified ? (
                          <span
                            className="os-badge"
                            data-tone="ok"
                            title={
                              item.last_verified_at
                                ? `最近一次真实对话成功于 ${item.last_verified_at}`
                                : '已通过真实模型调用验证'
                            }
                          >
                            已验证
                          </span>
                        ) : (
                          <span
                            className="os-badge"
                            data-tone="muted"
                            title="真实对话成功或点「测试连接」通过后，才会标记为已验证"
                          >
                            未测试
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
                            disabled={busy || item.is_default || !item.enabled || !canBecomeDefault(item)}
                            title={canBecomeDefault(item) ? undefined : '先点「测试连接」通过，才能设为默认'}
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
