import { useCallback, useEffect, useRef, useState } from 'react'
import {
  deleteSession,
  friendlyChatError,
  getLocalSessions,
  getSessionId,
  getSessionMeta,
  getUserId,
  loadHistory,
  makeSessionTitle,
  newSessionId,
  saveLocalSessions,
  saveSessionMeta,
  setSessionId,
  setUserId,
  streamChat,
  titleFromHistory,
} from '../lib/chatClient'
import type { SessionMeta } from '../lib/chatClient'
import { relativeTime } from '../lib/format'
import type { ActiveProviderStatus } from '../lib/providerStatus'

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
  /** 失败时的人话 + 原始错误（原始文本默认折叠）。 */
  error?: { text: string; raw: string }
}

/** 快捷指令 chips（点击填入输入框）。 */
const QUICK = ['市场分析', '销售机会', '客户策略', 'SEO优化', '数据分析']

interface ChatPanelProps {
  /** 模型状态唯一来源（/v1/health → lib/providerStatus）。 */
  provider: ActiveProviderStatus
  /** "去配置模型"：跳到模型管理。 */
  onConfigureModel: () => void
}

export function ChatPanel({ provider, onConfigureModel }: ChatPanelProps) {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [sessionId, setSessionIdState] = useState(() => getSessionId())
  // 用户身份：声明后画像跨会话共享（未声明则后端按会话主体记画像）。
  const [userId, setUserIdState] = useState(() => getUserId())
  const [sessions, setSessions] = useState<string[]>(() => {
    const cur = getSessionId()
    const local = getLocalSessions()
    return local.includes(cur) ? local : [cur, ...local]
  })
  // 会话标题：首条用户消息前 16 字 + 更新时间。绝不再展示截断的 session ID。
  const [meta, setMeta] = useState<Record<string, SessionMeta>>(() => getSessionMeta())
  const listRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  /** 合并写一条会话元数据（标题只在为空时写入，更新时间每次都刷）。 */
  const recordMeta = useCallback((id: string, patch: Partial<SessionMeta>) => {
    setMeta((prev) => {
      const next = {
        ...prev,
        [id]: {
          title: patch.title ?? prev[id]?.title ?? '',
          updatedAt: patch.updatedAt ?? prev[id]?.updatedAt ?? 0,
        },
      }
      saveSessionMeta(next)
      return next
    })
  }, [])

  const scrollToBottom = useCallback(() => {
    if (listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight
  }, [])

  // 首次进入把当前会话的历史接回来：否则刷新后对话记录"看起来消失了"，
  // 下拉框也只能显示"未命名会话"。只取一次，且不覆盖用户已开始输入的内容。
  useEffect(() => {
    let alive = true
    void (async () => {
      try {
        const data = await loadHistory(sessionId, userId)
        if (!alive) return
        const historyMessages = data.history.map((h) => ({
          role: h.role === 'user' ? ('user' as const) : ('assistant' as const),
          content: h.content,
        }))
        setMessages((prev) => (prev.length === 0 ? historyMessages : prev))
        const title = titleFromHistory(data.history)
        if (title) {
          setMeta((prev) => {
            if (prev[sessionId]?.title) return prev
            const next = { ...prev, [sessionId]: { title, updatedAt: prev[sessionId]?.updatedAt ?? 0 } }
            saveSessionMeta(next)
            return next
          })
        }
      } catch {
        // 首次加载失败不阻塞输入；发送时仍会给出明确错误。
      }
    })()
    return () => {
      alive = false
    }
    // 只在挂载时恢复当前会话。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const switchSession = useCallback(
    async (id: string) => {
      if (id === sessionId || sending) return
      setSessionIdState(id)
      setSessionId(id)
      setMessages([])
      try {
        const data = await loadHistory(id, userId)
        setMessages(
          data.history.map((h) => ({
            role: h.role === 'user' ? 'user' : ('assistant' as const),
            content: h.content,
          })),
        )
        const title = titleFromHistory(data.history)
        if (title) recordMeta(id, { title })
      } catch {
        setMessages([])
      }
    },
    [sessionId, sending, userId, recordMeta],
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
      setMeta((prev) => {
        if (!prev[id]) return prev
        const next = { ...prev }
        delete next[id]
        saveSessionMeta(next)
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
      recordMeta(sessionId, {
        title: meta[sessionId]?.title || makeSessionTitle(value),
        updatedAt: Date.now() / 1000,
      })
      setMessages((prev) => [
        ...prev,
        { role: 'user', content: value },
        { role: 'assistant', content: '', streaming: true, tools: [] },
      ])
      setTimeout(scrollToBottom, 0)
      try {
        for await (const evt of streamChat(value, sessionId, userId || undefined)) {
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
        recordMeta(sessionId, { updatedAt: Date.now() / 1000 })
      } catch (err) {
        // 原始错误（可能是整段后端 JSON）只进"详情"，正文给可行动的中文。
        setMessages((prev) => {
          const next = [...prev]
          const last = next[next.length - 1]
          next[next.length - 1] = {
            ...last,
            streaming: false,
            error: friendlyChatError(err),
          }
          return next
        })
      } finally {
        setSending(false)
        scrollToBottom()
      }
    },
    [input, sending, sessionId, userId, scrollToBottom, recordMeta, meta],
  )

  /** 下拉框标签：首条消息 + 更新时间；没有标题时显示"未命名会话"（绝不显示 ID）。 */
  const sessionLabel = useCallback(
    (id: string) => {
      const m = meta[id]
      const title = m?.title || '未命名会话'
      return m?.updatedAt ? `${title} · ${relativeTime(m.updatedAt)}` : title
    },
    [meta],
  )

  const showModelWarning = !provider.isReal

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
            {/* 模型状态：未配置真实模型时必须在输入框上方明示，而不是让用户
                以为对面是一个真实模型。判据来自 /v1/health（单一事实源）。 */}
            {showModelWarning && (
              <div className="chat-model-banner" role="status">
                <span className="chat-model-badge">模拟回复</span>
                <span className="chat-model-detail">
                  {provider.label}。{provider.detail}
                </span>
                <button className="chat-model-action" onClick={onConfigureModel}>
                  去配置模型
                </button>
              </div>
            )}

            <div className="chat-log" ref={listRef}>
              {messages.length === 0 ? (
                <div className="chat-empty">
                  {provider.isReal
                    ? '请下达业务指令，Jarvis 将为您实时分析。'
                    : '当前未配置真实模型：这里的回复是模拟生成的，不应作为业务结论。'}
                </div>
              ) : (
                messages.map((m, i) => (
                  <div key={i} className={`chat-row ${m.role}`}>
                    <div className="chat-avatar">{m.role === 'user' ? '我' : 'J'}</div>
                    <div className="chat-bubble">
                      {m.content || (m.streaming ? <span className="cursor" /> : '…')}
                      {m.error && (
                        <div className="chat-error">
                          <div className="chat-error-text">{m.error.text}</div>
                          <details className="chat-error-raw">
                            <summary>详情</summary>
                            <pre>{m.error.raw}</pre>
                          </details>
                        </div>
                      )}
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
                placeholder={
                  provider.isReal
                    ? '请输入您的业务指令...'
                    : '未配置真实模型：当前发送将得到模拟回复'
                }
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
              title="切换会话（按首条消息命名）"
            >
              {sessions.map((id) => (
                <option key={id} value={id}>
                  {sessionLabel(id)}
                </option>
              ))}
            </select>
            <button className="sess-btn" onClick={newSession} title="新会话">
              ＋新会话
            </button>
            <button className="sess-btn danger" onClick={() => removeSession(sessionId)} title="删除会话">
              删除
            </button>
            <span className="sess-user">
              <span className="sess-user-label">身份</span>
              <input
                className="sess-user-input"
                value={userId}
                onChange={(e) => setUserIdState(e.target.value)}
                onBlur={(e) => setUserId(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') setUserId((e.target as HTMLInputElement).value)
                }}
                placeholder="未声明（按会话记画像）"
                title="声明用户 ID 后，画像跨会话共享（主体 user-{id}）"
              />
            </span>
          </div>
        </div>
      </div>
    </section>
  )
}
