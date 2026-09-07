/**
 * 鎏灏对话 HTTP 客户端 — 通过 SSE 流式读取回复。
 *
 * 浏览器原生 EventSource 只支持 GET，而后端对话端点是 POST，因此这里用
 * fetch + ReadableStream 手动解析 Server-Sent Events（一行一个 data: 帧）。
 */

/** 工具调用事件（后端 chat_stream 的 tool 事件）。 */
export interface ToolEvent {
  type: 'tool';
  name: string;
  args: Record<string, unknown>;
  output: string;
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
): AsyncGenerator<ChatEvent> {
  const res = await fetch('/v1/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, session_id: sessionId }),
  });

  if (!res.ok || !res.body) {
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
            output: (evt.output ?? '') as string,
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

/** 会话历史记录。 */
export interface HistoryEntry {
  role: string;
  content: string;
}

export interface SessionHistory {
  session_id: string;
  history: HistoryEntry[];
  count: number;
}

/** 加载某会话的历史消息（后端从持久化 store 读，跨后端重启有效）。 */
export async function loadHistory(sessionId: string): Promise<SessionHistory> {
  const res = await fetch(
    `/v1/chat/history?session_id=${encodeURIComponent(sessionId)}`,
  );
  if (!res.ok) throw new Error(`加载历史失败（${res.status}）`);
  return (await res.json()) as SessionHistory;
}

/** 列出后端活跃会话。 */
export async function listSessions(): Promise<string[]> {
  const res = await fetch('/v1/chat/sessions');
  if (!res.ok) throw new Error(`获取会话列表失败（${res.status}）`);
  const data = (await res.json()) as { sessions?: string[] };
  return data.sessions ?? [];
}

/** 删除一个会话（后端清空历史 + 移除实例）。 */
export async function deleteSession(sessionId: string): Promise<boolean> {
  const res = await fetch(
    `/v1/chat/sessions/${encodeURIComponent(sessionId)}`,
    { method: 'DELETE' },
  );
  if (!res.ok) throw new Error(`删除会话失败（${res.status}）`);
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
