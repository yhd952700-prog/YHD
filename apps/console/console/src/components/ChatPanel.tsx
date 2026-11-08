import { useCallback, useEffect, useRef, useState } from 'react'
import {
  chatFailureText,
  deleteSession,
  friendlyChatError,
  getLocalSessions,
  getSelectedModelId,
  getSessionId,
  getSessionMeta,
  getUserId,
  listSessions,
  loadHistory,
  makeSessionTitle,
  newSessionId,
  saveLocalSessions,
  saveSessionMeta,
  searchSessions,
  setSelectedModelId,
  setSessionId,
  setUserId,
  streamChat,
  titleFromHistory,
} from '../lib/chatClient'
import type { SessionMeta, SessionSearchResult } from '../lib/chatClient'
import { useApi } from '../lib/api'
import { relativeTime } from '../lib/format'
import type { ActiveProviderStatus } from '../lib/providerStatus'
import { BudgetDenialActions } from './ui'

/** 对话页只需要模型列表里可展示/可选的最小字段。 */
interface ChatModel {
  id: string
  name: string
  model: string
  enabled: boolean
  is_default: boolean
}

interface ChatModelsResponse {
  models: ChatModel[]
  default_id: string | null
}

interface ToolCall {
  name: string
  args: Record<string, unknown>
  /** 后端已改为结构化 JSON（dict/list/scalar）；旧记录仍是字符串。 */
  output: unknown
}

/** 工具结果转可渲染文本：结构化输出按 JSON 展示，旧字符串原样保留。 */
function toolOutputText(output: unknown): string {
  if (typeof output === 'string') return output
  if (output === undefined || output === null) return ''
  try {
    return JSON.stringify(output, null, 2)
  } catch {
    return String(output)
  }
}

