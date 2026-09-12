import { useCallback, useEffect, useState } from 'react'
import './App.css'
import { Sidebar, TopBar } from './components/chrome'
import { ChatPanel } from './components/ChatPanel'
import {
  ApprovalCenter,
  type ApprovalActions,
  type ApprovalUiState,
} from './components/ApprovalCenter'
import {
  type ApprovalGrant,
  type EnforcementSnapshot,
  PolicyAuthError,
  describeToken,
  getToken,
  isBuiltinMachinePrincipal,
  issueApproval,
  listApprovals,
  revokeApproval,
  setToken,
} from './lib/policyClient'
import {
  Activity,
  BizReality,
  KeyMetrics,
  MarketFocus,
  OurGoal,
  Pipeline,
  SystemHealth,
  TaskCenter,
  TodayBrief,
  UserProfileCard,
} from './components/panels'

/** 真实遥测（后端 /v1/dashboard/* 聚合的真实运行数据）。 */
export interface Telemetry {
  ready: boolean
  uptime: number
  providerType: string
  providerModel: string
  sessions: number
  sessionDetail: { id: string; turns: number }[]
  auditTotal: number
  activity: { type: string; outcome: string; principal: string; timestamp: number }[]
}

/** 未认证时的诚实初值 —— 绝不假装"安全运行"。 */
const EMPTY_STATE: ApprovalUiState = {
  token: '',
  authed: false,
  snap: null,
  grants: [],
  busy: false,
  error: '',
  machineIdentity: '',
}

