/**
 * 鎏灏对话 HTTP 客户端 — 通过 SSE 流式读取回复。
 *
 * 浏览器原生 EventSource 只支持 GET，而后端对话端点是 POST，因此这里用
 * fetch + ReadableStream 手动解析 Server-Sent Events（一行一个 data: 帧）。
 *
 * 鉴权：`/v1/chat/*` 已挂后端闸门（与 `apiFetch`/`policyClient` 同一约定），
 * 因此**每一个**请求都必须带 `Authorization: Bearer`。此文件原先用裸 fetch
 * 漏了令牌 —— 闸门一上，聊天就会 401，故统一走下面两个helper。
 */

import { clearSession, tokenHeaders, UNAUTHENTICATED_EVENT } from './auth';
import { ApiError } from './api';
import {
  BUDGET_DENIAL_MESSAGE,
  BUDGET_DENIAL_NEXT_STEP,
  LOCAL_SERVICE_OFFLINE_MANUAL,
  PERMISSION_DENIAL_MESSAGE,
} from './errorMessage';

/** 组装带会话令牌的请求头。
 *
 * 委托给 `auth.tokenHeaders`：它同时写私有头（`X-Liuhao-Token`）与标准头，
 * 前者是托管边缘网关下唯一能原样穿过的通道（见 `auth.TOKEN_HEADER`）。
 */
function authHeaders(extra?: HeadersInit): Headers {
  return tokenHeaders(extra);
}

/** 401 = 会话失效：清本地会话并广播，让应用退回登录页（同 apiFetch）。 */
function handleUnauthorized(status: number): void {
  if (status !== 401) return;
  clearSession();
  try {
    window.dispatchEvent(new CustomEvent(UNAUTHENTICATED_EVENT));
  } catch {
    // 非浏览器环境：忽略。
  }
}

/** 工具调用事件（后端 chat_stream 的 tool 事件）。 */
export interface ToolEvent {
  type: 'tool';
  name: string;
  args: Record<string, unknown>;
  /** 后端已改为结构化 JSON（dict/list/scalar）；旧记录仍是字符串。 */
  output: unknown;
  /** 工具调用 JSON 原文，前端据此从流式内容里精确剥离。 */
  raw?: string;
}

/** 流结束事件：`status` 不是 `completed` 就表示这一轮失败/被拒绝。 */
export interface DoneEvent {
  type: 'done';
  status: string;
  /** 失败分类（后端另一支正在透传）：quota_exhausted / key_revoked / rate_limited / unreachable 等。 */
  reason?: string;
  /** 后端给的中性失败说明；技术细节只进日志。 */
  error?: string;
  turn?: number;
}

/** 流式事件联合类型：逐 token 文本、工具调用，或结束帧。 */
export type ChatEvent =
  | { type: 'token'; content: string }
  | ToolEvent
  | DoneEvent;

/**
 * 流式发送一条消息，逐事件产出（token / tool）。
 * @param message 用户消息
 * @param sessionId 会话 ID（多轮独立）
 */