/** 工具结果是否失败/需澄清：失败时摘要用人话，原始 JSON 仍收在折叠区里。 */
function toolOutcome(output: unknown): { failed: boolean; message: string } {
  let parsed: unknown = output
  if (typeof output === 'string' && /^\s*[{[]/.test(output)) {
    try {
      parsed = JSON.parse(output)
    } catch {
      return { failed: false, message: '' }
    }
  }
  if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
    const record = parsed as Record<string, unknown>
    const status = String(record.status ?? '').toLowerCase()
    const question = typeof record.question === 'string' ? record.question.trim() : ''
    if (status === 'needs_clarification' || question) {
      return { failed: true, message: question || '需要补充信息' }
    }
    const failed =
      status === 'error' ||
      status === 'failed' ||
      status === 'failure' ||
      record.success === false
    if (failed) {
      const reason =
        (typeof record.error === 'string' && record.error) ||
        (typeof record.detail === 'string' && record.detail) ||
        (typeof record.message === 'string' && record.message) ||
        ''
      return { failed: true, message: reason || '工具没有返回可用结果' }
    }
  }
  return { failed: false, message: '' }
}

interface Message {
  role: 'user' | 'assistant'
  content: string
  streaming?: boolean
  /** 流式期间的状态行（后端 notice / 20 秒慢提示）：不进入正文、不计 token。 */
  notice?: string
  tools?: ToolCall[]
  /** 失败时的人话 + 原始错误（原始文本默认折叠）。 */
  error?: { text: string; raw: string; action?: 'budget' }
}

/** 首字迟迟不来时的非阻断提示（只更新状态行，不打断输入）。 */
const SLOW_RESPONSE_NOTICE_MS = 20_000
const SLOW_RESPONSE_NOTICE = '模型响应较慢，仍在等待…'

/** 快捷指令 chips（点击填入输入框）。 */
const QUICK = ['市场分析', '销售机会', '客户策略', 'SEO优化', '数据分析']

/** 与后端 ChatRequest.message 的 max_length=8000 保持一致。 */
const MAX_MESSAGE_CHARS = 8000
/** 接近上限时才显示字数，避免日常输入被计数器打扰。 */
const NEAR_LIMIT_CHARS = 6000
/** 会话列表每页条数：1000 个会话也不一次拉全（首屏只取 50）。 */
const SESSION_PAGE_SIZE = 50
/** 单会话历史每页消息数（"加载更早的消息"每次再取 200）。 */
const HISTORY_PAGE_SIZE = 200

interface ChatPanelProps {
  /** 模型状态唯一来源（/v1/health → lib/providerStatus）。 */
  provider: ActiveProviderStatus
  /** "去配置模型"：跳到模型管理。 */
  onConfigureModel: () => void
  /** "去用量页调额度"：402 预算拒绝时的可点出口。 */
  onOpenUsage?: () => void
}

export function ChatPanel({ provider, onConfigureModel, onOpenUsage }: ChatPanelProps) {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [inputError, setInputError] = useState('')
  const [sending, setSending] = useState(false)
  const [sessionId, setSessionIdState] = useState(() => getSessionId())
  // 用户身份：声明后画像跨会话共享（未声明则后端按会话主体记画像）。
  const [userId, setUserIdState] = useState(() => getUserId())
  // 对话模型选择：空串 = 跟随后端当前默认模型。模型列表来自模型管理页保存的数据，
  // 因此"保存即出现在这里"，删除后也会自动回落，不需要重启后端。
  const [modelId, setModelIdState] = useState(() => getSelectedModelId())
  const models = useApi<ChatModelsResponse>('/v1/models', 15000)
  const availableModels = (models.data?.models ?? []).filter((m) => m.enabled)
  const defaultModel =
    availableModels.find((m) => m.is_default) ?? availableModels[0] ?? null
  const [sessions, setSessions] = useState<string[]>(() => {
    const cur = getSessionId()
    const local = getLocalSessions()
    return local.includes(cur) ? local : [cur, ...local]
  })
  // 会话标题：首条用户消息前 16 字 + 更新时间。绝不再展示截断的 session ID。
  const [meta, setMeta] = useState<Record<string, SessionMeta>>(() => getSessionMeta())
  // 分页状态：会话列表只取一页（"加载更多"续取），历史只取最近一页（"加载更早"续取）。
  const [sessionsTotal, setSessionsTotal] = useState(0)
  const [sessionsHasMore, setSessionsHasMore] = useState(false)
  const [remoteOffset, setRemoteOffset] = useState(0)
  const [loadingMoreSessions, setLoadingMoreSessions] = useState(false)
  const [historyHasOlder, setHistoryHasOlder] = useState(false)
  const [loadingOlder, setLoadingOlder] = useState(false)
  // 历史搜索（FTS5 索引；索引建立中/降级为扫描时界面必须如实提示）
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResult, setSearchResult] = useState<SessionSearchResult | null>(null)
  const [searchBusy, setSearchBusy] = useState(false)
  const [searchError, setSearchError] = useState('')
  const searchRetryRef = useRef(0)
  const listRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const inputChars = input.length
  const inputTooLong = inputChars > MAX_MESSAGE_CHARS

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

  /** 切回本页时刷新模型列表：在设置里保存/删除后回来立刻可见。 */
  const reloadModels = models.reload
  useEffect(() => {
    const onFocus = () => reloadModels()
    window.addEventListener('focus', onFocus)
    return () => window.removeEventListener('focus', onFocus)
  }, [reloadModels])

  /** 选中的模型被删除/禁用后自动回到"跟随默认模型"，避免继续发一个不存在的 id。 */
  useEffect(() => {
    if (!models.data) return
    const enabled = (models.data.models ?? []).filter((m) => m.enabled)
    if (modelId && !enabled.some((m) => m.id === modelId)) {
      setModelIdState('')
      setSelectedModelId('')
    }
  }, [models.data, modelId])

  const chooseModel = useCallback((id: string) => {
    setModelIdState(id)
    setSelectedModelId(id)
  }, [])

  // 首次进入把当前会话的历史接回来：否则刷新后对话记录"看起来消失了"，
  // 下拉框也只能显示"未命名会话"。只取一次，且不覆盖用户已开始输入的内容。
  // 只取最近一页（默认 200 条），500 轮的长会话也不整段拉；更早的用按钮续取。
  useEffect(() => {
    let alive = true
    void (async () => {
      try {
        const data = await loadHistory(sessionId, userId, { limit: HISTORY_PAGE_SIZE })
        if (!alive) return
        const historyMessages = data.history.map((h) => ({
          role: h.role === 'user' ? ('user' as const) : ('assistant' as const),
          content: h.content,
        }))
        setMessages((prev) => (prev.length === 0 ? historyMessages : prev))
        setHistoryHasOlder(Boolean(data.has_more))
        // 只有整段历史都在这一页时，首条用户消息才是真正的"首条"，才可作标题。
        const title = data.has_more ? '' : titleFromHistory(data.history)
        if (title) {
          setMeta((prev) => {
            if (prev[sessionId]?.title) return prev
            const next = { ...prev, [sessionId]: { title, updatedAt: prev[sessionId]?.updatedAt ?? 0 } }
            saveSessionMeta(next)
            return next
          })
        }
        // 落在最新一页的底部：刷新后位置合理。
        setTimeout(scrollToBottom, 0)
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

  /** 续取更早的消息（offset 从最近一条往前数 = 当前已渲染条数）。 */
  const loadOlderMessages = useCallback(async () => {
    if (loadingOlder || sending) return
    const node = listRef.current
    const prevHeight = node?.scrollHeight ?? 0
    const prevTop = node?.scrollTop ?? 0
    setLoadingOlder(true)
    try {
      const data = await loadHistory(sessionId, userId, {
        limit: HISTORY_PAGE_SIZE,
        offset: messages.length,
      })
      setMessages((prev) => [
        ...data.history.map((h) => ({
          role: h.role === 'user' ? ('user' as const) : ('assistant' as const),
          content: h.content,
        })),
        ...prev,
      ])
      setHistoryHasOlder(Boolean(data.has_more))
      // 保持视口停在原来读到的那一句，而不是跳到顶部。
      requestAnimationFrame(() => {
        const box = listRef.current
        if (box) box.scrollTop = box.scrollHeight - prevHeight + prevTop
      })
    } catch {
      // 加载更早失败：保留现状，用户可再点一次。
    } finally {
      setLoadingOlder(false)
    }
  }, [loadingOlder, sending, sessionId, userId, messages.length])

  // 换机/备份迁移后 localStorage 里没有会话清单，但后端已有历史会话：只用本地
  // 清单会让「工作台」看不到导入前的对话。这里按后端列表补全，并给还没有标题
  // 的会话回填首条用户消息作为标题（限 20 个，避免首屏 N+1 拖慢）。
  useEffect(() => {
    let alive = true
    void (async () => {
      let page: Awaited<ReturnType<typeof listSessions>>
      try {
        // 只取第一页：1000 个会话时首屏也不拉全量。
        page = await listSessions(SESSION_PAGE_SIZE, 0)
      } catch {
        // 未登录/离线：保留本地清单，不阻断对话
        return
      }
      if (!alive) return
      const remote = page.sessions
      setSessionsTotal(page.total)
      setSessionsHasMore(page.has_more)
      setRemoteOffset(remote.length)
      if (remote.length === 0) return
      setSessions((prev) => {
        const next = [...prev, ...remote.filter((id) => !prev.includes(id))]
        saveLocalSessions(next)
        return next
      })
      await Promise.all(
        remote.slice(0, 20).map(async (id) => {
          try {
            const data = await loadHistory(id, userId)
            const title = titleFromHistory(data.history)
            if (!title || !alive) return
            setMeta((prev) => {
              if (prev[id]?.title) return prev
              const next = { ...prev, [id]: { title, updatedAt: prev[id]?.updatedAt ?? 0 } }
              saveSessionMeta(next)
              return next
            })
          } catch {
            // 单个会话读失败不影响其余会话被发现
          }
        }),
      )
    })()
    return () => {
      alive = false
    }
    // 只在挂载时发现一次；后续清单由本地操作维护。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const switchSession = useCallback(
    async (id: string) => {
      if (id === sessionId || sending) return
      setSessionIdState(id)
      setSessionId(id)
      setMessages([])
      setHistoryHasOlder(false)
      try {
        const data = await loadHistory(id, userId, { limit: HISTORY_PAGE_SIZE })
        setMessages(
          data.history.map((h) => ({
            role: h.role === 'user' ? 'user' : ('assistant' as const),
            content: h.content,
          })),
        )
        setHistoryHasOlder(Boolean(data.has_more))
        // 顶替到最新一页底部（与刷新后的落点一致）。
        setTimeout(scrollToBottom, 0)
        const title = data.has_more ? '' : titleFromHistory(data.history)
        if (title) recordMeta(id, { title })
      } catch {
        setMessages([])
      }
    },
    [sessionId, sending, userId, recordMeta, scrollToBottom],
  )

  const newSession = useCallback(() => {
    const id = newSessionId()
    setSessionIdState(id)
    setSessionId(id)
    setMessages([])
    setHistoryHasOlder(false)
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

  /** 续取下一页会话（首屏只加载 50 条）。 */
  const loadMoreSessions = useCallback(async () => {
    if (loadingMoreSessions || !sessionsHasMore) return
    setLoadingMoreSessions(true)
    try {
      const page = await listSessions(SESSION_PAGE_SIZE, remoteOffset)
      setSessionsTotal(page.total)
      setSessionsHasMore(page.has_more)
      setRemoteOffset((prev) => prev + page.sessions.length)
      setSessions((prev) => {
        const next = [...prev, ...page.sessions.filter((id) => !prev.includes(id))]
        saveLocalSessions(next)
        return next
      })
    } catch {
      // 失败保留现状，按钮可再点
    } finally {
      setLoadingMoreSessions(false)
    }
  }, [loadingMoreSessions, sessionsHasMore, remoteOffset])

  /** 执行一次历史搜索（结果里带 ready/degraded，界面据此如实提示）。 */
  const runSearch = useCallback(async (query: string) => {
    const value = query.trim()
    if (!value) {
      setSearchResult(null)
      setSearchError('')
      setSearchBusy(false)
      return
    }
    setSearchBusy(true)
    setSearchError('')
    try {
      setSearchResult(await searchSessions(value, 20, 0))
    } catch {
      setSearchError('搜索失败，请稍后重试。')
    } finally {
      setSearchBusy(false)
    }
  }, [])

  // 输入 300ms 防抖后再查，避免每个按键都打一次接口。
  useEffect(() => {
    const value = searchQuery.trim()
    searchRetryRef.current = 0
    if (!value) {
      setSearchResult(null)
      setSearchError('')
      setSearchBusy(false)
      return
    }
    const timer = window.setTimeout(() => {
      void runSearch(value)
    }, 300)
    return () => window.clearTimeout(timer)
  }, [searchQuery, runSearch])

  // 索引还在建立（ready=false）时每 2 秒自动重试一次，最多 15 次（约 30 秒）；
  // 之后保留"索引建立中 x%"的提示，让用户知道结果为何不全，而不是空列表。
  useEffect(() => {
    if (!searchResult || searchResult.ready || !searchQuery.trim()) return
    if (searchRetryRef.current >= 15) return
    const timer = window.setTimeout(() => {
      searchRetryRef.current += 1
      void runSearch(searchQuery.trim())
    }, 2000)
    return () => window.clearTimeout(timer)
  }, [searchResult, searchQuery, runSearch])


  const send = useCallback(
    async (text: string) => {
      const value = (text ?? input).trim()
      if (!value || sending) return
      if (value.length > MAX_MESSAGE_CHARS) {
        setInputError(
          `消息过长：当前 ${value.length} 字，单条上限 ${MAX_MESSAGE_CHARS} 字。请精简或分段发送。`,
        )
        return
      }
      setInputError('')
      setInput('')
      // 焦点留在输入框：连续对话可以直接说下一句，不必再从侧栏 Tab 回来。
      inputRef.current?.focus()
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
      // 20 秒还没有首个 token 时给一条非阻断提示：只更新状态行，输入框照常可用。
      let slowTimer: ReturnType<typeof setTimeout> | null = setTimeout(() => {
        setMessages((prev) => {
          const next = [...prev]
          const last = next[next.length - 1]
          if (!last || !last.streaming || last.content || last.notice) return prev
          next[next.length - 1] = { ...last, notice: SLOW_RESPONSE_NOTICE }
          return next
        })
      }, SLOW_RESPONSE_NOTICE_MS)
      const clearSlowTimer = () => {
        if (slowTimer) {
          clearTimeout(slowTimer)
          slowTimer = null
        }
      }
      try {
        for await (const evt of streamChat(
          value,
          sessionId,
          userId || undefined,
          modelId || undefined,
        )) {
          if (evt.type === 'token') {
            clearSlowTimer()
            setMessages((prev) => {
              const next = [...prev]
              const last = next[next.length - 1]
              next[next.length - 1] = {
                ...last,
                content: last.content + evt.content,
                notice: undefined,
              }
              return next
            })
            scrollToBottom()
          } else if (evt.type === 'notice') {
            // 后端「正在重试」等提示只更新状态行：不追加正文、不改变 streaming、
            // 也不计入 token。
            clearSlowTimer()
            if (evt.message) {
              setMessages((prev) => {
                const next = [...prev]
                next[next.length - 1] = { ...next[next.length - 1], notice: evt.message }
                return next
              })
            }
          } else if (evt.type === 'error') {
            // 错误帧之后后端必补 done；最终由 done 渲染错误卡，这里只清慢提示。
            clearSlowTimer()
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
          } else if (evt.type === 'done') {
            // HTTP 200 + done{status:error} 也是一轮失败：必须渲染失败卡片，
            // 不能把上游的失败文案当成普通回复。
            if (evt.status && evt.status !== 'completed') {
              setMessages((prev) => {
                const next = [...prev]
                const last = next[next.length - 1]
                // 后端失败时会把中性失败文案当 token 发出来；按 done.error
                // 精确剔除，避免它同时以“正常回复”的样子出现。
                let content = last.content
                const failure = (evt.error ?? '').trim()
                if (failure) {
                  const trimmed = content.trim()
                  if (trimmed === failure) content = ''
                  else if (trimmed.endsWith(failure)) {
                    const cut = content.trimEnd()
                    content = cut.slice(0, cut.length - failure.length).trimEnd()
                  }
                }
                next[next.length - 1] = {
                  ...last,
                  content,
                  streaming: false,
                  notice: undefined,
                  error: {
                    text: chatFailureText(evt.reason, evt.error),
                    raw: evt.error ?? evt.status,
                    action: evt.reason === 'budget_exceeded' ? 'budget' : undefined,
                  },
                }
                return next
              })
            }
          }
        }
        clearSlowTimer()
        setMessages((prev) => {
          const next = [...prev]
          const last = next[next.length - 1]
          next[next.length - 1] = { ...last, streaming: false, notice: undefined }
          return next
        })
        recordMeta(sessionId, { updatedAt: Date.now() / 1000 })
      } catch (err) {
        // 原始错误（可能是整段后端 JSON）只进"详情"，正文给可行动的中文。
        clearSlowTimer()
        setMessages((prev) => {
          const next = [...prev]
          const last = next[next.length - 1]
          next[next.length - 1] = {
            ...last,
            streaming: false,
            notice: undefined,
            error: friendlyChatError(err),
          }
          return next
        })
      } finally {
        clearSlowTimer()
        setSending(false)
        scrollToBottom()
      }
    },
    [input, sending, sessionId, userId, modelId, scrollToBottom, recordMeta, meta],
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
              <div className="holo-human">你</div>
            </div>
          </div>

          <div className="jarvis-intro">
            <div className="jarvis-name">鎏灏管家</div>
            <div className="jarvis-sub">你的 AI 业务伙伴</div>
          </div>
        </div>

        <div className="jarvis-right">
          <div className="jarvis-greeting">
            <span className="greet-main">你好，我已准备就绪。</span>
            <span className="greet-sub">今天想让我分析什么？</span>
          </div>

          <div className="jarvis-chat">
            {/* 历史搜索：结果来自 /v1/sessions/search（FTS5 索引）。
                索引建立中/降级扫描时必须把 notice 原样展示给用户。 */}
            {searchQuery.trim() && (
              <div className="chat-search-panel" role="status" aria-live="polite">
                {searchError ? (
                  <div className="chat-search-note">{searchError}</div>
                ) : searchBusy && !searchResult ? (
                  <div className="chat-search-note">搜索中…</div>
                ) : searchResult ? (
                  <>
                    <div className="chat-search-head">
                      {searchResult.ready
                        ? `找到 ${searchResult.total} 条匹配消息`
                        : `索引建立中（${searchResult.index_total ? Math.floor((searchResult.indexed * 100) / searchResult.index_total) : 0}%），结果可能不完整`}
                      {searchResult.degraded && (
                        <span className="chat-search-tag">搜索变慢</span>
                      )}
                      <button
                        className="sess-btn"
                        onClick={() => setSearchQuery('')}
                        title="关闭搜索结果"
                      >
                        关闭
                      </button>
                    </div>
                    {searchResult.notice && (
                      <div className="chat-search-note">{searchResult.notice}</div>
                    )}
                    {searchResult.results.length === 0 ? (
                      <div className="chat-search-note">
                        {searchBusy ? '搜索中…' : '没有匹配的消息'}
                      </div>
                    ) : (
                      <div className="chat-search-list">
                        {searchResult.results.map((hit) => (
                          <button
                            key={`${hit.session_id}-${hit.turn}-${hit.role}`}
                            className="chat-search-hit"
                            onClick={() => {
                              void switchSession(hit.session_id)
                              setSearchQuery('')
                            }}
                            title="跳到该会话"
                          >
                            <span className="chat-search-title">
                              {sessionLabel(hit.session_id)}
                              <span className="chat-search-turn">第 {hit.turn} 轮</span>
                            </span>
                            <span className="chat-search-snippet">{hit.snippet}</span>
                          </button>
                        ))}
                      </div>
                    )}
                  </>
                ) : null}
              </div>
            )}
            {/* 模型状态：未配置真实模型时必须在输入框上方明示，而不是让用户
                以为对面是一个真实模型。判据来自 /v1/health（单一事实源）。 */}
            {showModelWarning && (
              <div className="chat-model-banner" role="status">
                <span className="chat-model-badge">模拟回复</span>
                <span className="chat-model-detail">
                  {provider.label}。{provider.detail}
                  <br />
                  可以先跳过，不影响浏览；之后在「系统设置 → 模型管理」里补上 Key 即可。
                </span>
                <button className="chat-model-action" onClick={onConfigureModel}>
                  去配置模型
                </button>
              </div>
            )}

            {/* role="log" + aria-live：新回复追加时读屏会播报；aria-busy 在流式
                生成期间为 true，让读屏等整段结束再念，避免逐字刷屏。 */}
            <div
              className="chat-log"
              ref={listRef}
              role="log"
              aria-live="polite"
              aria-relevant="additions text"
              aria-busy={sending || undefined}
            >
              {historyHasOlder && (
                <div className="chat-older-row">
                  <button
                    className="sess-btn"
                    onClick={() => void loadOlderMessages()}
                    disabled={loadingOlder || sending}
                    title="从持久化历史里继续往前取（不一次拉 500 轮）"
                  >
                    {loadingOlder ? '加载中…' : '加载更早的消息'}
                  </button>
                </div>
              )}
              {messages.length === 0 ? (
                <div className="chat-empty">
                  {provider.isReal
                    ? '请下达业务指令，鎏灏管家会实时分析。'
                    : '当前未配置真实模型：这里的回复是模拟生成的，不应作为业务结论。'}
                </div>
              ) : (
                messages.map((m, i) => (
                  <div
                    key={i}
                    className={`chat-row ${m.role}`}
                    data-streaming={m.streaming ? 'true' : undefined}
                  >
                    <div className="chat-avatar">{m.role === 'user' ? '我' : '灏'}</div>
                    <div className="chat-bubble">
                      {/* 建流前被拒（402/403）时正文为空：此时只显示下面的错误卡，
                          不能再落一个「…」空气泡，否则用户以为助手回了半个空回复。 */}
                      {m.content ||
                        (m.streaming ? (
                          <span className="cursor" />
                        ) : m.error ? null : (
                          '…'
                        ))}
                      {m.error && (
                        <div className="chat-error">
                          <div className="chat-error-text">{m.error.text}</div>
                          {m.error.action === 'budget' && onOpenUsage && (
                            <BudgetDenialActions
                              onOpenUsage={onOpenUsage}
                              onConfigureModel={onConfigureModel}
                            />
                          )}
                          <div className="chat-error-text">
                            如果一直失败，请确认启动窗口仍在运行，然后重试。
                          </div>
                        </div>
                      )}
                      {m.tools && m.tools.length > 0 && (
                        <div className="tool-calls">
                          {m.tools.map((t, j) => {
                            // 失败/需澄清时摘要用人话并默认收起；原始 JSON 不删，
                            // 展开仍能看到（D：只收进折叠区，不做信息裁剪）。
                            const outcome = toolOutcome(t.output)
                            return (
                              <details
                                key={j}
                                className="tool-card"
                                open={!outcome.failed}
                              >
                                <summary className="tool-summary">
                                  {outcome.failed ? (
                                    <>
                                      调用工具 <code>{t.name}</code> 失败：
                                      {outcome.message}
                                    </>
                                  ) : (
                                    <>
                                      调用工具 <code>{t.name}</code>
                                    </>
                                  )}
                                </summary>
                                <pre className="tool-output">{toolOutputText(t.output)}</pre>
                              </details>
                            )
                          })}
                        </div>
                      )}
                      {m.streaming && m.notice && (
                        <div className="chat-status-note" role="status">
                          {m.notice}
                        </div>
                      )}
                      {/* 只在真实流式期间出现；结束即移除，静态卡片不加扫描线。 */}
                      {m.streaming && <div className="sc-stream-frontier" aria-hidden="true" />}
                    </div>
                  </div>
                ))
              )}
            </div>

            <div className="jarvis-input-wrap">
              <textarea
                ref={inputRef}
                value={input}
                aria-label="业务指令输入框"
                onChange={(e) => {
                  setInput(e.target.value)
                  if (inputError) setInputError('')
                }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault()
                    send(input)
                  }
                }}
                placeholder={
                  provider.isReal
                    ? '请输入你的业务指令…'
                    : '未配置真实模型：当前发送将得到模拟回复'
                }
                rows={1}
                /* 用 readOnly 而不是 disabled：disabled 会把焦点从输入框拿走，
                   键盘用户发完一句就得从侧栏重走几十次 Tab 才能继续输入。 */
                readOnly={sending}
              />
              <button
                className="jarvis-send"
                onClick={() => send(input)}
                disabled={sending || !input.trim() || inputTooLong}
              >
                {sending ? '生成中…' : '发送'}
              </button>
            </div>
            {(inputError || inputTooLong || inputChars > NEAR_LIMIT_CHARS) && (
              <div className="jarvis-input-hint" role={inputError || inputTooLong ? 'alert' : undefined}>
                {inputError ||
                  (inputTooLong
                    ? `消息过长：当前 ${inputChars} 字，单条上限 ${MAX_MESSAGE_CHARS} 字，请精简或分段发送。`
                    : `${inputChars} / ${MAX_MESSAGE_CHARS} 字`)}
              </div>
            )}

            <div className="jarvis-chips">
              {QUICK.map((q) => (
                <button key={q} className="chip" onClick={() => send(q)} disabled={sending}>
                  {q}
                </button>
              ))}
            </div>
          </div>

          <div className="jarvis-sessions">
            {/* 对话模型选择：列表来自模型管理页，保存后无需重启即可选到；
                模型被删除/禁用时上面的 effect 会自动回落到默认模型。 */}
            <select
              className="sess-select"
              value={modelId}
              onChange={(e) => chooseModel(e.target.value)}
              title="选择本次对话使用的模型（在「系统设置 → 模型管理」中添加/编辑）"
              aria-label="选择对话模型"
            >
              <option value="">
                默认模型{defaultModel ? `：${defaultModel.name}` : ''}
              </option>
              {availableModels.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name} · {m.model}
                </option>
              ))}
            </select>
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
            {/* 会话列表分页：1000 个会话也只先取 50 条，滚到底/点按钮继续加载。 */}
            {sessionsHasMore && (
              <button
                className="sess-btn"
                onClick={() => void loadMoreSessions()}
                disabled={loadingMoreSessions}
                title={`后端共 ${sessionsTotal} 个会话，已加载 ${sessions.length} 个`}
              >
                {loadingMoreSessions
                  ? '加载中…'
                  : `加载更多会话（${sessions.length}/${sessionsTotal}）`}
              </button>
            )}
            <span className="sess-search">
              <input
                className="sess-search-input"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="搜索历史消息…"
                aria-label="搜索历史消息"
                title="在当前账号的会话历史里搜索（中文可直接搜关键词）"
              />
            </span>
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
                title="声明用户 ID 后，画像跨会话共享（账号 user-{id}）"
              />
            </span>
          </div>
        </div>
      </div>
    </section>
  )
}
