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
import { LOCAL_SERVICE_OFFLINE_MANUAL } from './errorMessage';

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

/** 流式事件联合类型：逐 token 文本，或工具调用。 */
export type ChatEvent =
  | { type: 'token'; content: string }
  | ToolEvent;

/**
 * 流式发送一条消息，逐事件产出（token / tool）。
 * @param message 用户消息
 * @param sessionId 会话 ID（多轮独立）
 */
export async function* streamChat(
  message: string,
  sessionId: string,
  userId?: string,
): AsyncGenerator<ChatEvent> {
  const body: Record<string, unknown> = { message, session_id: sessionId };
  // 声明用户身份 → 后端把画像主体绑定为 user-{id}，同一用户跨会话共享画像。
  if (userId) body.user_id = userId;
  const res = await fetch('/v1/chat/stream', {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify(body),
  });

  if (!res.ok || !res.body) {
    handleUnauthorized(res.status);
    const text = await res.text().catch(() => '');
    throw new Error(`请求失败（${res.status}）：${text}`);
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

/** 会话历史记录。 */
export interface HistoryEntry {
  role: string;
  content: string;
}

export interface SessionHistory {
  session_id: string;
  history: HistoryEntry[];
  count: number;
  user_id?: string | null;
  profile_principal?: string;
}

/** 加载某会话的历史消息（后端从持久化 store 读，跨后端重启有效）。 */
export async function loadHistory(
  sessionId: string,
  userId?: string,
): Promise<SessionHistory> {
  const q = `session_id=${encodeURIComponent(sessionId)}${
    userId ? `&user_id=${encodeURIComponent(userId)}` : ''
  }`;
  const res = await fetch(`/v1/chat/history?${q}`, { headers: authHeaders() });
  if (!res.ok) {
    handleUnauthorized(res.status);
    throw new Error(`加载历史失败（${res.status}）`);
  }
  return (await res.json()) as SessionHistory;
}

/** 列出后端活跃会话。 */
export async function listSessions(): Promise<string[]> {
  const res = await fetch('/v1/chat/sessions', { headers: authHeaders() });
  if (!res.ok) {
    handleUnauthorized(res.status);
    throw new Error(`获取会话列表失败（${res.status}）`);
  }
  const data = (await res.json()) as { sessions?: string[] };
  return data.sessions ?? [];
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
 * 把聊天的原始错误翻译成人话；原始文本留给"详情"折叠。
 *
 * 典型原始错误：`请求失败（404）：{"detail":"Ollama error 404: {...}"}` ——
 * 直接展示等于让用户读后端内部 JSON，既看不懂也不知道该做什么。
 */
export function friendlyChatError(err: unknown): { text: string; raw: string } {
  const raw = err instanceof Error ? err.message : String(err);
  const lower = raw.toLowerCase();

  if (lower.includes('failed to fetch') || lower.includes('networkerror')) {
    return { text: LOCAL_SERVICE_OFFLINE_MANUAL, raw };
  }
  if (raw.includes('401')) {
    return { text: '会话已失效，请重新登录', raw };
  }
  if (raw.includes('403')) {
    return { text: '当前账号没有对话权限', raw };
  }
  if (raw.includes('429')) {
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
