import { useCallback, useEffect, useRef, useState } from 'react'
import './App.css'
import { getSessionId, streamChat } from './lib/chatClient'

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

function App() {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [sessionId] = useState(() => getSessionId())
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const send = useCallback(async () => {
    const text = input.trim()
    if (!text || sending) return

    setInput('')
    setSending(true)
    // 追加用户消息 + 一个空的 assistant 占位（流式填充）
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
            next[next.length - 1] = {
              ...last,
              content: last.content + evt.content,
            }
            return next
          })
        } else if (evt.type === 'tool') {
          setMessages((prev) => {
            const next = [...prev]
            const last = next[next.length - 1]
            // 从流式内容里剥离工具调用 JSON 原文（诚实：只显示工具结果卡片）。
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
      <header className="header">
        <div className="header-title">
          <span className="logo">鎏</span>
          <div>
            <h1>鎏灏 · LIUHAO X</h1>
            <p className="subtitle">十源 DNA 统一的 AI 操作系统</p>
          </div>
        </div>
        <div className="session-chip" title="多轮对话独立会话">
          会话 <code>{sessionId}</code>
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
  )
}

export default App