export async function* streamChat(
  message: string,
  sessionId: string,
  userId?: string,
  modelId?: string,
): AsyncGenerator<ChatEvent> {
  const body: Record<string, unknown> = { message, session_id: sessionId };
  // 声明用户身份 → 后端把画像主体绑定为 user-{id}，同一用户跨会话共享画像。
  if (userId) body.user_id = userId;
  // 指定本次对话使用的模型配置；为空时后端按"当前默认模型"解析。
  if (modelId) body.model_id = modelId;
  const res = await fetch('/v1/chat/stream', {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify(body),
  });

  if (!res.ok || !res.body) {
    handleUnauthorized(res.status);
    const text = await res.text().catch(() => '');
    // 保留后端稳定错误码：402 budget_exceeded / 403 permission_denied 要能
    // 走到不同的中文文案和"下一步"入口，而不是被压成一句通用错误。
    let body: unknown = null;
    try {
      body = JSON.parse(text);
    } catch {
      body = null;
    }
    const record = (body && typeof body === 'object' ? body : {}) as {
      code?: unknown;
      message?: unknown;
      detail?: unknown;
    };
    const detail =
      record.detail && typeof record.detail === 'object' && !Array.isArray(record.detail)
        ? (record.detail as { code?: unknown; message?: unknown })
        : {};
    const code =
      typeof record.code === 'string'
        ? record.code
        : typeof detail.code === 'string'
          ? detail.code
          : null;
    const message =
      typeof record.message === 'string'
        ? record.message
        : typeof detail.message === 'string'
          ? detail.message
          : '';
    throw new ApiError(res.status, message || `请求失败（${res.status}）`, code);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const lines = buffer.split('\n');
    buffer = lines.pop() ?? '';

    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed.startsWith('data:')) continue;
      const payload = trimmed.slice(5).trim();
      try {
        const evt = JSON.parse(payload);
        if (evt.type === 'token' && evt.content) {
          yield { type: 'token', content: evt.content as string };
        } else if (evt.type === 'tool') {
          yield {
            type: 'tool',
            name: evt.name as string,
            args: (evt.args ?? {}) as Record<string, unknown>,
            output: evt.output as unknown,
            raw: evt.raw as string | undefined,
          };
        } else if (evt.type === 'done') {
          // 结束帧必须透出：HTTP 200 不代表这一轮成功，失败分类都在 done 里。
          yield {
            type: 'done',
            status: typeof evt.status === 'string' ? evt.status : 'completed',
            reason: typeof evt.reason === 'string' ? evt.reason : undefined,
            error: typeof evt.error === 'string' ? evt.error : undefined,
            turn: typeof evt.turn === 'number' ? evt.turn : undefined,
          };
        }
      } catch {
        // 忽略无法解析的帧（诚实：不因单个坏帧中断流）
      }
    }
  }
}

/** 读取或生成会话 ID（localStorage 持久化，重启浏览器仍续上同一会话）。 */
export function getSessionId(): string {
  const KEY = 'liuhao.session_id';
  let id = localStorage.getItem(KEY);
  if (!id) {
    id = newSessionId();
    localStorage.setItem(KEY, id);
  }
  return id;
}

/** 持久化当前会话 ID。 */
export function setSessionId(id: string): void {
  localStorage.setItem('liuhao.session_id', id);
}

