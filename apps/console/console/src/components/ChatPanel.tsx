import { useCallback, useRef, useState } from 'react'
import {
  deleteSession,
  getLocalSessions,
  getSessionId,
  loadHistory,
  newSessionId,
  saveLocalSessions,
  setSessionId,
  streamChat,
} from '../lib/chatClient'

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

function shortId(id: string): string {
  const s = id.startsWith('session-') ? id.slice('session-'.length) : id
  return s.length > 10 ? `${s.slice(0, 10)}…` : s
}

/** 快捷指令 chips（点击填入输入框）。 */
const QUICK = ['市场分析', '销售机会', '客户策略', 'SEO优化', '数据分析']

export function ChatPanel() {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [sessionId, setSessionIdState] = useState(() => getSessionId())
  const [sessions, setSessions] = useState<string[]>(() => {
    const cur = getSessionId()
    const local = getLocalSessions()
    return local.includes(cur) ? local : [cur, ...local]
  })
  const listRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  const scrollToBottom = useCallback(() => {
    if (listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight
  }, [])

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
        // 后端删除失败也允许本地移除
      }
      setSessions((prev) => {
        const next = prev.filter((s) => s !== id)
        saveLocalSessions(next)
        return next
      })
      if (id === sessionId) newSession()
    },
    [sessionId, newSession],
  )

  const send = useCallback(
    async (text: string) => {
      const value = (text ?? input).trim()
      if (!value || sending) return
      setInput('')
      setSending(true)
      setMessages((prev) => [
        ...prev,
        { role: 'user', content: value },
        { role: 'assistant', content: '', streaming: true, tools: [] },
      ])
      setTimeout(scrollToBottom, 0)
      try {
        for await (const evt of streamChat(value, sessionId)) {
          if (evt.type === 'token') {
            setMessages((prev) => {
              const next = [...prev]
              const last = next[next.length - 1]
              next[next.length - 1] = { ...last, content: last.content + evt.content }
              return next
            })
            scrollToBottom()
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
            content: last.content + `\n\n[后端连接失败] ${msg}`,
            streaming: false,
          }
          return next
        })
      } finally {
        setSending(false)
        scrollToBottom()
      }
    },
    [input, sending, sessionId, scrollToBottom],
  )

  return (
    <section className="jarvis-zone">
      <div className="jarvis-panel">
        <div className="jarvis-left">
          <div className="holo">
            <div className="holo-figure">
              <div className="holo-aura" />
              <div className="holo-ring r1" />
              <div className="holo-ring r2" />
              <div className="holo-human">您</div>
            </div>
          </div>

          <div className="jarvis-intro">
            <div className="jarvis-name">JARVIS</div>
            <div className="jarvis-sub">Your AI Business Partner</div>
          </div>
        </div>

        <div className="jarvis-right">
          <div className="jarvis-greeting">
            <span className="greet-main">老板，我已准备就绪！</span>
            <span className="greet-sub">今天我可以为您分析什么？</span>
          </div>

          <div className="jarvis-chat">
            <div className="chat-log" ref={listRef}>
              {messages.length === 0 ? (
                <div className="chat-empty">请下达业务指令，Jarvis 将为您实时分析。</div>
              ) : (
                messages.map((m, i) => (
                  <div key={i} className={`chat-row ${m.role}`}>
                    <div className="chat-avatar">{m.role === 'user' ? '我' : 'J'}</div>
                    <div className="chat-bubble">
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
            </div>

            <div className="jarvis-input-wrap">
              <textarea
                ref={inputRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault()
                    send(input)
                  }
                }}
                placeholder="请输入您的业务指令..."
                rows={1}
                disabled={sending}
              />
              <button className="jarvis-send" onClick={() => send(input)} disabled={sending || !input.trim()}>
                {sending ? '生成中…' : '发送'}
              </button>
            </div>

            <div className="jarvis-chips">
              {QUICK.map((q) => (
                <button key={q} className="chip" onClick={() => send(q)} disabled={sending}>
                  {q}
                </button>
              ))}
            </div>
          </div>

          <div className="jarvis-sessions">
            <select
              className="sess-select"
              value={sessionId}
              onChange={(e) => switchSession(e.target.value)}
              title="切换会话"
            >
              {sessions.map((id) => (
                <option key={id} value={id}>
                  {shortId(id)}
                </option>
              ))}
            </select>
            <button className="sess-btn" onClick={newSession} title="新会话">
              ＋新会话
            </button>
            <button className="sess-btn danger" onClick={() => removeSession(sessionId)} title="删除会话">
              删除
            </button>
          </div>
        </div>
      </div>
    </section>
  )
}
