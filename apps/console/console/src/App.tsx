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

// ---------- 类型 ----------
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

// 侧边栏条目
interface NavItem {
  key: string
  label: string
  glyph: string
  badge?: string
}

const NAV_MAIN: NavItem[] = [
  { key: 'dashboard', label: '首页 · 仪表板', glyph: '▪' },
  { key: 'jarvis', label: 'JARVIS 智能助手', glyph: '◉' },
  { key: 'brief', label: 'CEO 简报', glyph: '▤', badge: 'AI' },
  { key: 'tasks', label: '任务中心', glyph: '◆' },
  { key: 'sales', label: '客户与销售', glyph: '▣' },
  { key: 'data', label: '业务数据中心', glyph: '▦' },
  { key: 'market', label: '市场研究', glyph: '◈' },
  { key: 'aihub', label: 'AI 决策中枢', glyph: '⬢', badge: 'AIR' },
  { key: 'approval', label: '审批中心', glyph: '▧', badge: 'I' },
  { key: 'knowledge', label: '知识中心', glyph: '▨' },
  { key: 'system', label: '系统状态', glyph: '℘' },
  { key: 'settings', label: '设置', glyph: '⚙' },
]

/** 会话 ID 缩短显示。 */
function shortId(id: string): string {
  const s = id.startsWith('session-') ? id.slice('session-'.length) : id
  return s.length > 12 ? `${s.slice(0, 12)}…` : s
}

/** 当前时间（HH:MM:SS）。 */
function useClock(): string {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(t)
  }, [])
  const pad = (n: number) => String(n).padStart(2, '0')
  const d = now
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

/** 运行时长（秒 → 天/时/分/秒）。 */
function fmtDuration(total: number): string {
  if (total < 0) return '—'
  const d = Math.floor(total / 86400)
  const h = Math.floor((total % 86400) / 3600)
  const m = Math.floor((total % 3600) / 60)
  if (d > 0) return `${d}天${h}时${m}分`
  if (h > 0) return `${h}时${m}分`
  return `${m}分`
}

