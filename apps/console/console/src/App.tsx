import { useCallback, useEffect, useRef, useState } from 'react'
import './App.css'
import {
  deleteSession,
  getLocalSessions,
  getSessionId,
  loadHistory,
  newSessionId,
  saveLocalSessions,
  setSessionId,
  streamChat,
} from './lib/chatClient'

interface ToolCall {
  name: string
  args: Record<string, unknown>
  output: string
}

interface Message {
  role: 'user' | 'assistant'
  content: string
  streaming?: boolean
  tools?: ToolCall[]
}

/** 会话 ID 缩短显示（session- 前缀去掉 + 截断）。 */
function shortId(id: string): string {
  const s = id.startsWith('session-') ? id.slice('session-'.length) : id
  return s.length > 12 ? `${s.slice(0, 12)}…` : s
}

function App() {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [sessionId, setSessionIdState] = useState(() => getSessionId())
  const [sessions, setSessions] = useState<string[]>(() => {
    const cur = getSessionId()
    const local = getLocalSessions()
    return local.includes(cur) ? local : [cur, ...local]
  })
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const switchSession = useCallback(
    async (id: string) => {
      if (id === sessionId || sending) return
      setSessionIdState(id)
      setSessionId(id)
      setMessages([])
      try {
        const data = await loadHistory(id)
        setMessages(
          data.history.map((h) => ({
            role: h.role === 'user' ? 'user' : ('assistant' as const),
            content: h.content,
          })),
        )
      } catch {
        setMessages([])
      }
    },
    [sessionId, sending],
  )

  const newSession = useCallback(() => {
    const id = newSessionId()
    setSessionIdState(id)
    setSessionId(id)
    setMessages([])
    setSessions((prev) => {
      const next = [id, ...prev.filter((s) => s !== id)]
      saveLocalSessions(next)
      return next
    })
  }, [])

  const removeSession = useCallback(
    async (id: string) => {
      try {
        await deleteSession(id)
      } catch {
        // 后端删除失败也允许本地移除（前端本地清单仍为准）
      }
      setSessions((prev) => {
        const next = prev.filter((s) => s !== id)
        saveLocalSessions(next)
        return next
      })
      if (id === sessionId) {
        const nextId = newSessionId()
        setSessionIdState(nextId)
        setSessionId(nextId)
        setMessages([])
      }
    },
    [sessionId],
  )

  const send = useCallback(async () => {
    const text = input.trim()
    if (!text || sending) return

    setInput('')
    setSending(true)
    setMessages((prev) => [
      ...prev,
      { role: 'user', content: text },
      { role: 'assistant', content: '', streaming: true, tools: [] },
    ])

    try {
      for await (const evt of streamChat(text, sessionId)) {
        if (evt.type === 'token') {
          setMessages((prev) => {
            const next = [...prev]
            const last = next[next.length - 1]
            next[next.length - 1] = { ...last, content: last.content + evt.content }
            return next
          })
        } else if (evt.type === 'tool') {
          setMessages((prev) => {
            const next = [...prev]
            const last = next[next.length - 1]
            let content = last.content
            if (evt.raw && content.endsWith(evt.raw)) {
              content = content.slice(0, content.length - evt.raw.length)
            }
            const tools = [
              ...(last.tools ?? []),
              { name: evt.name, args: evt.args, output: evt.output },
            ]
            next[next.length - 1] = { ...last, content, tools }
            return next
          })
        }
      }
      setMessages((prev) => {
        const next = [...prev]
        const last = next[next.length - 1]
        next[next.length - 1] = { ...last, streaming: false }
        return next
      })
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      setMessages((prev) => {
        const next = [...prev]
        const last = next[next.length - 1]
        next[next.length - 1] = {
          ...last,
          content: last.content + `\n\n[连接后端失败] ${msg}`,
          streaming: false,
        }
        return next
      })
    } finally {
      setSending(false)
    }
  }, [input, sending, sessionId])

  return (
    <div className="app">
      <aside className="sidebar">
        <button className="new-session" onClick={newSession}>
          + 新会话
        </button>
        <ul className="session-list">
          {sessions.map((id) => (
            <li
              key={id}
              className={`session-item ${id === sessionId ? 'active' : ''}`}
            >
              <button
                className="session-name"
                onClick={() => switchSession(id)}
                title={id}
              >
                {shortId(id)}
              </button>
              <button
                className="session-del"
                onClick={() => removeSession(id)}
                title="删除会话"
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      </aside>

      <div className="main">
        <header className="header">
          <div className="header-title">
            <span className="logo">鎏</span>
            <div>
              <h1>鎏灏 · LIUHAO X</h1>
              <p className="subtitle">十源 DNA 统一的 AI 操作系统</p>
            </div>
          </div>
          <div className="session-chip" title="当前会话">
            会话 <code>{shortId(sessionId)}</code>
          </div>
        </header>

        <main className="messages">
          {messages.length === 0 ? (
            <div className="empty">
              <p>开始与鎏灏对话</p>
              <p className="empty-hint">
                后端请先启动：<code>.venv\Scripts\python.exe -m uvicorn src.gateway.main:app --port 8080</code>
              </p>
            </div>
          ) : (
            messages.map((m, i) => (
              <div key={i} className={`row ${m.role}`}>
                <div className="avatar">{m.role === 'user' ? '我' : '鎏'}</div>
                <div className="bubble">
                  {m.content || (m.streaming ? <span className="cursor" /> : '…')}
                  {m.tools && m.tools.length > 0 && (
                    <div className="tool-calls">
                      {m.tools.map((t, j) => (
                        <details key={j} className="tool-card" open>
                          <summary className="tool-summary">
                            调用工具 <code>{t.name}</code>
                          </summary>
                          <pre className="tool-output">{t.output}</pre>
                        </details>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            ))
          )}
          <div ref={bottomRef} />
        </main>

        <footer className="composer">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                send()
              }
            }}
            placeholder="输入消息，Enter 发送，Shift+Enter 换行"
            rows={2}
            disabled={sending}
          />
          <button onClick={send} disabled={sending || !input.trim()}>
            {sending ? '生成中…' : '发送'}
          </button>
        </footer>
      </div>
    </div>
  )
}

export default App