function App() {
  // 真实遥测（/v1/dashboard/summary + /v1/dashboard/activity）—— NO-FAKE：只接真实接口
  const [gwReady, setGwReady] = useState<boolean | null>(null)
  const [gwUptime, setGwUptime] = useState<number>(-1)
  const [liveSessions, setLiveSessions] = useState(0)
  const [provider, setProvider] = useState<{ type: string; model: string }>({
    type: '—',
    model: '—',
  })
  const [auditTotal, setAuditTotal] = useState(0)
  const [activity, setActivity] = useState<Telemetry['activity']>([])
  const [sessionDetail, setSessionDetail] = useState<Telemetry['sessionDetail']>([])
  const [activeNav, setActiveNav] = useState('dashboard')

  // Policy 审批真实状态 —— 由 App 单一持有，因为侧栏与审批面板显示的是同一份真相。
  const [acl, setAcl] = useState<ApprovalUiState>(EMPTY_STATE)

  const loadPolicy = useCallback(async (token: string) => {
    if (!token) {
      setAcl({ ...EMPTY_STATE })
      return
    }
    const info = describeToken(token)
    const subject = info?.subject ?? ''
    setAcl((prev) => ({ ...prev, token, busy: true, error: '' }))
    try {
      // 一次 list 同时拿回凭据与拦截配置（后端在响应里带了 enforcement）。
      const data = await listApprovals(token, { includeRevoked: false })
      setAcl({
        token,
        authed: true,
        snap: data.enforcement as EnforcementSnapshot,
        grants: data.grants as ApprovalGrant[],
        busy: false,
        error: '',
        machineIdentity: isBuiltinMachinePrincipal(subject) ? subject : '',
      })
    } catch (e) {
      const msg =
        e instanceof PolicyAuthError
          ? e.message
          : e instanceof Error
            ? e.message
            : '读取失败'
      setAcl({
        token,
        authed: false,
        snap: null,
        grants: [],
        busy: false,
        error: msg,
        machineIdentity: '',
      })
    }
  }, [])

  useEffect(() => {
    // 注意：刻意在无令牌时**不**进 loadPolicy —— 初始 state 就是诚实的
    // EMPTY_STATE，挂载时无需 setState（清空令牌走 onTokenChange 事件路径）。
    const stored = getToken()
    if (stored) void loadPolicy(stored)
  }, [loadPolicy])

  const onTokenChange = useCallback(
    (token: string) => {
      setToken(token)
      void loadPolicy(token)
    },
    [loadPolicy],
  )

  const onIssue = useCallback(
    async (action: string, reason: string, ttlSeconds: number) => {
      setAcl((prev) => ({ ...prev, busy: true, error: '' }))
      try {
        await issueApproval(acl.token, [action], reason, ttlSeconds)
      } catch (e) {
        setAcl((prev) => ({
          ...prev,
          busy: false,
          error: e instanceof Error ? e.message : '签发失败',
        }))
        return
      }
      await loadPolicy(acl.token)
    },
    [acl.token, loadPolicy],
  )

  const onRevoke = useCallback(
    async (grantId: string) => {
      setAcl((prev) => ({ ...prev, busy: true, error: '' }))
      try {
        await revokeApproval(acl.token, grantId, '')
      } catch (e) {
        setAcl((prev) => ({
          ...prev,
          busy: false,
          error: e instanceof Error ? e.message : '撤销失败',
        }))
        return
      }
      await loadPolicy(acl.token)
    },
    [acl.token, loadPolicy],
  )

  const aclActions: ApprovalActions = {
    onTokenChange,
    onRefresh: () => void loadPolicy(acl.token),
    onIssue,
    onRevoke,
  }

  useEffect(() => {
    let alive = true
    async function poll() {
      try {
        const r = await fetch('/v1/ready', { cache: 'no-store' })
        const d = (await r.json()) as { status?: string }
        if (alive) setGwReady(d.status === 'ready')
      } catch {
        if (alive) setGwReady(false)
      }
      // 驾驶舱遥测（真实：provider / 会话 / 审计 / 动态）
      try {
        const s = await fetch('/v1/dashboard/summary', { cache: 'no-store' })
        const d = (await s.json()) as {
          uptime_seconds?: number
          provider?: { type?: string; model?: string }
          sessions?: { count?: number; detail?: { id: string; turns: number }[] }
          audit?: { total_events?: number }
        }
        if (alive) {
          if (typeof d.uptime_seconds === 'number') setGwUptime(d.uptime_seconds)
          if (d.provider) {
            setProvider({ type: d.provider.type ?? '—', model: d.provider.model ?? '—' })
          }
          setLiveSessions(d.sessions?.count ?? 0)
          setSessionDetail(d.sessions?.detail ?? [])
          setAuditTotal(d.audit?.total_events ?? 0)
        }
      } catch {
        // 保持上次值
      }
      try {
        const a = await fetch('/v1/dashboard/activity?limit=6', { cache: 'no-store' })
        const d = (await a.json()) as { activity?: Telemetry['activity'] }
        if (alive && d.activity) setActivity(d.activity)
      } catch {
        // 保持上次值
      }
    }
    poll()
    const t = setInterval(poll, 15000)
    return () => {
      alive = false
      clearInterval(t)
    }
  }, [])

  const dashboard = (
    <div className="layout">
      {/* 左列：今日简报 + 市场 */}
      <div className="col col-left">
        <TodayBrief />
        <MarketFocus />
      </div>

      {/* 中列：JARVIS + Pipeline + 业务数据 + 任务 */}
      <div className="col col-center">
        <ChatPanel />
        <Pipeline />
        <BizReality />
        <TaskCenter sessions={sessionDetail} />
      </div>

      {/* 右列：核心指标 + 系统状态 + 动态 + 目标 */}
      <div className="col col-right">
        <KeyMetrics auditTotal={auditTotal} sessions={liveSessions} />
        <SystemHealth
          ready={gwReady}
          liveSessions={liveSessions}
          uptime={gwUptime}
          provider={provider}
          auditTotal={auditTotal}
        />
        <UserProfileCard defaultPrincipal="default" />
        <Activity items={activity} />
        <OurGoal />
      </div>
    </div>
  )

  return (
    <div className="app">
      <TopBar />
      <div className="shell">
        <Sidebar active={activeNav} onSelect={setActiveNav} approval={acl} />
        <main className="main">
          {activeNav === 'approval' ? (
            <div className="layout layout-single">
              <div className="col col-wide">
                <ApprovalCenter state={acl} actions={aclActions} />
              </div>
            </div>
          ) : (
            dashboard
          )}
        </main>
      </div>
    </div>
  )
}

export default App