function App() {
  // chat 状态（保留原逻辑）
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [sessionId, setSessionIdState] = useState(() => getSessionId())
  const [sessions, setSessions] = useState<string[]>(() => {
    const cur = getSessionId()
    const local = getLocalSessions()
    return local.includes(cur) ? local : [cur, ...local]
  })
  const messagesRef = useRef<HTMLDivElement>(null)
  // 驾驶舱遥测（真实数据源 /v1/ready + /v1/chat/sessions）
  const [gwReady, setGwReady] = useState<boolean | null>(null)
  const [gwUptime, setGwUptime] = useState<number>(-1)
  const [liveSessions, setLiveSessions] = useState(0)
  const [activeNav, setActiveNav] = useState('jarvis')

  useEffect(() => {
    if (messages.length && messagesRef.current) {
      messagesRef.current.scrollTop = messagesRef.current.scrollHeight
    }
  }, [messages])

  // 拉取真实遥测
  useEffect(() => {
    let alive = true
    async function poll() {
      try {
        const r = await fetch('/v1/ready', { cache: 'no-store' })
        const d = (await r.json()) as { status?: string; timestamp?: number }
        if (alive) {
          setGwReady(d.status === 'ready')
          if (d.timestamp) setGwUptime((Date.now() / 1000 - d.timestamp) | 0)
        }
      } catch {
        if (alive) setGwReady(false)
      }
      try {
        const s = await fetch('/v1/chat/sessions', { cache: 'no-store' })
        const d = (await s.json()) as { count?: number }
        if (alive) setLiveSessions(d.count ?? sessions.length)
      } catch {
        /* 保持上次值 */
      }
    }
    poll()
    const t = setInterval(poll, 15000)
    return () => {
      alive = false
      clearInterval(t)
    }
  }, [sessions.length])

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

  const clock = useClock()

  return (
    <div className="app">
      {/* ===== 顶栏 ===== */}
      <header className="topbar">
        <div className="brand">
          <div className="brand-logo">鎏</div>
          <div className="brand-name">
            <span className="brand-cn">鎏灏 AI</span>
            <span className="brand-en">LIUHAO X · AI OS</span>
          </div>
        </div>

        <div className="topbar-center">
          <div className="cc-title">
            <span className="cc-title-main">CEO COMMAND CENTER</span>
            <span className="cc-title-sub">老板驾驶舱</span>
          </div>
          <div className="cc-tags">
            <span>Intelligent</span> · <span>Autonomous</span> · <span>Global</span>
          </div>
        </div>

        <div className="topbar-meta">
          <span className="meta-time">{clock}</span>
          <span className="meta-weather">26° 深圳</span>
          <span className="meta-user">辛宏达</span>
        </div>
      </header>

      {/* ===== 主体 ===== */}
      <div className="shell">
        {/* 左导航 */}
        <aside className="sidebar">
          <nav>
            {NAV_MAIN.map((n) => (
              <button
                key={n.key}
                className={`nav-item ${activeNav === n.key ? 'active' : ''}`}
                onClick={() => setActiveNav(n.key)}
              >
                <span className="nav-glyph">{n.glyph}</span>
                <span className="nav-label">{n.label}</span>
                {n.badge && <span className="nav-badge">{n.badge}</span>}
              </button>
            ))}
          </nav>

          <div className="sidebar-section">
            <div className="sidebar-sec-title">安全运行模式</div>
            <div className="safe-mode">
              <div className="safe-mode-badge">SAFE MODE</div>
              <ul>
                <li className="ok">Executive Lock <em>LOCKED</em></li>
                <li className="ok">Browser Guard <em>GUARDED</em></li>
                <li className="warn">External Action <em>PENDING APPROVAL</em></li>
              </ul>
            </div>
          </div>

          <div className="sidebar-foot">© 2026 @ LIUHAO X Team</div>
        </aside>

        {/* 主区 */}
        <main className="main">
          {/* 中央 JARVIS 全息区 */}
          <section className="holo-zone">
            <div className="holo-orb">
              <div className="orb-ring" />
              <div className="orb-glow" />
              <div className="orb-face">JARVIS</div>
            </div>
            <div className="holo-aside">
              <div className="holo-greet">老板，我已准备就绪！</div>
              <div className="holo-sub">今天我可以为您分析什么？</div>
              <div className="holo-chips">
                <span className="chip">市场分析</span>
                <span className="chip">竞品报告</span>
                <span className="chip">产品策略</span>
                <span className="chip">财务数据</span>
                <span className="chip">投资分析</span>
                <span className="chip">行业研究</span>
              </div>
            </div>
          </section>

          {/* 对话区（真实 chat） */}
          <section className="chat-panel">
            <div className="chat-head">
              <span className="chat-session">会话 <code>{shortId(sessionId)}</code></span>
              <div className="chat-actions">
                <select
                  className="mini-select"
                  value={sessionId}
                  onChange={(e) => switchSession(e.target.value)}
                  title="切换会话"
                >
                  {sessions.map((id) => (
                    <option key={id} value={id}>{shortId(id)}</option>
                  ))}
                </select>
                <button onClick={newSession} className="mini-btn">+ 新会话</button>
                <button
                  onClick={() => removeSession(sessionId)}
                  className="mini-btn danger"
                  title="删除当前会话"
                >
                  删除
                </button>
              </div>
            </div>

            <div className="chat-messages" ref={messagesRef}>
              {messages.length === 0 ? (
                <div className="chat-empty">
                  <p>开始与 <b>鎏灏</b> 对话</p>
                  <p className="chat-empty-hint">
                    后端：<code>.venv\Scripts\python.exe -m uvicorn src.gateway.main:app --port 8080</code>
                  </p>
                </div>
              ) : (
                messages.map((m, i) => (
                  <div key={i} className={`chat-row ${m.role}`}>
                    <div className="chat-avatar">{m.role === 'user' ? '我' : '鎏'}</div>
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

            <div className="chat-composer">
              <textarea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault()
                    send()
                  }
                }}
                placeholder="输入指令，Enter 发送，Shift+Enter 换行…"
                rows={2}
                disabled={sending}
              />
              <button onClick={send} disabled={sending || !input.trim()}>
                {sending ? '生成中…' : '发送'}
              </button>
            </div>
          </section>

          {/* ===== 驾驶舱面板网格 ===== */}
          <div className="panel-grid">
            {/* 核心业务数据（真实遥测） */}
            <div className="panel kpi-strip">
              <div className="panel-title">核心数据 · Live Telemetry</div>
              <div className="kpi-cards">
                <div className="kpi">
                  <div className="kpi-label">会话数</div>
                  <div className="kpi-value">{liveSessions}</div>
                  <div className="kpi-trend">实时</div>
                </div>
                <div className="kpi">
                  <div className="kpi-label">系统状态</div>
                  <div className="kpi-value">{gwReady === null ? '…' : gwReady ? 'READY' : 'OFF'}</div>
                  <div className="kpi-trend">{gwReady ? '正常' : '离线'}</div>
                </div>
                <div className="kpi">
                  <div className="kpi-label">运行时长</div>
                  <div className="kpi-value">{fmtDuration(gwUptime)}</div>
                  <div className="kpi-trend">网关</div>
                </div>
                <div className="kpi">
                  <div className="kpi-label">当前模型</div>
                  <div className="kpi-value small">mock</div>
                  <div className="kpi-trend">待接入真实模型</div>
                </div>
              </div>
            </div>

            {/* 系统状态（真实） */}
            <div className="panel">
              <div className="panel-title">系统状态 · System Health</div>
              <ul className="status-list">
                <li className="ok">Governance <em>正常</em></li>
                <li className="ok">Audit <em>正常</em></li>
                <li className="ok">Business Reality <em>正常</em></li>
                <li className="ok">Agent Runtime <em>正常</em></li>
                <li className="ok">Provider (网关) <em>{gwReady ? '在线' : '离线'}</em></li>
                <li className="ok">Execution Lock <em>已锁定</em></li>
                <li className="ok">Browser Guard <em>已锁定</em></li>
                <li className="warn">API 服务 (在线) <em>受控</em></li>
              </ul>
            </div>

            {/* CEO 今日简报（占位） */}
            <div className="panel">
              <div className="panel-title">CEO 今日简报 <span className="tag-ai">AI</span></div>
              <div className="placeholder-note">数据待接入</div>
              <ul className="brief-list">
                <li>系统遥测已接入，业务简报待连接数据源</li>
                <li>接入后可生成每日经营摘要 / 风险提示</li>
              </ul>
            </div>

            {/* 销售 Pipeline（占位） */}
            <div className="panel">
              <div className="panel-title">销售 Pipeline <span className="tag-med">看板</span></div>
              <div className="placeholder-note">数据待接入</div>
              <div className="pipeline">
                {['Prospect', 'Qualified', '待开发', '已联系', '提案', '需求确认', '报价', '谈判', '成交'].map(
                  (s) => (
                    <div key={s} className="pipe-col">
                      <div className="pipe-head">{s}</div>
                      <div className="pipe-num">—</div>
                    </div>
                  ),
                )}
              </div>
            </div>

            {/* 业务数据中心（占位） */}
            <div className="panel">
              <div className="panel-title">业务数据中心 · Business Reality</div>
              <div className="placeholder-note">数据待接入</div>
              <div className="biz-reality">
                {['Company', 'Product', 'Supplier', 'Market', 'Customer', 'Pipeline'].map((k) => (
                  <div key={k} className="biz-chip">{k}<em>—</em></div>
                ))}
              </div>
            </div>

            {/* 全球市场聚焦（装饰） */}
            <div className="panel market-panel">
              <div className="panel-title">全球市场聚焦 · Market Focus</div>
              <div className="market-map">
                <div className="globe" />
                <ul className="market-rank">
                  <li><b>Thailand</b><span>—</span><em>92</em></li>
                  <li><b>Vietnam</b><span>—</span><em>85</em></li>
                  <li><b>Malaysia</b><span>—</span><em>82</em></li>
                  <li><b>Indonesia</b><span>—</span><em>80</em></li>
                  <li><b>Philippines</b><span>—</span><em>78</em></li>
                </ul>
              </div>
              <div className="placeholder-note small">评分数据待接入</div>
            </div>

            {/* AI 任务中心（占位） */}
            <div className="panel">
              <div className="panel-title">AI 任务中心 <span className="tags">
                <span className="tag">全部</span><span className="tag">进行中</span><span className="tag">已完成</span><span className="tag">已送达</span>
              </span></div>
              <div className="placeholder-note">数据待接入</div>
              <ul className="task-list">
                <li>任务队列等待接入真实执行流</li>
                <li>从 /v1/chat 会话与工具调用生成任务视图</li>
              </ul>
              <button className="enter-btn">进入任务中心 →</button>
            </div>

            {/* 最近动态（真实会话为线索） */}
            <div className="panel">
              <div className="panel-title">最近动态 · Activity</div>
              <div className="activity">
                <div className="act"><span className="act-time">最新</span> 网关状态 {gwReady ? 'READY' : 'OFF'}（经 /v1/ready）</div>
                <div className="act"><span className="act-time">实时</span> 活跃会话 {liveSessions} 个</div>
                <div className="act"><span className="act-time">近时</span> 对话经由 SSE 流式（/v1/chat/stream）</div>
                <div className="act"><span className="act-time">待接入</span> 业务动态数据源 / 任务完成事件</div>
              </div>
            </div>

            {/* 我们的目标 */}
            <div className="panel goal-panel">
              <div className="panel-title">我们的目标</div>
              <div className="goal">
                <span className="goal-badge">🏆</span>
                <span>Build a Global Brand — 鎏灏 · 成为全球 AI 操作系统</span>
              </div>
            </div>
          </div>
        </main>
      </div>
    </div>
  )
}

export default App
