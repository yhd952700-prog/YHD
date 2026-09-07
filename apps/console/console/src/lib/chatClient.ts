/**
 * 鎏灏对话 HTTP 客户端 — 通过 SSE 流式读取回复。
 *
 * 浏览器原生 EventSource 只支持 GET，而后端对话端点是 POST，因此这里用
 * fetch + ReadableStream 手动解析 Server-Sent Events（一行一个 data: 帧）。
 */
export interface StreamDone {
  type: 'done';
  turn: number;
  session_id: string;
}

/**
 * 流式发送一条消息，逐 token 产出回复文本。
 * @param message 用户消息
 * @param sessionId 会话 ID（多轮独立）
 */
export async function* streamChat(
  message: string,
  sessionId: string,
): AsyncGenerator<string> {
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
          yield evt.content;
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
    id = `session-${Date.now().toString(36)}`;
    localStorage.setItem(KEY, id);
  }
  return id;
}