/** 生成一个新的会话 ID。 */
export function newSessionId(): string {
  return `session-${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
}

/** 读取当前用户 ID（可为空——未声明身份时后端按会话主体记画像）。 */
export function getUserId(): string {
  return localStorage.getItem('liuhao.user_id') || '';
}

/** 持久化当前用户 ID（传空串表示清除身份声明）。 */
export function setUserId(id: string): void {
  const v = (id || '').trim();
  if (v) localStorage.setItem('liuhao.user_id', v);
  else localStorage.removeItem('liuhao.user_id');
}

/** 当前选择的对话模型配置 id（空串 = 使用后端当前默认模型）。 */
export function getSelectedModelId(): string {
  return localStorage.getItem('liuhao.model_id') || '';
}

/** 持久化对话模型选择；传空串表示恢复"跟随默认模型"。 */
export function setSelectedModelId(id: string): void {
  const v = (id || '').trim();
  if (v) localStorage.setItem('liuhao.model_id', v);
  else localStorage.removeItem('liuhao.model_id');
}

/** 会话历史记录。 */
export interface HistoryEntry {
  role: string;
  content: string;
}

export interface SessionHistory {
  session_id: string;
  history: HistoryEntry[];
  count: number;
  /** 该会话消息总数（分页后 count 只是本页条数）。 */
  total?: number;
  /** 更早还有消息（"加载更早的消息"用 offset=已有条数 继续取）。 */
  has_more?: boolean;
  limit?: number | null;
  offset?: number;
  user_id?: string | null;
  profile_principal?: string;
}

/** 加载某会话的历史消息（后端从持久化 store 读，跨后端重启有效）。
 *
 * 省略 `opts` 时保持旧的全量语义；聊天页默认取最近 200 条，
 * "加载更早"传 `offset=当前已渲染消息数`（offset 从最近一条往前数）。
 */
export async function loadHistory(
  sessionId: string,
  userId?: string,
  opts?: { limit?: number; offset?: number },
): Promise<SessionHistory> {
  const parts = [`session_id=${encodeURIComponent(sessionId)}`];
  if (userId) parts.push(`user_id=${encodeURIComponent(userId)}`);
  if (opts?.limit != null) parts.push(`limit=${opts.limit}`);
  if (opts?.offset != null) parts.push(`offset=${opts.offset}`);
  const res = await fetch(`/v1/chat/history?${parts.join('&')}`, {
    headers: authHeaders(),
  });
  if (!res.ok) {
    handleUnauthorized(res.status);
    throw new Error(`加载历史失败（${res.status}）`);
  }
  return (await res.json()) as SessionHistory;
}

/** 会话列表一页。 */
export interface SessionPage {
  sessions: string[];
  total: number;
  has_more: boolean;
  limit: number | null;
  offset: number;
}

/** 列出后端活跃会话（分页；省略参数 = 全量，兼容旧调用）。 */
export async function listSessions(
  limit?: number,
  offset = 0,
): Promise<SessionPage> {
  const parts: string[] = [];
  if (limit != null) parts.push(`limit=${limit}`);
  if (offset > 0) parts.push(`offset=${offset}`);
  const query = parts.length ? `?${parts.join('&')}` : '';
  const res = await fetch(`/v1/chat/sessions${query}`, { headers: authHeaders() });
  if (!res.ok) {
    handleUnauthorized(res.status);
    throw new Error(`获取会话列表失败（${res.status}）`);
  }
  const data = (await res.json()) as {
    sessions?: string[];
    total?: number;
    has_more?: boolean;
    limit?: number | null;
    offset?: number;
  };
  const sessions = data.sessions ?? [];
  return {
    sessions,
    total: data.total ?? sessions.length,
    has_more: data.has_more ?? false,
    limit: data.limit ?? null,
    offset: data.offset ?? 0,
  };
}

/** 一条搜索命中（一条消息）。 */
export interface SessionSearchHit {
  session_id: string;
  turn: number;
  role: string;
  snippet: string;
  created_at: number;
}

/** 历史搜索响应（含降级/建索引进度，界面必须如实展示 notice）。 */
export interface SessionSearchResult {
  query?: string;
  results: SessionSearchHit[];
  count: number;
  total: number;
  has_more: boolean;
  limit: number;
  offset: number;
  mode: string;
  degraded: boolean;
  ready: boolean;
  indexed: number;
  index_total: number;
  notice: string;
}

/** 在当前账号的会话历史里全文搜索（中文 2 字及以上子串可直接搜）。 */
export async function searchSessions(
  query: string,
  limit = 20,
  offset = 0,
): Promise<SessionSearchResult> {
  const parts = [
    `q=${encodeURIComponent(query)}`,
    `limit=${limit}`,
    `offset=${offset}`,
  ];
  const res = await fetch(`/v1/sessions/search?${parts.join('&')}`, {
    headers: authHeaders(),
  });
  if (!res.ok) {
    handleUnauthorized(res.status);
    throw new Error(`搜索失败（${res.status}）`);
  }
  return (await res.json()) as SessionSearchResult;
}

/** 删除一个会话（后端清空历史 + 移除实例）。 */
export async function deleteSession(sessionId: string): Promise<boolean> {
  const res = await fetch(
    `/v1/chat/sessions/${encodeURIComponent(sessionId)}`,
    { method: 'DELETE', headers: authHeaders() },
  );
  if (!res.ok) {
    handleUnauthorized(res.status);
    throw new Error(`删除会话失败（${res.status}）`);
  }
  const data = (await res.json()) as { deleted?: boolean };
  return data.deleted === true;
}

const SESSIONS_KEY = 'liuhao.sessions';

/** 读取本地维护的会话 ID 清单（跨浏览器会话保留）。 */
export function getLocalSessions(): string[] {
  try {
    const raw = localStorage.getItem(SESSIONS_KEY);
    const list = raw ? (JSON.parse(raw) as unknown) : [];
    return Array.isArray(list) ? (list as string[]) : [];
  } catch {
    return [];
  }
}

/** 持久化本地会话 ID 清单。 */
export function saveLocalSessions(list: string[]): void {
  localStorage.setItem(SESSIONS_KEY, JSON.stringify(list));
}

const META_KEY = 'liuhao.session_meta';

/** 会话标题元数据：用"首条用户消息"而不是截断的会话 ID 展示。 */
export interface SessionMeta {
  title: string;
  /** 秒级时间戳（与审计/页面其他时间字段一致）。 */
  updatedAt: number;
}

/** 读取本地会话元数据（id → 标题 + 更新时间）。 */
export function getSessionMeta(): Record<string, SessionMeta> {
  try {
    const raw = localStorage.getItem(META_KEY);
    const parsed = raw ? (JSON.parse(raw) as unknown) : {};
    return parsed && typeof parsed === 'object'
      ? (parsed as Record<string, SessionMeta>)
      : {};
  } catch {
    return {};
  }
}

/** 持久化本地会话元数据。 */
export function saveSessionMeta(meta: Record<string, SessionMeta>): void {
  try {
    localStorage.setItem(META_KEY, JSON.stringify(meta));
  } catch {
    // 存储满/隐私模式：忽略，会话本身仍可用。
  }
}

/** 从历史里取首条用户消息做标题（压掉换行与多余空白）。 */
export function titleFromHistory(history: HistoryEntry[]): string {
  const first = history.find(
    (entry) => entry.role === 'user' && entry.content.trim(),
  );
  return makeSessionTitle(first?.content ?? '');
}

/** 截取一段可读标题（避免把整段业务指令塞进下拉框）。 */
export function makeSessionTitle(text: string): string {
  const clean = text.replace(/\s+/g, ' ').trim();
  if (!clean) return '';
  return clean.length > 16 ? `${clean.slice(0, 16)}…` : clean;
}

/**
 * 把结束帧里的失败分类翻译成可行动的卡片文案。
 *
 * 后端在 `done` 事件里给 `reason`；这里只按约定消费，不猜测、不改分类。
 * `fallback` 是后端已给的中性说明（`done.error`），没有分类时用它兜底。
 */
export function chatFailureText(reason?: string, fallback?: string): string {
  switch ((reason ?? '').trim().toLowerCase()) {
    case 'quota_exhausted':
      return '模型额度已用完。请到「系统设置 → 模型管理」充值，或换一个还有额度的 Key 后重试。';
    case 'key_revoked':
      return '这个 Key 已被吊销或停用。请到「系统设置 → 模型管理」重新生成并填入新的 Key。';
    case 'rate_limited':
      return '请求被上游限流了。请稍后重试，或稍等片刻再发一次。';
    case 'budget_exceeded':
      return `${BUDGET_DENIAL_MESSAGE}${BUDGET_DENIAL_NEXT_STEP}`;
    case 'permission_denied':
      return PERMISSION_DENIAL_MESSAGE;
    case 'unreachable': {
      // 后端已经给出带模型名的完整说明时优先用它：固定文案不知道用户配的是
      // 哪个模型，卡片上就少了"该检查哪个模型"的关键信息。
      const text = (fallback ?? '').trim();
      return text || '连不上模型服务。请检查网络和「系统设置 → 模型管理」里的接口地址后重试。';
    }
    default: {
      const text = (fallback ?? '').trim();
      return text || '这一轮没有成功，请稍后重试。';
    }
  }
}

/**
 * 把聊天的原始错误翻译成人话；原始文本留给"详情"折叠。
 *
 * 典型原始错误：`请求失败（404）：{"detail":"Ollama error 404: {...}"}` ——
 * 直接展示等于让用户读后端内部 JSON，既看不懂也不知道该做什么。
 */
export function friendlyChatError(err: unknown): {
  text: string;
  raw: string;
  /** 402 时告诉界面要渲染"去调额度 / 换更便宜模型"的可点入口。 */
  action?: 'budget';
} {
  const raw = err instanceof Error ? err.message : String(err);
  const lower = raw.toLowerCase();
  const status = typeof (err as { status?: unknown })?.status === 'number'
    ? ((err as { status: number }).status)
    : null;
  const code = typeof (err as { code?: unknown })?.code === 'string'
    ? ((err as { code: string }).code)
    : null;

  if (code === 'budget_exceeded' || status === 402 || raw.includes('402')) {
    return {
      text: `${BUDGET_DENIAL_MESSAGE}${BUDGET_DENIAL_NEXT_STEP}`,
      raw,
      action: 'budget',
    };
  }
  if (code === 'permission_denied') {
    return { text: PERMISSION_DENIAL_MESSAGE, raw };
  }
  // 403 的另一种：请求里用了别人的模型 Key。后端原话是「无权使用该模型配置」，
  // 这里补一句「往哪里去」，但不给预算按钮（不是钱的问题）。
  if (code === 'ACCOUNT_KEY_NOT_OWNED') {
    return {
      text: '无权使用该模型配置：这个 Key 不在你的账号下。请到「我的 Key」添加你自己的模型，或换用有权限的模型后重试。',
      raw,
    };
  }
  // 成员没有自己的模型 Key 时后端返回 409（状态码正确），但这里要说人话：
  // 直接指路「我的 Key」，不把英文 code 甩给用户。
  if (code === 'ACCOUNT_KEY_NOT_CONFIGURED') {
    return {
      text: '你还没有自己的模型 Key。请到「我的 Key」添加后重试。',
      raw,
    };
  }
  if (lower.includes('failed to fetch') || lower.includes('networkerror')) {
    return { text: LOCAL_SERVICE_OFFLINE_MANUAL, raw };
  }
  if (raw.includes('422')) {
    // 后端校验失败会把整段 input 回显在 detail 里；这里只留可读摘要，
    // 避免 5 万字原文同时进入 React state 和"详情"折叠区。
    return {
      text: '消息过长或格式不合法：单条消息最多 8000 字，请精简或分段发送',
      raw: raw.length > 600 ? `${raw.slice(0, 600)}…` : raw,
    };
  }
  // 物理 429/401 的 `ApiError.message` 只带后端给的中文说明，不含状态码；
  // 只看 raw 里的 "429" 会让限流落进「对话失败」的通用兜底，和 402/403 混淆。
  if (status === 401 || raw.includes('401')) {
    return { text: '会话已失效，请重新登录', raw };
  }
  if (status === 403 || raw.includes('403')) {
    return { text: '当前账号没有对话权限', raw };
  }
  if (status === 429 || raw.includes('429')) {
    return { text: '请求过于频繁，请稍后重试', raw };
  }
  if (raw.includes('408') || lower.includes('timeout') || lower.includes('超时')) {
    return { text: '请求超时，请稍后重试', raw };
  }
  if (
    lower.includes('ollama error 404') ||
    lower.includes('model not found') ||
    lower.includes('model_not_found') ||
    lower.includes('不存在') ||
    lower.includes('not found')
  ) {
    return { text: '模型不存在，请在模型管理中确认模型名', raw };
  }
  if (raw.includes('404')) {
    return { text: '模型不存在，请在模型管理中确认模型名', raw };
  }
  if (/请求失败（5\d\d）/.test(raw) || lower.includes('server error')) {
    return { text: 'AI 服务暂时不可用，请稍后重试', raw };
  }
  if (lower.includes('error')) {
    return { text: 'AI 服务返回错误，请稍后重试；若持续出现请查看详情', raw };
  }
  return { text: '对话失败，请稍后重试', raw };
}
